"""Deterministic affected-test planning from repository inventories and Git paths."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable, Mapping, Sequence

import yaml

from .digest import content_digest


class TestImpactError(RuntimeError):
    """Affected-test selection cannot safely determine its input."""


class AffectedTestsBlocked(TestImpactError):
    """A requested Git base is not usable; callers must report BLOCKED."""


@dataclass(frozen=True, order=True)
class NameStatus:
    status: str
    path: str
    new_path: str | None = None

    def __post_init__(self) -> None:
        if not self.status or not _safe_path(self.path):
            raise ValueError("invalid Git name-status path")
        if self.new_path is not None and not _safe_path(self.new_path):
            raise ValueError("invalid Git name-status destination path")


@dataclass(frozen=True, order=True)
class ImpactSkill:
    skill_id: str
    path: str
    content_digest: str
    layer: str = ""

    def __post_init__(self) -> None:
        if not self.skill_id or not _safe_path(self.path) or not self.content_digest:
            raise ValueError("invalid Skill impact inventory")


@dataclass(frozen=True, order=True)
class ImpactProfile:
    profile_id: str
    skill_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.profile_id or any(not item for item in self.skill_ids):
            raise ValueError("invalid Profile impact inventory")


@dataclass(frozen=True)
class ImpactInventory:
    skills: tuple[ImpactSkill, ...] = ()
    profiles: tuple[ImpactProfile, ...] = ()

    def __post_init__(self) -> None:
        skills = tuple(sorted(self.skills))
        profiles = tuple(sorted(self.profiles))
        if len({item.skill_id for item in skills}) != len(skills):
            raise ValueError("duplicate Skill ID in impact inventory")
        if len({item.profile_id for item in profiles}) != len(profiles):
            raise ValueError("duplicate Profile ID in impact inventory")
        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "profiles", profiles)


@dataclass(frozen=True)
class TestSelection:
    core: bool = False
    skill_ids: tuple[str, ...] = ()
    profile_ids: tuple[str, ...] = ()
    collection_paths: tuple[Path, ...] = ()
    reasons: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    digests: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        skills = tuple(sorted(set(self.skill_ids)))
        profiles = tuple(sorted(set(self.profile_ids)))
        paths = tuple(sorted({Path(path) for path in self.collection_paths}, key=lambda item: item.as_posix()))
        if any(path.is_absolute() or ".." in path.parts for path in paths):
            raise ValueError("test collection paths must be repository-relative")
        object.__setattr__(self, "skill_ids", skills)
        object.__setattr__(self, "profile_ids", profiles)
        object.__setattr__(self, "collection_paths", paths)
        object.__setattr__(self, "reasons", tuple(sorted(set(self.reasons))))
        object.__setattr__(self, "changed_paths", tuple(sorted(set(self.changed_paths))))
        object.__setattr__(self, "digests", tuple(sorted(set(self.digests))))

    @property
    def required_case_ids(self) -> tuple[str, ...]:
        return tuple(
            (["core"] if self.core else [])
            + [f"skill:{skill_id}" for skill_id in self.skill_ids]
            + [f"profile:{profile_id}" for profile_id in self.profile_ids]
        )


def select_from_changes(
    pre: ImpactInventory,
    post: ImpactInventory,
    changes: Iterable[NameStatus | str],
) -> TestSelection:
    """Select behavior collections using semantic inventory deltas plus changed paths.

    Metadata-only transitions are deliberately ignored unless the inventory proves
    a changed Skill digest or Profile membership/member digest.
    """
    change_records = tuple(_coerce_change(item) for item in changes)
    changed_paths = tuple(sorted({path for item in change_records for path in _paths(item)}))
    before_skills = {item.skill_id: item for item in pre.skills}
    after_skills = {item.skill_id: item for item in post.skills}
    before_profiles = {item.profile_id: item for item in pre.profiles}
    after_profiles = {item.profile_id: item for item in post.profiles}
    selected_skills: set[str] = set()
    selected_profiles: set[str] = set()
    reasons: set[str] = set()

    # Content and identity transitions are behavioral. Layer/source/revision/ignore
    # differences without an ID or digest difference never reach this branch.
    for skill_id, after in after_skills.items():
        before = before_skills.get(skill_id)
        if before is None:
            selected_skills.add(skill_id)
            reasons.add(f"skill-added:{skill_id}")
        elif before.content_digest != after.content_digest:
            selected_skills.add(skill_id)
            reasons.add(f"skill-content:{skill_id}")

    # A changed path below a Skill is not proof of a behavioral payload delta:
    # layer-only R100 moves have exactly that shape. Canonical pre/post IDs and
    # digests above decide Skill behavior; path inputs only map test fixtures.

    before_digests = {item.skill_id: item.content_digest for item in pre.skills}
    after_digests = {item.skill_id: item.content_digest for item in post.skills}
    for profile_id, after in after_profiles.items():
        before = before_profiles.get(profile_id)
        before_members = before.skill_ids if before is not None else ()
        after_members = after.skill_ids
        if before is None or before_members != after_members or any(
            before_digests.get(skill_id) != after_digests.get(skill_id) for skill_id in after_members
        ):
            selected_profiles.add(profile_id)
            reasons.add(f"profile-members:{profile_id}")

    for skill_id in tuple(selected_skills):
        for profile in post.profiles:
            if skill_id in profile.skill_ids:
                selected_profiles.add(profile.profile_id)
                reasons.add(f"profile-member-content:{profile.profile_id}")

    core = False
    for path in changed_paths:
        if _is_runtime_path(path):
            core = True
            selected_skills.update(after_skills)
            selected_profiles.update(after_profiles)
            reasons.add("runtime-all")
        elif path.startswith("src/hwskill/"):
            core = True
            reasons.add("core-source")
        elif path.startswith("tests/core/"):
            core = True
            reasons.add("core-test")
        else:
            target = _test_target(path, after_skills, after_profiles)
            if target is not None:
                kind, target_id = target
                if kind == "skill":
                    selected_skills.add(target_id)
                    reasons.add(f"test-skill:{target_id}")
                else:
                    selected_profiles.add(target_id)
                    reasons.add(f"test-profile:{target_id}")

    paths = tuple(
        [Path("tests/skills") / skill_id / "test.yaml" for skill_id in selected_skills]
        + [Path("tests/profiles") / profile_id / "test.yaml" for profile_id in selected_profiles]
    )
    return TestSelection(
        core=core,
        skill_ids=tuple(selected_skills),
        profile_ids=tuple(selected_profiles),
        collection_paths=paths,
        reasons=tuple(reasons),
        changed_paths=changed_paths,
        digests=tuple((skill_id, after_skills[skill_id].content_digest) for skill_id in selected_skills if skill_id in after_skills),
    )


def select_affected_tests(repo_root: Path, base: str | None = None) -> TestSelection:
    """Build a selection from Git's NUL-delimited name-status output and inventories."""
    root = Path(repo_root).resolve()
    post = _inventory_from_worktree(root)
    if base is None:
        pre = post
        changes = _worktree_changes(root)
    else:
        pre = _inventory_at_revision(root, base)
        changes = _git_name_status(root, ["diff", "--name-status", "-z", base, "--"])
        changes += _worktree_changes(root)
    return select_from_changes(pre, post, changes)


