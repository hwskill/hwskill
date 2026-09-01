"""Offline repository inventory for the registry integrity gate.

This module deliberately reports independently discoverable problems together.
The more specific source/Skill/Catalog and reference checks are added by later
integrity tasks; the inventory here is their common, safe traversal boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Iterator

import yaml

from . import registry
from .source_manifest import load_source_manifest


@dataclass(frozen=True, order=True)
class IntegrityIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class IntegrityReport:
    issues: tuple[IntegrityIssue, ...]
    skill_count: int
    source_count: int
    profile_count: int

    @property
    def ok(self) -> bool:
        return not self.issues


class IntegrityError(ValueError):
    """Raised when callers require a clean report before continuing."""

    def __init__(self, report: IntegrityReport) -> None:
        self.report = report
        super().__init__(f"repository integrity check failed ({len(report.issues)} issue(s))")


def check_integrity(repo_root: Path) -> IntegrityReport:
    """Collect offline inventory problems without stopping at the first one."""
    root = Path(repo_root).absolute()
    issues: list[IntegrityIssue] = []
    if not _repository_root_is_safe(root, issues):
        return IntegrityReport(tuple(sorted(issues)), 0, 0, 0)

    skill_tree_safe = _inventory_tree_is_safe(root / "skills-src", root, issues)
    skill_markdown = tuple(_inventory_files(root / "skills-src", "SKILL.md", root, issues))
    governance = tuple(_inventory_files(root / "skills-src", "skill.yaml", root, issues))
    governance_dirs = {path.parent for path in governance}
    markdown_dirs = {path.parent for path in skill_markdown}
    for path in skill_markdown:
        if path.parent not in governance_dirs:
            issues.append(_issue("orphan-skill", root, path, "SKILL.md has no sibling skill.yaml"))
    for path in governance:
        if path.parent not in markdown_dirs:
            issues.append(_issue("orphan-governance", root, path, "skill.yaml has no sibling SKILL.md"))

    source_paths = tuple(_inventory_files(root / "sources", "*.yaml", root, issues))
    for path in source_paths:
        try:
            load_source_manifest(path)
        except Exception as exc:
            issues.append(_issue("invalid-source-manifest", root, path, str(exc)))

    profile_tree_safe = _inventory_tree_is_safe(root / "profiles", root, issues)
    profile_paths = tuple(_inventory_files(root / "profiles", "*.yaml", root, issues))
    for path in profile_paths:
        _parse_yaml_mapping(path, root, issues, "invalid-profile", "profile definition")

    for path in _inventory_repository_locks(root, issues):
        _parse_yaml_mapping(path, root, issues, "invalid-lock", "repository lock")

    for path in _inventory_test_manifests(root, issues):
        _parse_yaml_mapping(path, root, issues, "invalid-test-manifest", "test manifest")

    catalog_path = root / "registry" / "catalog.json"
    catalog_ok = _parse_catalog(catalog_path, root, issues)
    registry_ok = skill_tree_safe and profile_tree_safe and _validate_registry(root, issues)
    if catalog_ok and registry_ok:
        expected_catalog = json.dumps(
            registry.build_catalog(root), ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"
        try:
            actual_catalog = catalog_path.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append(_issue("catalog-stale", root, catalog_path, f"Catalog is missing or unreadable; run hwskill registry build ({exc})"))
        else:
            if actual_catalog != expected_catalog:
                issues.append(_issue("catalog-stale", root, catalog_path, "Catalog differs from the current registry; run hwskill registry build"))

    return IntegrityReport(
        issues=tuple(sorted(issues)),
        skill_count=len(governance_dirs & markdown_dirs),
        source_count=len(source_paths),
        profile_count=len(profile_paths),
    )


def require_integrity(repo_root: Path) -> IntegrityReport:
    """Return a clean report or raise an exception that retains all issues."""
    report = check_integrity(repo_root)
    if not report.ok:
        raise IntegrityError(report)
    return report


def _inventory_files(
    directory: Path,
    pattern: str,
    root: Path,
    issues: list[IntegrityIssue],
) -> Iterator[Path]:
    if not _path_components_are_safe(root, directory, issues):
        return
    try:
        directory_mode = directory.lstat().st_mode
    except OSError:
        return
    if not stat.S_ISDIR(directory_mode):
        return
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        retained_directories = []
        for name in directories:
            candidate = current_path / name
            if _path_components_are_safe(root, candidate, issues):
                retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            if not Path(name).match(pattern):
                continue
            candidate = current_path / name
            if not _path_components_are_safe(root, candidate, issues):
                continue
            if _regular_file(candidate):
                yield candidate
            else:
                _unsafe_path(root, candidate, issues, "inventory file must be a regular file")


def _inventory_tree_is_safe(directory: Path, root: Path, issues: list[IntegrityIssue]) -> bool:
    """Reject links before a focused Registry parser could resolve one."""
    if not _path_components_are_safe(root, directory, issues):
        return False
    try:
        directory_mode = directory.lstat().st_mode
    except FileNotFoundError:
        return True
    except OSError as exc:
        _unsafe_path(root, directory, issues, f"cannot inspect inventory root: {exc}")
        return False
    if not stat.S_ISDIR(directory_mode):
        _unsafe_path(root, directory, issues, "inventory root must be a real directory")
        return False
    safe = True
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        retained_directories = []
        for name in directories:
            candidate = current_path / name
            if not _path_components_are_safe(root, candidate, issues):
                safe = False
            else:
                retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            candidate = current_path / name
            if not _path_components_are_safe(root, candidate, issues):
                safe = False
                continue
            try:
                mode = candidate.lstat().st_mode
            except OSError as exc:
                _unsafe_path(root, candidate, issues, f"cannot inspect Skill payload: {exc}")
                safe = False
                continue
            if not stat.S_ISREG(mode):
                _unsafe_path(root, candidate, issues, "Skill payload must be a regular file")
                safe = False
    return safe


def _inventory_repository_locks(root: Path, issues: list[IntegrityIssue]) -> Iterator[Path]:
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        retained_directories = []
        for name in directories:
            candidate = current_path / name
            if name in {".git", ".venv", "__pycache__"}:
                continue
            if _path_components_are_safe(root, candidate, issues):
                retained_directories.append(name)
        directories[:] = retained_directories
        if current_path.name == ".hwskills" and "lock.yaml" in files:
            candidate = current_path / "lock.yaml"
            if not _path_components_are_safe(root, candidate, issues):
                continue
            if _regular_file(candidate):
                yield candidate
            else:
                _unsafe_path(root, candidate, issues, "repository lock must be a regular file")


def _inventory_test_manifests(root: Path, issues: list[IntegrityIssue]) -> Iterator[Path]:
    for kind in ("skills", "profiles"):
        yield from _inventory_files(root / "tests" / kind, "test.yaml", root, issues)


def _parse_yaml_mapping(
    path: Path,
    root: Path,
    issues: list[IntegrityIssue],
    code: str,
    label: str,
) -> None:
    if not _path_components_are_safe(root, path, issues):
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        issues.append(_issue(code, root, path, f"cannot parse {label}: {exc}"))
        return
    if not isinstance(data, dict):
        issues.append(_issue(code, root, path, f"{label} must be a mapping"))


def _parse_catalog(path: Path, root: Path, issues: list[IntegrityIssue]) -> bool:
    if not _path_components_are_safe(root, path, issues):
        return False
    if not _regular_file(path):
        issues.append(_issue("invalid-catalog", root, path, "Catalog must be a regular JSON file"))
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(_issue("invalid-catalog", root, path, f"cannot parse Catalog: {exc}"))
        return False
    if not isinstance(data, dict):
        issues.append(_issue("invalid-catalog", root, path, "Catalog must be a JSON object"))
        return False
    return True


def _validate_registry(root: Path, issues: list[IntegrityIssue]) -> bool:
    try:
        registry.validate_registry(root)
    except Exception as exc:
        issues.append(_issue("invalid-registry", root, root / "skills-src", str(exc)))
        return False
    return True


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _repository_root_is_safe(root: Path, issues: list[IntegrityIssue]) -> bool:
    """Reject an unsafe root before inventory construction can traverse it."""
    try:
        mode = root.lstat().st_mode
    except OSError as exc:
        _unsafe_path(root, root, issues, f"cannot inspect repository root: {exc}")
        return False
    if stat.S_ISLNK(mode):
        _unsafe_path(root, root, issues, "repository root must not be a symlink")
        return False
    if not stat.S_ISDIR(mode):
        _unsafe_path(root, root, issues, "repository root must be a real directory")
        return False
    return True


def _path_components_are_safe(root: Path, path: Path, issues: list[IntegrityIssue]) -> bool:
    """Ensure no component below the repository root is a symlink before any read."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        _unsafe_path(root, path, issues, "inventory path escapes the repository root")
        return False
    candidate = root
    for component in relative.parts:
        candidate = candidate / component
        try:
            mode = candidate.lstat().st_mode
        except OSError:
            return True
        if stat.S_ISLNK(mode):
            _unsafe_path(root, candidate, issues, "symlinked path component is not followed")
            return False
    return True


def _unsafe_path(root: Path, path: Path, issues: list[IntegrityIssue], message: str) -> None:
    issue = _issue("unsafe-path", root, path, message)
    if any(existing.code == issue.code and existing.path == issue.path for existing in issues):
        return
    issues.append(issue)


def _issue(code: str, root: Path, path: Path, message: str) -> IntegrityIssue:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return IntegrityIssue(code=code, path=relative, message=message)
