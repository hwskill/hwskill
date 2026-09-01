"""Schema validation and discovery for declarative Skill/Profile test collections."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeAlias

import yaml


class TestManifestError(ValueError):
    """Raised when a declarative test collection is not schema-safe."""


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
class TestCollection:
    manifest_path: Path
    target: TestTarget
    cases: tuple[TestCase, ...]
    fixtures_dir: Path


def load_test_collection(path: Path, repo_root: Path) -> TestCollection:
    """Load one repository-contained collection without following symlinks."""
    root = _repository_root(repo_root)
    manifest_path = _safe_repo_path(path, root, "test manifest")
    if not manifest_path.is_file():
        raise TestManifestError(f"test manifest must be a regular file: {manifest_path}")
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
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
    return TestCollection(
        manifest_path=manifest_path,
        target=target,
        cases=cases,
        fixtures_dir=manifest_path.parent / "fixtures",
    )


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
    if kind not in {"skill", "profile"}:
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
            raise TestManifestError(f"symlink is not allowed for {label}: {current}")
    if require_exists and not candidate.exists():
        raise TestManifestError(f"{label} does not exist: {candidate}")
    return candidate


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise TestManifestError(f"symlink is not allowed for {label}: {current}")


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
