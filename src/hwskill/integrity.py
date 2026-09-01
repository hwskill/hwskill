"""Offline repository inventory for the registry integrity gate.

This module deliberately reports independently discoverable problems together.
The more specific source/Skill/Catalog and reference checks are added by later
integrity tasks; the inventory here is their common, safe traversal boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterator

import yaml

from . import registry
from .models import EffectiveCatalog
from .profiles import ProfileDefinition, _lock_data
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


@dataclass(frozen=True)
class _SourceSkillReference:
    manifest_path: Path
    source_id: str | None
    revision: str | None
    skill_id: str
    upstream_path: str
    layer: str
    content_digest: str


@dataclass(frozen=True)
class _GovernedSkill:
    record: registry.SkillRecord
    governance_path: Path
    upstream_path: str | None


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
    source_references = _source_references(source_paths, root, issues)

    profile_tree_safe = _inventory_tree_is_safe(root / "profiles", root, issues)
    profile_paths = tuple(_inventory_files(root / "profiles", "*.yaml", root, issues))

    catalog_path = root / "registry" / "catalog.json"
    catalog_data = _parse_catalog(catalog_path, root, issues)
    if skill_tree_safe:
        governed_skills, registry_ok = _governed_skills(
            root, governance, issues, validate_profiles=profile_tree_safe
        )
        profile_definitions = _validate_profile_definitions(
            root, profile_paths, {item.record.skill_id for item in governed_skills}, issues
        )
        _validate_repository_profile_locks(
            root, profile_definitions, governed_skills, issues
        )
        issues.extend(validate_test_manifest_references(
            root,
            {item.record.skill_id for item in governed_skills},
            set(profile_definitions),
        ))
        _check_source_skill_consistency(root, source_references, governed_skills, issues)
        if catalog_data is not None:
            _check_catalog_entries(root, catalog_path, catalog_data, governed_skills, issues)
    else:
        governed_skills, registry_ok = (), False
        profile_definitions = _validate_profile_definitions(root, profile_paths, set(), issues)
        _validate_repository_profile_locks(root, profile_definitions, (), issues)
        issues.extend(validate_test_manifest_references(root, set(), set(profile_definitions)))
    registry_ok = skill_tree_safe and profile_tree_safe and registry_ok
    if catalog_data is not None and registry_ok:
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


def _inventory_repository_profile_pairs(
    root: Path, issues: list[IntegrityIssue],
) -> Iterator[tuple[Path | None, Path | None]]:
    """Yield repository-owned profile/lock pairs without following links."""
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
        if current_path.name != ".hwskills":
            continue
        profile = current_path / "profile.yaml" if "profile.yaml" in files else None
        lock = current_path / "lock.yaml" if "lock.yaml" in files else None
        safe_profile = _repository_pair_file(root, profile, issues, "repository profile")
        safe_lock = _repository_pair_file(root, lock, issues, "repository lock")
        if safe_profile is not None or safe_lock is not None:
            yield safe_profile, safe_lock


def _repository_pair_file(
    root: Path, path: Path | None, issues: list[IntegrityIssue], label: str,
) -> Path | None:
    if path is None:
        return None
    if not _path_components_are_safe(root, path, issues):
        return None
    if _regular_file(path):
        return path
    _unsafe_path(root, path, issues, f"{label} must be a regular file")
    return None


def _read_yaml_mapping(
    path: Path,
    root: Path,
    issues: list[IntegrityIssue],
    code: str,
    label: str,
) -> dict[str, Any] | None:
    """Read one already-inventoried mapping and preserve unrelated diagnostics."""
    if not _path_components_are_safe(root, path, issues):
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        issues.append(_issue(code, root, path, f"cannot parse {label}: {exc}"))
        return None
    if not isinstance(data, dict):
        issues.append(_issue(code, root, path, f"{label} must be a mapping"))
        return None
    return data


def _validate_profile_definitions(
    root: Path,
    profile_paths: tuple[Path, ...],
    known_skill_ids: set[str],
    issues: list[IntegrityIssue],
) -> dict[str, ProfileDefinition]:
    """Return parseable repository Profile definitions while reporting broken edges."""
    definitions: dict[str, ProfileDefinition] = {}
    origins: dict[str, list[Path]] = {}
    for path in sorted(profile_paths, key=lambda item: _relative_path(root, item)):
        data = _read_yaml_mapping(path, root, issues, "invalid-profile", "profile definition")
        if data is None:
            continue
        profile_id = data.get("id")
        skills = data.get("skills")
        description = data.get("description", "")
        if (
            not isinstance(profile_id, str)
            or not profile_id
            or profile_id != path.stem
            or not isinstance(skills, list)
            or any(not isinstance(skill_id, str) or not skill_id for skill_id in skills)
            or len(skills) != len(set(skills))
        ):
            issues.append(_issue("invalid-profile", root, path, "invalid profile definition"))
            continue
        origins.setdefault(profile_id, []).append(path)
        definitions.setdefault(
            profile_id,
            ProfileDefinition(profile_id, str(description), tuple(skills)),
        )
        for skill_id in sorted(set(skills) - known_skill_ids):
            issues.append(_issue(
                "unknown-profile-skill", root, path,
                f"profile {profile_id} references an unknown Skill: {skill_id}",
            ))
    for profile_id, paths in sorted(origins.items()):
        if len(paths) > 1:
            canonical = min(paths, key=lambda item: _relative_path(root, item))
            issues.append(_issue(
                "duplicate-profile-id", root, canonical,
                f"profile id is declared by multiple definitions: {profile_id}",
            ))
    return definitions


def _validate_repository_profile_locks(
    root: Path,
    definitions: dict[str, ProfileDefinition],
    governed_skills: tuple[_GovernedSkill, ...],
    issues: list[IntegrityIssue],
) -> None:
    """Compare every checked-in project binding to the canonical lock payload."""
    by_skill_id = {item.record.skill_id: item.record for item in governed_skills}
    profile_pairs = tuple(_inventory_repository_profile_pairs(root, issues))
    for profile_path, lock_path in sorted(
        profile_pairs,
        key=lambda pair: _relative_path(root, pair[0] or pair[1]),
    ):
        if profile_path is None:
            if lock_path is not None:
                issues.append(_issue(
                    "missing-profile-selection", root, lock_path,
                    "repository lock has no sibling profile.yaml",
                ))
            continue
        if lock_path is None:
            issues.append(_issue(
                "missing-profile-lock", root, profile_path,
                "repository profile.yaml has no sibling lock.yaml",
            ))
            continue
        profile_data = _read_yaml_mapping(
            profile_path, root, issues, "invalid-profile-selection", "repository profile selection",
        )
        lock_data = _read_yaml_mapping(
            lock_path, root, issues, "invalid-lock", "repository lock",
        )
        if profile_data is None or lock_data is None:
            continue
        profile_ids = _repository_profile_ids(profile_data)
        if profile_ids is None:
            issues.append(_issue(
                "invalid-profile-selection", root, profile_path,
                "repository profile selection has an invalid profiles list",
            ))
            continue
        missing_profiles = sorted(set(profile_ids) - set(definitions))
        if missing_profiles:
            for profile_id in missing_profiles:
                issues.append(_issue(
                    "unknown-profile", root, profile_path,
                    f"repository profile selection references an unknown profile: {profile_id}",
                ))
            continue
        selected_skill_ids = {
            skill_id
            for profile_id in profile_ids
            for skill_id in definitions[profile_id].skill_ids
        }
        missing_skills = sorted(selected_skill_ids - set(by_skill_id))
        if missing_skills:
            for skill_id in missing_skills:
                issues.append(_issue(
                    "unknown-profile-skill", root, profile_path,
                    f"repository profile selection resolves an unknown Skill: {skill_id}",
                ))
            continue
        records = tuple(by_skill_id[skill_id] for skill_id in sorted(selected_skill_ids))
        digest_input = json.dumps(
            [(record.skill_id, record.content_digest) for record in records],
            separators=(",", ":"),
        ).encode()
        catalog = EffectiveCatalog(
            project=profile_path.parent.parent,
            registry_root=root,
            profile_ids=profile_ids,
            skills=records,
            catalog_digest="sha256:" + hashlib.sha256(digest_input).hexdigest(),
            effective_scope="project",
            profile_source=profile_path,
        )
        expected = _lock_data(catalog)
        actual = {
            "schema_version": lock_data.get("schema_version"),
            "catalog_digest": lock_data.get("catalog_digest"),
            "skills": lock_data.get("skills"),
        }
        if actual != expected:
            issues.append(_issue(
                "stale-profile-lock", root, lock_path,
                "lock does not match the resolved profile; set the profile again",
            ))


def _repository_profile_ids(data: dict[str, Any]) -> tuple[str, ...] | None:
    values = data.get("profiles", ())
    if not isinstance(values, (list, tuple)):
        return None
    profile_ids = tuple(str(item) for item in values)
    if any(not profile_id for profile_id in profile_ids):
        return None
    return tuple(dict.fromkeys(profile_ids))


def validate_test_manifest_references(
    repo_root: Path,
    known_skill_ids: set[str],
    known_profile_ids: set[str],
) -> tuple[IntegrityIssue, ...]:
    """Validate declarative test target headers without parsing or executing cases."""
    root = Path(repo_root).absolute()
    issues: list[IntegrityIssue] = []
    if not _repository_root_is_safe(root, issues):
        return tuple(sorted(issues))
    targets: dict[tuple[str, str], list[Path]] = {}
    for kind in ("skills", "profiles"):
        for path in _inventory_files(root / "tests" / kind, "test.yaml", root, issues):
            data = _read_yaml_mapping(path, root, issues, "invalid-test-manifest", "test manifest")
            if data is None:
                continue
            target = data.get("target")
            schema_version = data.get("schema_version")
            target_kind = target.get("kind") if isinstance(target, dict) else None
            target_id = target.get("id") if isinstance(target, dict) else None
            if (
                schema_version != 1
                or target_kind not in {"skill", "profile"}
                or not isinstance(target_id, str)
                or not target_id
                or target_id != target_id.strip()
            ):
                issues.append(_issue(
                    "invalid-test-manifest", root, path,
                    "test manifest must define schema_version 1 and a target kind/id",
                ))
                continue
            targets.setdefault((target_kind, target_id), []).append(path)
            known_ids = known_skill_ids if target_kind == "skill" else known_profile_ids
            if target_id not in known_ids:
                issues.append(_issue(
                    f"unknown-test-{target_kind}", root, path,
                    f"test target references an unknown {target_kind}: {target_id}",
                ))
    for (target_kind, target_id), paths in sorted(targets.items()):
        if len(paths) > 1:
            canonical = min(paths, key=lambda item: _relative_path(root, item))
            issues.append(_issue(
                "duplicate-test-target", root, canonical,
                f"multiple test collections target {target_kind} {target_id}",
            ))
    return tuple(sorted(issues))


def _parse_catalog(path: Path, root: Path, issues: list[IntegrityIssue]) -> dict[str, Any] | None:
    if not _path_components_are_safe(root, path, issues):
        return None
    if not _regular_file(path):
        issues.append(_issue("invalid-catalog", root, path, "Catalog must be a regular JSON file"))
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(_issue("invalid-catalog", root, path, f"cannot parse Catalog: {exc}"))
        return None
    if not isinstance(data, dict):
        issues.append(_issue("invalid-catalog", root, path, "Catalog must be a JSON object"))
        return None
    return data


def _governed_skills(
    root: Path,
    governance_paths: tuple[Path, ...],
    issues: list[IntegrityIssue],
    *,
    validate_profiles: bool,
) -> tuple[tuple[_GovernedSkill, ...], bool]:
    """Collect individually valid Skills even when the strict Registry entrypoint fails."""
    valid: list[_GovernedSkill] = []
    for path in governance_paths:
        try:
            record = registry.validate_skill(root, path)
        except Exception:
            continue
        upstream_path = _governance_upstream_path(path)
        valid.append(_GovernedSkill(record, path, upstream_path))
    if validate_profiles:
        try:
            registry.validate_registry(root)
        except Exception as exc:
            issues.append(_issue("invalid-registry", root, root / "skills-src", str(exc)))
            return tuple(valid), False
    elif len(valid) != len(governance_paths):
        return tuple(valid), False
    return tuple(valid), True


def _governance_upstream_path(path: Path) -> str | None:
    """Read only the provenance detail needed for source cross-indexing.

    `validate_skill` has already accepted this file, so a failed best-effort read
    simply leaves the relevant source edge unmatched rather than crashing the run.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    source = data.get("source") if isinstance(data, dict) else None
    path_value = source.get("upstream_path") if isinstance(source, dict) else None
    return path_value if isinstance(path_value, str) else None