def load_impact_inventory(repo_root: Path) -> ImpactInventory:
    """Load the current structured inventory for verification-state comparison."""
    return _inventory_from_worktree(Path(repo_root).resolve())


def _worktree_changes(root: Path) -> tuple[NameStatus, ...]:
    changes = _git_name_status(root, ["diff", "--name-status", "-z"])
    changes += _git_name_status(root, ["diff", "--cached", "--name-status", "-z"])
    untracked = _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    changes += tuple(NameStatus("A", path) for path in _nul_items(untracked))
    return changes


def _git_name_status(root: Path, arguments: Sequence[str]) -> tuple[NameStatus, ...]:
    return _parse_name_status(_git(root, arguments))


def _git(root: Path, arguments: Sequence[str]) -> bytes:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", *arguments], cwd=root, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=environment,
    )
    if completed.returncode:
        text = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AffectedTestsBlocked(f"cannot read Git change input: {text or 'git command failed'}")
    return completed.stdout


def _parse_name_status(data: bytes) -> tuple[NameStatus, ...]:
    fields = _nul_items(data)
    result: list[NameStatus] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status or index >= len(fields):
            raise TestImpactError("malformed NUL-delimited Git name-status output")
        first = fields[index]
        index += 1
        if status[:1] in {"R", "C"}:
            if index >= len(fields):
                raise TestImpactError("malformed rename/copy Git name-status output")
            result.append(NameStatus(status, first, fields[index]))
            index += 1
        else:
            result.append(NameStatus(status, first))
    return tuple(result)


def _nul_items(data: bytes) -> tuple[str, ...]:
    if data and not data.endswith(b"\0"):
        raise TestImpactError("Git NUL-delimited output is truncated")
    return tuple(item.decode("utf-8", errors="surrogateescape") for item in data.split(b"\0") if item)


