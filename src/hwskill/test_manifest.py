"""Schema validation and discovery for declarative Skill/Profile test collections."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Literal, TypeAlias

import yaml


_MAX_MANIFEST_BYTES = 64 * 1024


class TestManifestError(ValueError):
    """Raised when a declarative test collection is not schema-safe."""


class _YamlMappingKeyError(yaml.YAMLError):
    """Internal parser error for ambiguous YAML mappings."""


class _ManifestLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _ManifestLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge" or key_node.value == "<<":
            raise _YamlMappingKeyError("YAML merge key '<<' is not allowed")
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise _YamlMappingKeyError("YAML mapping key must be a string")
        if key in mapping:
            raise _YamlMappingKeyError(f"duplicate YAML mapping key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ManifestLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class TestTarget:
    kind: Literal["skill", "profile"]
    target_id: str


@dataclass(frozen=True)
class CommandAction:
    action_id: str
    command: str
    workdir: str | None = None


@dataclass(frozen=True)
class AgentAction:
    action_id: str
    prompt: str
    workdir: str | None = None


TestAction: TypeAlias = CommandAction | AgentAction


@dataclass(frozen=True)
class TestCase:
    case_id: str
    description: str | None
    workdir: str | None
    prepare: TestAction | None
    steps: tuple[TestAction, ...]
    post_check: TestAction


@dataclass(frozen=True)
class FixtureIdentity:
    relative: tuple[str, ...]
    device: int
    inode: int
    mode: int
    size: int = 0
    content_digest: str | None = None


@dataclass(frozen=True)
class FixtureSource:
    """Descriptor-free identity snapshot for fixture copying at execution time."""

    repository: FixtureIdentity
    anchors: tuple[FixtureIdentity, ...]
    entries: tuple[FixtureIdentity, ...]
    missing_component: str | None = None


@dataclass(frozen=True)
class TestCollection:
    manifest_path: Path
    target: TestTarget
    cases: tuple[TestCase, ...]
    fixtures_dir: Path
    fixture_source: FixtureSource | None = None


def load_test_collection(path: Path, repo_root: Path) -> TestCollection:
    """Load one repository-contained collection without following symlinks."""
    root = _repository_root(repo_root)
    manifest_path = _safe_repo_path(path, root, "test manifest")
    try:
        data = yaml.load(_read_anchored_manifest(root, manifest_path), Loader=_ManifestLoader)
    except _YamlMappingKeyError as exc:
        raise TestManifestError(f"{manifest_path}: {exc}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TestManifestError(f"cannot read test manifest {manifest_path}: {exc}") from exc

    root_data = _mapping(data, "test manifest")
    _exact_keys(root_data, {"schema_version", "target", "cases"}, "test manifest")
    if not isinstance(root_data["schema_version"], int) or isinstance(root_data["schema_version"], bool) or root_data["schema_version"] != 1:
        raise TestManifestError("test manifest schema_version must be exactly 1")
    target = _parse_target(root_data["target"])
    _validate_target_path(manifest_path, root, target)
    cases_data = root_data["cases"]
    if not isinstance(cases_data, list):
        raise TestManifestError("test manifest cases must be a list")
    cases = tuple(_parse_case(item, index + 1) for index, item in enumerate(cases_data))
    _validate_unique((case.case_id for case in cases), "case id")
    fixtures_dir = manifest_path.parent / "fixtures"
    _validate_fixtures_dir(fixtures_dir, root)
    fixture_source = _snapshot_fixture_source(root, fixtures_dir)
    return TestCollection(
        manifest_path=manifest_path,
        target=target,
        cases=cases,
        fixtures_dir=fixtures_dir,
        fixture_source=fixture_source,
    )


def _read_anchored_manifest(root: Path, manifest_path: Path) -> str:
    """Read one manifest from a stable no-follow descriptor chain rooted at ``root``."""
    try:
        relative = manifest_path.relative_to(root)
    except ValueError as exc:
        raise TestManifestError(f"test manifest must be inside repository: {manifest_path}") from exc
    if not relative.parts:
        raise TestManifestError(f"test manifest must be a regular file: {manifest_path}")
    if not hasattr(os, "O_NOFOLLOW"):
        raise TestManifestError("test manifest requires O_NOFOLLOW support")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, directory_flags)
    descriptors = [root_fd]
    identities: list[tuple[int, str, int]] = []
    try:
        parent_fd = root_fd
        for component in relative.parts[:-1]:
            child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            descriptors.append(child_fd)
            identities.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        file_name = relative.parts[-1]
        file_fd = os.open(file_name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent_fd)
        descriptors.append(file_fd)
        identities.append((parent_fd, file_name, file_fd))
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise TestManifestError(f"test manifest must be a regular file: {manifest_path}")
        _after_manifest_opened(root, manifest_path)
        _assert_manifest_identities(identities, manifest_path)
        raw = _read_manifest_bytes(file_fd, manifest_path)
        return raw.decode("utf-8")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _after_manifest_opened(_root: Path, _manifest_path: Path) -> None:
    """Deterministic race-test seam after descriptor anchoring and before parsing."""


def _assert_manifest_identities(
    identities: list[tuple[int, str, int]],
    manifest_path: Path,
) -> None:
    for parent_fd, name, anchored_fd in identities:
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise TestManifestError(f"test manifest changed before read: {manifest_path}") from exc
        anchored = os.fstat(anchored_fd)
        if (current.st_dev, current.st_ino) != (anchored.st_dev, anchored.st_ino):
            raise TestManifestError(f"test manifest changed before read: {manifest_path}")
        if stat.S_ISDIR(anchored.st_mode) != stat.S_ISDIR(current.st_mode):
            raise TestManifestError(f"test manifest changed before read: {manifest_path}")
        if stat.S_ISREG(anchored.st_mode) != stat.S_ISREG(current.st_mode):
            raise TestManifestError(f"test manifest changed before read: {manifest_path}")


def _read_manifest_bytes(file_fd: int, manifest_path: Path) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := os.read(file_fd, 64 * 1024):
        size += len(chunk)
        if size > _MAX_MANIFEST_BYTES:
            raise TestManifestError(f"test manifest exceeds maximum size: {manifest_path}")
        chunks.append(chunk)
    return b"".join(chunks)


def _snapshot_fixture_source(root: Path, fixtures_dir: Path) -> FixtureSource:
    """Capture fixture identities without retaining descriptors beyond collection load."""
    relative = fixtures_dir.relative_to(root)
    if not hasattr(os, "O_NOFOLLOW"):
        raise TestManifestError("fixtures require O_NOFOLLOW support")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, directory_flags)
    descriptors = [root_fd]
    try:
        repository = _fixture_identity((), os.fstat(root_fd))
        parent_fd = root_fd
        anchors: list[FixtureIdentity] = []
        for index, component in enumerate(relative.parts):
            try:
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            except FileNotFoundError:
                if index == len(relative.parts) - 1:
                    return FixtureSource(repository, tuple(anchors), (), component)
                raise TestManifestError("fixture source disappeared while loading collection") from None
            descriptors.append(child_fd)
            identity = _fixture_identity(relative.parts[:index + 1], os.fstat(child_fd))
            anchors.append(identity)
            parent_fd = child_fd
        entries: list[FixtureIdentity] = []
        _snapshot_fixture_entries(parent_fd, (), entries)
        return FixtureSource(repository, tuple(anchors), tuple(entries))
    except OSError as exc:
        raise TestManifestError(f"cannot safely snapshot fixtures: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _snapshot_fixture_entries(
    directory_fd: int,
    relative: tuple[str, ...],
    entries: list[FixtureIdentity],
) -> None:
    current = os.fstat(directory_fd)
    if not stat.S_ISDIR(current.st_mode):
        raise TestManifestError("fixtures must be a real directory")
    entries.append(_fixture_identity(relative, current))
    for name in sorted(os.listdir(directory_fd)):
        candidate = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        child_relative = (*relative, name)
        if stat.S_ISLNK(candidate.st_mode):
            raise TestManifestError(f"fixtures must not contain a symlink: {'/'.join(child_relative)}")
        if stat.S_ISDIR(candidate.st_mode):
            child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                _assert_fixture_stat_matches(candidate, opened, child_relative)
                _snapshot_fixture_entries(child_fd, child_relative, entries)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(candidate.st_mode):
            file_fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                opened = os.fstat(file_fd)
                _assert_fixture_stat_matches(candidate, opened, child_relative)
                digest = _fixture_digest(file_fd)
                after = os.fstat(file_fd)
                _assert_fixture_stat_matches(opened, after, child_relative)
                entries.append(_fixture_identity(child_relative, after, digest))
            finally:
                os.close(file_fd)
        else:
            raise TestManifestError(
                f"fixtures contains unsupported fixture {'/'.join(child_relative)} ({_fixture_entry_type(candidate.st_mode)})"
            )


def _fixture_identity(
    relative: tuple[str, ...],
    value: os.stat_result,
    content_digest: str | None = None,
) -> FixtureIdentity:
    return FixtureIdentity(relative, value.st_dev, value.st_ino, value.st_mode, value.st_size, content_digest)


def _fixture_digest(file_fd: int) -> str:
    digest = hashlib.sha256()
    while block := os.read(file_fd, 1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _assert_fixture_stat_matches(
    expected: os.stat_result,
    actual: os.stat_result,
    relative: tuple[str, ...],
) -> None:
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        raise TestManifestError(f"fixture changed while snapshotting: {'/'.join(relative)}")
    if stat.S_IFMT(expected.st_mode) != stat.S_IFMT(actual.st_mode):
        raise TestManifestError(f"fixture changed while snapshotting: {'/'.join(relative)}")


def discover_test_collections(repo_root: Path) -> tuple[TestCollection, ...]:
    """Find every collection under the two canonical test roots in path order."""
    root = _repository_root(repo_root)
    manifests = (
        _discover_manifest_paths(root, Path("tests") / "profiles")
        + _discover_manifest_paths(root, Path("tests") / "skills")
    )
    collections = tuple(load_test_collection(path, root) for path in sorted(manifests))
    seen: set[tuple[str, str]] = set()
    for collection in collections:
        key = (collection.target.kind, collection.target.target_id)
        if key in seen:
            raise TestManifestError(
                f"duplicate target collection: {collection.target.kind} {collection.target.target_id}"
            )
        seen.add(key)
    return collections


def _parse_target(value: Any) -> TestTarget:
    data = _mapping(value, "target")
    _exact_keys(data, {"kind", "id"}, "target")
    kind = data["kind"]
    if not isinstance(kind, str) or kind not in {"skill", "profile"}:
        raise TestManifestError("target.kind must be skill or profile")
    return TestTarget(kind=kind, target_id=_nonempty_string(data["id"], "target.id"))


def _parse_case(value: Any, position: int) -> TestCase:
    data = _mapping(value, f"case {position}")
    _exact_keys(
        data,
        {"id", "description", "workdir", "prepare", "steps", "post_check"},
        f"case {position}",
        optional={"description", "workdir", "prepare"},
    )
    case_id = _nonempty_string(data["id"], f"case {position}.id")
    description = data.get("description")
    if "description" in data and not isinstance(description, str):
        raise TestManifestError(f"case {case_id} description must be a string")
    workdir = _optional_workdir(data, "workdir", f"case {case_id}.workdir")
    prepare = None
    if "prepare" in data:
        prepare = _parse_action(data["prepare"], "prepare", f"case {case_id}.prepare")
    steps_data = data["steps"]
    if not isinstance(steps_data, list) or not steps_data:
        raise TestManifestError(f"case {case_id} steps must be a non-empty list")
    steps = tuple(
        _parse_action(action, f"step-{index}", f"case {case_id}.steps[{index - 1}]")
        for index, action in enumerate(steps_data, start=1)
    )
    post_check = _parse_action(data["post_check"], "post-check", f"case {case_id}.post_check")
    actions = tuple(action for action in (prepare, *steps, post_check) if action is not None)
    _validate_unique((action.action_id for action in actions), f"action id in case {case_id}")
    return TestCase(
        case_id=case_id,
        description=description,
        workdir=workdir,
        prepare=prepare,
        steps=steps,
        post_check=post_check,
    )


def _parse_action(value: Any, default_id: str, location: str) -> TestAction:
    data = _mapping(value, f"action {location}")
    action_type = data.get("type")
    allowed: set[str]
    if not isinstance(action_type, str):
        raise TestManifestError(f"action {location} type must be command or agent")
    if action_type == "command":
        allowed = {"id", "type", "workdir", "command"}
    elif action_type == "agent":
        allowed = {"id", "type", "workdir", "prompt"}
    else:
        raise TestManifestError(f"action {location} type must be command or agent")
    _exact_keys(data, allowed, f"action {location}", optional={"id", "workdir"})
    action_id = _nonempty_string(data["id"], f"action {location}.id") if "id" in data else default_id
    workdir = _optional_workdir(data, "workdir", f"action {location}.workdir")
    if action_type == "command":
        return CommandAction(
            action_id=action_id,
            command=_nonempty_string(data["command"], f"action {location}.command"),
            workdir=workdir,
        )
    return AgentAction(
        action_id=action_id,
        prompt=_nonempty_string(data["prompt"], f"action {location}.prompt"),
        workdir=workdir,
    )


def _optional_workdir(data: dict[str, Any], key: str, location: str) -> str | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value:
        raise TestManifestError(f"{location} must be a non-empty POSIX relative workdir")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise TestManifestError(f"{location} must not escape its workspace")
    normalized = str(path)
    if not normalized or normalized == "/":
        raise TestManifestError(f"{location} must be a non-empty POSIX relative workdir")
    return normalized


def _validate_target_path(manifest_path: Path, root: Path, target: TestTarget) -> None:
    relative = manifest_path.relative_to(root)
    parts = relative.parts
    if target.kind == "skill":
        expected = ("tests", "skills", *target.target_id.split("/"), "test.yaml")
        valid_id = (
            len(target.target_id.split("/")) == 2
            and all(_safe_path_component(part) for part in target.target_id.split("/"))
        )
    else:
        expected = ("tests", "profiles", target.target_id, "test.yaml")
        valid_id = _safe_path_component(target.target_id)
    if not valid_id or parts != expected:
        raise TestManifestError(
            f"test manifest path does not match target {target.kind} {target.target_id}: {relative.as_posix()}"
        )


def _discover_manifest_paths(root: Path, relative_root: Path) -> tuple[Path, ...]:
    directory = _safe_repo_path(root / relative_root, root, "test root", require_exists=False)
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise TestManifestError(f"test root must be a directory: {directory}")
    found: list[Path] = []

    def visit(current: Path) -> None:
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as exc:
            raise TestManifestError(f"cannot scan test root {current}: {exc}") from exc
        for entry in entries:
            entry_path = Path(entry.path)
            if entry.is_symlink():
                raise TestManifestError(f"symlink is not allowed in test discovery: {entry_path}")
            if entry.is_dir(follow_symlinks=False):
                visit(entry_path)
            elif entry.is_file(follow_symlinks=False) and entry.name == "test.yaml":
                found.append(entry_path)

    visit(directory)
    return tuple(found)


def _repository_root(repo_root: Path) -> Path:
    root = Path(os.path.abspath(repo_root))
    _reject_symlink_ancestors(root, "repository root")
    if not root.is_dir():
        raise TestManifestError(f"repository root must be a directory: {root}")
    return root


def _safe_repo_path(path: Path, root: Path, label: str, *, require_exists: bool = True) -> Path:
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise TestManifestError(f"{label} must be inside repository: {candidate}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise TestManifestError(f"{label} must not include a symlink: {current}")
    if require_exists and not candidate.exists():
        raise TestManifestError(f"{label} does not exist: {candidate}")
    return candidate


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise TestManifestError(f"{label} must not include a symlink: {current}")


def _validate_fixtures_dir(fixtures_dir: Path, root: Path) -> None:
    fixtures_dir = _safe_repo_path(fixtures_dir, root, "fixtures", require_exists=False)
    try:
        mode = os.lstat(fixtures_dir).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise TestManifestError(f"cannot inspect fixtures directory {fixtures_dir}: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise TestManifestError(f"fixtures must be a real directory: {fixtures_dir}")

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise TestManifestError(f"cannot scan fixtures directory {directory}: {exc}") from exc
        for entry in entries:
            entry_path = Path(entry.path)
            relative = entry_path.relative_to(fixtures_dir).as_posix()
            try:
                mode = os.lstat(entry_path).st_mode
            except OSError as exc:
                raise TestManifestError(f"cannot inspect fixture {relative}: {exc}") from exc
            if stat.S_ISLNK(mode):
                raise TestManifestError(f"fixtures must not contain a symlink: {relative}")
            if stat.S_ISDIR(mode):
                visit(entry_path)
            elif not stat.S_ISREG(mode):
                raise TestManifestError(
                    f"fixtures contains unsupported fixture {relative} ({_fixture_entry_type(mode)})"
                )

    visit(fixtures_dir)


def _fixture_entry_type(mode: int) -> str:
    if stat.S_ISFIFO(mode):
        return "FIFO"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character device"
    if stat.S_ISBLK(mode):
        return "block device"
    return "special file"


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TestManifestError(f"{location} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TestManifestError(f"{location} keys must be strings")
    return value


def _exact_keys(
    data: dict[str, Any],
    allowed: set[str],
    location: str,
    *,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    unknown = set(data) - allowed
    missing = allowed - optional - set(data)
    if unknown:
        raise TestManifestError(f"{location} has unknown keys: {', '.join(sorted(unknown))}")
    if missing:
        raise TestManifestError(f"{location} is missing required keys: {', '.join(sorted(missing))}")


def _nonempty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TestManifestError(f"{location} must be a non-empty string")
    return value


def _safe_path_component(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value


def _validate_unique(values: Any, label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise TestManifestError(f"duplicate {label}: {value}")
        seen.add(value)