def _source_references(
    source_paths: tuple[Path, ...],
    root: Path,
    issues: list[IntegrityIssue],
) -> tuple[_SourceSkillReference, ...]:
    """Load valid manifests and retain enough raw shape to diagnose invalid ones."""
    references: list[_SourceSkillReference] = []
    source_ids: dict[str, list[Path]] = {}
    for path in source_paths:
        raw = _read_source_data(path)
        source_id = _raw_source_id(raw)
        if source_id is not None:
            source_ids.setdefault(source_id, []).append(path)
        _report_raw_resolved_ignore_overlap(root, path, raw, issues)
        try:
            source = load_source_manifest(path)
        except Exception as exc:
            issues.append(_issue("invalid-source-manifest", root, path, str(exc)))
            references.extend(_raw_source_references(path, raw))
            continue
        references.extend(
            _SourceSkillReference(
                manifest_path=path,
                source_id=source.source_id,
                revision=source.resolved_revision,
                skill_id=item.skill_id,
                upstream_path=item.path,
                layer=item.layer,
                content_digest=item.content_digest,
            )
            for item in source.skills
        )
    for source_id, paths in sorted(source_ids.items()):
        if len(paths) > 1:
            ordered_paths = tuple(sorted(paths, key=lambda item: _relative_path(root, item)))
            issues.append(_issue(
                "duplicate-source-id", root, ordered_paths[0],
                f"source_id is declared by multiple manifests: {source_id}",
            ))
    return tuple(references)