def _inventory_at_revision(root: Path, revision: str) -> ImpactInventory:
    try:
        catalog = json.loads(_git(root, ["show", f"{revision}:registry/catalog.json"]).decode("utf-8"))
        paths = _nul_items(_git(root, ["ls-tree", "-r", "--name-only", "-z", revision, "--", "profiles"]))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AffectedTestsBlocked(f"cannot read inventory at Git base {revision!r}") from exc
    profiles: list[ImpactProfile] = []
    for path in paths:
        if not path.startswith("profiles/") or not path.endswith(".yaml"):
            continue
        try:
            data = yaml.safe_load(_git(root, ["show", f"{revision}:{path}"]).decode("utf-8")) or {}
            profiles.append(ImpactProfile(str(data["id"]), tuple(str(item) for item in data.get("skills", ()))))
        except (KeyError, TypeError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
            raise AffectedTestsBlocked(f"cannot read profile inventory at Git base {revision!r}") from exc
    return _inventory_from_catalog(catalog, profiles)


def _inventory_from_worktree(root: Path) -> ImpactInventory:
    try:
        catalog = json.loads((root / "registry/catalog.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        catalog = None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TestImpactError("cannot read current registry catalog") from exc
    profiles: list[ImpactProfile] = []
    for path in sorted((root / "profiles").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profiles.append(ImpactProfile(str(data["id"]), tuple(str(item) for item in data.get("skills", ()))))
        except (KeyError, TypeError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise TestImpactError(f"cannot read current profile inventory: {path}") from exc
    if catalog is not None:
        inventory = _inventory_from_catalog(catalog, profiles)
        skills = tuple(
            ImpactSkill(
                item.skill_id, item.path,
                content_digest(root / item.path) if (root / item.path).is_dir() else item.content_digest,
                item.layer,
            )
            for item in inventory.skills
        )
        return ImpactInventory(skills, inventory.profiles)
    skills: list[ImpactSkill] = []
    for governance in sorted((root / "skills-src").glob("**/skill.yaml")):
        try:
            data = yaml.safe_load(governance.read_text(encoding="utf-8")) or {}
            skills.append(ImpactSkill(
                str(data["id"]), governance.parent.relative_to(root).as_posix(),
                str(data["content_digest"]), str(data.get("layer", "")),
            ))
        except (KeyError, TypeError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise TestImpactError(f"cannot read current Skill inventory: {governance}") from exc
    return ImpactInventory(tuple(skills), tuple(profiles))


def _inventory_from_catalog(catalog: Mapping[str, object], profiles: Sequence[ImpactProfile]) -> ImpactInventory:
    try:
        entries = catalog["skills"]
        if not isinstance(entries, list):
            raise TypeError("skills is not a list")
        skills = tuple(ImpactSkill(str(item["id"]), str(item["path"]), str(item["content_digest"]), str(item.get("layer", ""))) for item in entries if isinstance(item, dict))
    except (KeyError, TypeError, ValueError) as exc:
        raise TestImpactError("invalid registry catalog impact inventory") from exc
    return ImpactInventory(skills=skills, profiles=tuple(profiles))


def _coerce_change(value: NameStatus | str | Path) -> NameStatus:
    return value if isinstance(value, NameStatus) else NameStatus("M", Path(value).as_posix())


def _paths(change: NameStatus) -> tuple[str, ...]:
    return (change.path,) if change.new_path is None else (change.path, change.new_path)


def _safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _skill_at_path(skills: Iterable[ImpactSkill], path: str) -> ImpactSkill | None:
    return next((skill for skill in skills if _inside(path, skill.path)), None)


def _test_target(
    path: str,
    skills: Mapping[str, ImpactSkill],
    profiles: Mapping[str, ImpactProfile],
) -> tuple[str, str] | None:
    parts = PurePosixPath(path).parts
    if len(parts) >= 4 and parts[:2] == ("tests", "skills"):
        remainder = "/".join(parts[2:])
        candidates = [skill_id for skill_id in skills if remainder == skill_id or remainder.startswith(skill_id + "/")]
        return ("skill", max(candidates, key=len)) if candidates else None
    if len(parts) >= 4 and parts[:2] == ("tests", "profiles"):
        return ("profile", parts[2]) if parts[2] in profiles else None
    return None


def _is_runtime_path(path: str) -> bool:
    if not path.startswith("src/hwskill/"):
        return False
    name = path.rsplit("/", 1)[-1]
    return name in {"loader.py", "search.py", "catalog.py"} or name.endswith("_adapter.py") or name in {"hosts.py", "configuration.py"}
