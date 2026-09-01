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


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    path: str
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
    root = Path(repo_root).resolve()
    issues: list[IntegrityIssue] = []

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

    profile_paths = tuple(_inventory_files(root / "profiles", "*.yaml", root, issues))
    for path in profile_paths:
        _parse_yaml_mapping(path, root, issues, "invalid-profile", "profile definition")

    for path in _inventory_repository_locks(root, issues):
        _parse_yaml_mapping(path, root, issues, "invalid-lock", "repository lock")

    for path in _inventory_test_manifests(root, issues):
        _parse_yaml_mapping(path, root, issues, "invalid-test-manifest", "test manifest")

    catalog_path = root / "registry" / "catalog.json"
    catalog_ok = _parse_catalog(catalog_path, root, issues)
    registry_ok = skill_tree_safe and _validate_registry(root, issues)
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
        issues=tuple(sorted(issues, key=lambda item: (item.path, item.code, item.message))),
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
    if not directory.is_dir() or directory.is_symlink():
        if directory.is_symlink():
            issues.append(_issue("unsafe-path", root, directory, "symlinked inventory directory is not followed"))
        return
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        retained_directories = []
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                issues.append(_issue("unsafe-path", root, candidate, "symlinked directory is not followed"))
            else:
                retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            if not Path(name).match(pattern):
                continue
            candidate = current_path / name
            if _regular_file(candidate):
                yield candidate
            else:
                issues.append(_issue("unsafe-path", root, candidate, "inventory file must be a regular file"))


def _inventory_tree_is_safe(directory: Path, root: Path, issues: list[IntegrityIssue]) -> bool:
    """Reject links before a focused Registry parser could resolve one."""
    if not directory.exists():
        return True
    if directory.is_symlink() or not directory.is_dir():
        issues.append(_issue("unsafe-path", root, directory, "Skill inventory root must be a real directory"))
        return False
    safe = True
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        retained_directories = []
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                issues.append(_issue("unsafe-path", root, candidate, "symlinked directory is not followed"))
                safe = False
            else:
                retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            candidate = current_path / name
            try:
                mode = candidate.lstat().st_mode
            except OSError as exc:
                issues.append(_issue("unsafe-path", root, candidate, f"cannot inspect Skill payload: {exc}"))
                safe = False
                continue
            if not stat.S_ISREG(mode):
                issues.append(_issue("unsafe-path", root, candidate, "Skill payload must be a regular file"))
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
            if candidate.is_symlink():
                issues.append(_issue("unsafe-path", root, candidate, "symlinked directory is not followed"))
            else:
                retained_directories.append(name)
        directories[:] = retained_directories
        if current_path.name == ".hwskills" and "lock.yaml" in files:
            candidate = current_path / "lock.yaml"
            if _regular_file(candidate):
                yield candidate
            else:
                issues.append(_issue("unsafe-path", root, candidate, "repository lock must be a regular file"))


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
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        issues.append(_issue(code, root, path, f"cannot parse {label}: {exc}"))
        return
    if not isinstance(data, dict):
        issues.append(_issue(code, root, path, f"{label} must be a mapping"))


def _parse_catalog(path: Path, root: Path, issues: list[IntegrityIssue]) -> bool:
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


def _issue(code: str, root: Path, path: Path, message: str) -> IntegrityIssue:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return IntegrityIssue(code=code, path=relative, message=message)