def _read_source_data(path: Path) -> object | None:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None


def _raw_source_id(raw: object | None) -> str | None:
    """Return only a manifest-shaped scalar ID without depending on full parsing."""
    if not isinstance(raw, dict):
        return None
    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not source_id or source_id != source_id.strip():
        return None
    return source_id


def _report_raw_resolved_ignore_overlap(
    root: Path,
    manifest_path: Path,
    raw: object | None,
    issues: list[IntegrityIssue],
) -> None:
    if not isinstance(raw, dict):
        return
    upstream = raw.get("upstream")
    resolved = raw.get("resolved")
    ignored = upstream.get("ignore") if isinstance(upstream, dict) else None
    resolved_skills = resolved.get("skills") if isinstance(resolved, dict) else None
    ignored_paths = [item.get("path") for item in ignored if isinstance(item, dict) and isinstance(item.get("path"), str)] if isinstance(ignored, list) else []
    resolved_paths = [item.get("path") for item in resolved_skills if isinstance(item, dict) and isinstance(item.get("path"), str)] if isinstance(resolved_skills, list) else []
    for resolved_path in sorted(set(resolved_paths)):
        if any(_paths_overlap(resolved_path, ignored_path) for ignored_path in ignored_paths):
            issues.append(_issue(
                "source-resolved-ignore-overlap", root, manifest_path,
                f"resolved path overlaps an ignored path: {resolved_path}",
            ))


def _raw_source_references(path: Path, raw: object | None) -> tuple[_SourceSkillReference, ...]:
    if not isinstance(raw, dict):
        return ()
    source_id = _raw_source_id(raw)
    resolved = raw.get("resolved")
    revision = resolved.get("revision") if isinstance(resolved, dict) and isinstance(resolved.get("revision"), str) else None
    skills = resolved.get("skills") if isinstance(resolved, dict) else None
    if not isinstance(skills, list):
        return ()
    result: list[_SourceSkillReference] = []
    for item in skills:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("id")
        upstream_path = item.get("path")
        layer = item.get("layer")
        digest = item.get("content_digest")
        if all(isinstance(value, str) for value in (skill_id, upstream_path, layer, digest)):
            result.append(_SourceSkillReference(path, source_id, revision, skill_id, upstream_path, layer, digest))
    return tuple(result)


def _check_source_skill_consistency(
    root: Path,
    references: tuple[_SourceSkillReference, ...],
    governed_skills: tuple[_GovernedSkill, ...],
    issues: list[IntegrityIssue],
) -> None:
    by_id = {item.record.skill_id: item for item in governed_skills}
    by_source_path: dict[tuple[str | None, str], list[_GovernedSkill]] = {}
    for skill in governed_skills:
        if skill.record.source_kind == "upstream" and skill.upstream_path is not None:
            by_source_path.setdefault((skill.record.source_id, skill.upstream_path), []).append(skill)
    references_by_id: dict[str, list[_SourceSkillReference]] = {}
    references_by_source_path: dict[tuple[str | None, str], list[_SourceSkillReference]] = {}
    for reference in references:
        references_by_id.setdefault(reference.skill_id, []).append(reference)
        references_by_source_path.setdefault((reference.source_id, reference.upstream_path), []).append(reference)
    for (source_id, upstream_path), owners in sorted(
        references_by_source_path.items(),
        key=lambda item: (item[0][0] is None, item[0][0] or "", item[0][1]),
    ):
        if source_id is None or len({owner.skill_id for owner in owners}) < 2:
            continue
        ordered_owners = tuple(sorted(owners, key=lambda item: _reference_sort_key(root, item)))
        evidence = ", ".join(
            f"{_relative_path(root, owner.manifest_path)} -> {owner.skill_id}"
            for owner in ordered_owners
        )
        issues.append(_issue(
            "duplicate-source-path", root, ordered_owners[0].manifest_path,
            f"source_id {source_id} maps upstream path {upstream_path} to multiple Skill IDs: {evidence}",
        ))
    for skill_id, owners in sorted(references_by_id.items()):
        if len(owners) > 1:
            ordered_owners = tuple(sorted(owners, key=lambda item: _reference_sort_key(root, item)))
            issues.append(_issue(
                "duplicate-source-skill", root, ordered_owners[0].manifest_path,
                f"resolved Skill has multiple source owners: {skill_id}",
            ))
        for reference in owners:
            skill = by_id.get(reference.skill_id)
            if skill is None:
                path_matches = by_source_path.get((reference.source_id, reference.upstream_path), [])
                if len(path_matches) == 1:
                    skill = path_matches[0]
                    issues.append(_issue(
                        "source-skill-id-mismatch", root, reference.manifest_path,
                        f"resolved Skill id differs for source path {reference.upstream_path}: {reference.skill_id}",
                    ))
                    _compare_source_reference(root, reference, skill, issues)
                    continue
                issues.append(_issue(
                    "source-skill-missing", root, reference.manifest_path,
                    f"resolved Skill has no governed payload: {reference.skill_id}",
                ))
                continue
            if skill.record.source_kind == "manual":
                issues.append(_issue(
                    "manual-skill-resolved", root, reference.manifest_path,
                    f"manual Skill appears in a resolved source: {reference.skill_id}",
                ))
                continue
            _compare_source_reference(root, reference, skill, issues)
    for skill in governed_skills:
        if skill.record.source_kind != "upstream":
            continue
        matching = [
            reference for reference in references_by_id.get(skill.record.skill_id, [])
            if reference.source_id == skill.record.source_id and reference.upstream_path == skill.upstream_path
        ]
        matching_path = references_by_source_path.get((skill.record.source_id, skill.upstream_path), [])
        if not matching and not matching_path:
            issues.append(_issue(
                "upstream-skill-unresolved", root, skill.governance_path,
                f"upstream Skill is not resolved by its source: {skill.record.skill_id}",
            ))


def _compare_source_reference(
    root: Path,
    reference: _SourceSkillReference,
    skill: _GovernedSkill,
    issues: list[IntegrityIssue],
) -> None:
    record = skill.record
    checks = (
        ("source-skill-source-mismatch", record.source_id, reference.source_id, "source_id"),
        ("source-skill-path-mismatch", skill.upstream_path, reference.upstream_path, "upstream path"),
        ("source-skill-layer-mismatch", record.layer, reference.layer, "layer"),
        ("source-skill-revision-mismatch", record.revision, reference.revision, "revision"),
        ("source-skill-digest-mismatch", record.content_digest, reference.content_digest, "content digest"),
    )
    for code, actual, expected, label in checks:
        if actual != expected:
            issues.append(_issue(
                code, root, reference.manifest_path,
                f"resolved Skill {label} differs for {record.skill_id}",
            ))
    expected_path = Path("skills-src") / reference.layer / record.skill_id.split("/")[0] / record.skill_id.split("/")[1]
    if record.path.relative_to(root) != expected_path:
        issues.append(_issue(
            "source-skill-physical-path-mismatch", root, reference.manifest_path,
            f"resolved Skill path differs for {record.skill_id}",
        ))


def _check_catalog_entries(
    root: Path,
    catalog_path: Path,
    catalog_data: dict[str, Any],
    governed_skills: tuple[_GovernedSkill, ...],
    issues: list[IntegrityIssue],
) -> None:
    entries = catalog_data.get("skills")
    if not isinstance(entries, list):
        return
    actual: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            issues.append(_issue("invalid-catalog", root, catalog_path, "Catalog Skill entries must have string ids"))
            continue
        skill_id = entry["id"]
        if skill_id in actual:
            issues.append(_issue("catalog-duplicate-skill", root, catalog_path, f"Catalog has duplicate Skill id: {skill_id}"))
            continue
        actual[skill_id] = entry
    expected = {item.record.skill_id: _catalog_entry(root, item.record) for item in governed_skills}
    for skill_id in sorted(actual.keys() - expected.keys()):
        issues.append(_issue("catalog-extra-skill", root, catalog_path, f"Catalog has no governed Skill: {skill_id}"))
    for skill_id in sorted(expected.keys() - actual.keys()):
        issues.append(_issue("catalog-missing-skill", root, catalog_path, f"Catalog is missing governed Skill: {skill_id}"))
    for skill_id in sorted(expected.keys() & actual.keys()):
        if actual[skill_id] != expected[skill_id]:
            issues.append(_issue("catalog-skill-mismatch", root, catalog_path, f"Catalog entry differs for Skill: {skill_id}"))


def _catalog_entry(root: Path, record: registry.SkillRecord) -> dict[str, Any]:
    return {
        "id": record.skill_id,
        "name": record.name,
        "description": record.description,
        "layer": record.layer,
        "source_kind": record.source_kind,
        "source_id": record.source_id,
        "revision": record.revision,
        "license": record.license,
        "content_digest": record.content_digest,
        "path": record.path.relative_to(root).as_posix(),
    }


def _paths_overlap(first: str, second: str) -> bool:
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _reference_sort_key(root: Path, reference: _SourceSkillReference) -> tuple[str, str, str, str, str, str, str]:
    return (
        _relative_path(root, reference.manifest_path),
        reference.source_id or "",
        reference.revision or "",
        reference.skill_id,
        reference.upstream_path,
        reference.layer,
        reference.content_digest,
    )


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
