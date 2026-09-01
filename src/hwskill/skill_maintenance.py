from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Any

import yaml

from .digest import content_digest
from .frontmatter import FrontmatterError, parse_skill_markdown
from .git_source import DiscoveredSkill, GitSourceClient
from .integrity import require_integrity
from .maintenance_transaction import MaintenancePlan, MaintenanceSummary, RepositoryTransaction, validated_plan
from .profiles import ProfileError, remove_profile_skill_id, replace_profile_skill_id
from .source_manifest import IgnoredSkill, ResolvedSourceSkill, UpstreamSource
from .source_maintenance import (
    SourceMaintenanceError,
    _catalog_content,
    _load_governance,
    _load_sources_with_paths,
    _skill_destination,
    _stage_source_manifest,
    _yaml,
)


class SkillMaintenanceError(ValueError):
    """A Skill lifecycle request cannot produce a consistent repository."""


class SkillReferencedError(SkillMaintenanceError):
    """A requested delete has repository-owned references that need a policy."""


@dataclass(frozen=True)
class SkillReferences:
    profile_ids: tuple[str, ...]
    test_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _SkillLocation:
    skill_id: str
    layer: str
    destination: Path
    governance: dict[str, Any]


def find_skill_references(repo_root: Path, skill_id: str) -> SkillReferences:
    root = Path(repo_root)
    profile_ids: list[str] = []
    profiles = root / "profiles"
    if profiles.is_dir():
        for path in sorted(profiles.glob("*.yaml")):
            data = _read_yaml(path)
            if isinstance(data, dict) and isinstance(data.get("skills"), list) and skill_id in data["skills"]:
                profile_ids.append(path.stem)
    test_paths: list[Path] = []
    tests = root / "tests"
    if tests.is_dir():
        for path in sorted(tests.glob("**/test.yaml")):
            data = _read_yaml(path)
            target = data.get("target") if isinstance(data, dict) else None
            if isinstance(target, dict) and target.get("kind") == "skill" and target.get("id") == skill_id:
                test_paths.append(path)
    return SkillReferences(tuple(sorted(profile_ids)), tuple(test_paths))


def plan_create_manual(
    repo_root: Path, skill_id: str, layer: str, description: str, license_name: str,
) -> MaintenancePlan:
    root = Path(repo_root)
    destination = _destination(layer, skill_id)
    if not isinstance(description, str) or not description.strip() or "\n" in description:
        raise SkillMaintenanceError("manual Skill description must be a non-empty single line")
    if not isinstance(license_name, str) or not license_name.strip():
        raise SkillMaintenanceError("manual Skill license must be non-empty")
    if _find_skill(root, skill_id, required=False) is not None:
        raise SkillMaintenanceError(f"Skill already exists: {skill_id}")

    tx = _transaction(root)
    try:
        name = skill_id.split("/", 1)[1]
        tx.write_text(destination / "SKILL.md", _markdown(name, description, ""))
        _write_governance(tx, destination, skill_id, layer, name, description, license_name, {"kind": "manual"})
        _stage_catalog(tx)
        return _validated_plan(tx, MaintenanceSummary(operation="skill-create", added_skill_ids=(skill_id,)))
    except BaseException:
        tx.discard()
        raise


def plan_update_manual(repo_root: Path, skill_id: str) -> MaintenancePlan:
    root = Path(repo_root)
    location = _require_skill(root, skill_id)
    if _source_kind(location.governance, skill_id) != "manual":
        raise SkillMaintenanceError(
            "cannot update an upstream Skill with skill update; use source update because it may overwrite upstream content"
        )
    tx = _transaction(root)
    try:
        candidate = tx.candidate_root / location.destination
        name, description = _markdown_metadata(candidate / "SKILL.md")
        _write_governance(
            tx, location.destination, skill_id, location.layer, name, description,
            _string_field(location.governance, "license", skill_id), {"kind": "manual"},
            existing=location.governance,
        )
        _stage_catalog(tx)
        return _validated_plan(tx, MaintenanceSummary(operation="skill-update", updated_skill_ids=(skill_id,)))
    except BaseException:
        tx.discard()
        raise


def plan_move_skill(repo_root: Path, skill_id: str, layer: str) -> MaintenancePlan:
    root = Path(repo_root)
    location = _require_skill(root, skill_id)
    destination = _destination(layer, skill_id)
    if destination == location.destination:
        raise SkillMaintenanceError(f"Skill is already in layer: {layer}")
    tx = _transaction(root)
    try:
        _copy_candidate_tree(tx, location.destination, destination)
        tx.delete(location.destination)
        candidate = tx.candidate_root / destination
        name, description = _markdown_metadata(candidate / "SKILL.md")
        _write_governance(
            tx, destination, skill_id, layer, name, description,
            _string_field(location.governance, "license", skill_id), location.governance["source"],
            existing=location.governance,
        )
        if _source_kind(location.governance, skill_id) == "upstream":
            source_path, source, resolved = _resolved_source_for_skill(root, skill_id)
            _stage_source_manifest(tx, dataclass_replace(
                source,
                skills=tuple(
                    dataclass_replace(item, layer=layer, content_digest=content_digest(candidate)) if item == resolved else item
                    for item in source.skills
                ),
            ))
        _stage_catalog(tx)
        return _validated_plan(tx, MaintenanceSummary(operation="skill-move", updated_skill_ids=(skill_id,)))
    except BaseException:
        tx.discard()
        raise


def plan_rename_skill(repo_root: Path, old_id: str, new_id: str) -> MaintenancePlan:
    root = Path(repo_root)
    location = _require_skill(root, old_id)
    if old_id == new_id:
        raise SkillMaintenanceError("new Skill id must differ from old Skill id")
    if _find_skill(root, new_id, required=False) is not None:
        raise SkillMaintenanceError(f"Skill already exists: {new_id}")
    destination = _destination(location.layer, new_id)
    tx = _transaction(root)
    try:
        _copy_candidate_tree(tx, location.destination, destination)
        tx.delete(location.destination)
        candidate = tx.candidate_root / destination
        _, description = _markdown_metadata(candidate / "SKILL.md")
        new_name = new_id.split("/", 1)[1]
        _rewrite_markdown_name(tx, destination / "SKILL.md", new_name)
        _write_governance(
            tx, destination, new_id, location.layer, new_name, description,
            _string_field(location.governance, "license", old_id), location.governance["source"],
            existing=location.governance,
        )
        affected_profiles = _replace_profile_references(tx, old_id, new_id)
        _replace_test_references(tx, old_id, new_id)
        if _source_kind(location.governance, old_id) == "upstream":
            _, source, resolved = _resolved_source_for_skill(root, old_id)
            _stage_source_manifest(tx, dataclass_replace(
                source,
                skills=tuple(
                    dataclass_replace(item, skill_id=new_id, content_digest=content_digest(candidate)) if item == resolved else item
                    for item in source.skills
                ),
            ))
        _stage_catalog(tx)
        return _validated_plan(tx, MaintenanceSummary(
            operation="skill-rename", updated_skill_ids=(new_id,), affected_profile_ids=affected_profiles,
        ))
    except BaseException:
        tx.discard()
        raise


def plan_delete_skill(repo_root: Path, skill_id: str, remove_from_profiles: bool) -> MaintenancePlan:
    root = Path(repo_root)
    location = _require_skill(root, skill_id)
    references = find_skill_references(root, skill_id)
    if references.test_paths or (references.profile_ids and not remove_from_profiles):
        details: list[str] = []
        if references.profile_ids:
            details.append("Profiles: " + ", ".join(references.profile_ids))
        if references.test_paths:
            details.append("tests: " + ", ".join(path.relative_to(root).as_posix() for path in references.test_paths))
        raise SkillReferencedError("Skill is referenced by " + "; ".join(details))
    tx = _transaction(root)
    try:
        tx.delete(location.destination)
        affected_profiles: tuple[str, ...] = ()
        if remove_from_profiles:
            affected_profiles = _remove_profile_references(tx, skill_id)
        if _source_kind(location.governance, skill_id) == "upstream":
            _, source, resolved = _resolved_source_for_skill(root, skill_id)
            ignored = {item.path: item for item in source.upstream.ignore}
            ignored[resolved.path] = IgnoredSkill(resolved.path, "deleted manually")
            _stage_source_manifest(tx, dataclass_replace(
                source,
                skills=tuple(item for item in source.skills if item != resolved),
                upstream=dataclass_replace(source.upstream, ignore=tuple(sorted(ignored.values(), key=lambda item: item.path))),
            ))
        _stage_catalog(tx)
        return _validated_plan(tx, MaintenanceSummary(
            operation="skill-delete", removed_skill_ids=(skill_id,), affected_profile_ids=affected_profiles,
        ))
    except BaseException:
        tx.discard()
        raise


def plan_manualize_skill(repo_root: Path, skill_id: str) -> MaintenancePlan:
    root = Path(repo_root)
    location = _require_skill(root, skill_id)
    if _source_kind(location.governance, skill_id) != "upstream":
        raise SkillMaintenanceError(f"Skill is already manual: {skill_id}")
    _, source, resolved = _resolved_source_for_skill(root, skill_id)
    tx = _transaction(root)
    try:
        _write_governance(
            tx, location.destination, skill_id, location.layer,
            _string_field(location.governance, "name", skill_id),
            _string_field(location.governance, "description", skill_id),
            _string_field(location.governance, "license", skill_id), {"kind": "manual"},
            existing=location.governance,
        )
        ignored = {item.path: item for item in source.upstream.ignore}
        ignored[resolved.path] = IgnoredSkill(resolved.path, "manualized")
        _stage_source_manifest(tx, dataclass_replace(
            source,
            skills=tuple(item for item in source.skills if item != resolved),
            upstream=dataclass_replace(source.upstream, ignore=tuple(sorted(ignored.values(), key=lambda item: item.path))),
        ))
        _stage_catalog(tx)
        return _validated_plan(tx, MaintenanceSummary(
            operation="skill-manualize", source_ids=(source.source_id,), manualized_skill_ids=(skill_id,),
        ))
    except BaseException:
        tx.discard()
        raise


def plan_adopt_skill(
    repo_root: Path, source_id: str, skill_id: str, upstream_path: str, replace: bool,
    git_client: GitSourceClient,
) -> MaintenancePlan:
    root = Path(repo_root)
    normalized_path = _safe_upstream_path(upstream_path)
    _, source = _find_source(root, source_id)
    if any(item.path == normalized_path for item in source.skills):
        raise SkillMaintenanceError(f"upstream path is already resolved: {normalized_path}")
    existing = _find_skill(root, skill_id, required=False)
    if existing is not None and _source_kind(existing.governance, skill_id) != "manual":
        raise SkillMaintenanceError(f"Skill id is already upstream-managed: {skill_id}")

    persisted_revision = _persisted_source_revision(source)
    materialize_track = persisted_revision or source.upstream.track
    with tempfile.TemporaryDirectory(prefix="hwskill-skill-adopt-") as temporary:
        checkout = Path(temporary)
        resolved = git_client.materialize(source.upstream.repository, materialize_track, checkout)
        if persisted_revision is not None and resolved.commit != persisted_revision:
            raise SkillMaintenanceError(
                f"materialized persisted source revision differs: expected {persisted_revision}, got {resolved.commit}"
            )
        source_revision = persisted_revision or resolved.commit
        payload = _adoptable_upstream_payload(checkout, source.upstream.skills_path, normalized_path)
        if existing is not None:
            _assert_safe_skill_tree(root / existing.destination, "local Skill payload")
        item = _upstream_metadata(payload, normalized_path)
        upstream_digest = content_digest(payload)
        existing_digest = content_digest(root / existing.destination) if existing is not None else None
        if existing_digest is not None and existing_digest != upstream_digest and not replace:
            raise SkillMaintenanceError("manual Skill content differs from upstream; retry with replace=True to overwrite it")

        layer = existing.layer if existing is not None else source.defaults.layer
        destination = existing.destination if existing is not None else _destination(layer, skill_id)
        tx = _transaction(root)
        try:
            if existing is None or existing_digest != upstream_digest:
                _stage_upstream_payload(tx, payload, destination)
                _write_governance(
                    tx, destination, skill_id, layer, item.name, item.description,
                    source.defaults.license, _upstream_provenance(source, source_revision, normalized_path),
                    existing=existing.governance if existing is not None else None,
                )
            else:
                # Identical manual payloads change ownership only; their local
                # description, license, layer, and status remain untouched.
                _write_governance(
                    tx, destination, skill_id, layer,
                    _string_field(existing.governance, "name", skill_id),
                    _string_field(existing.governance, "description", skill_id),
                    _string_field(existing.governance, "license", skill_id),
                    _upstream_provenance(source, source_revision, normalized_path), existing=existing.governance,
                )
            ignored = {ignored.path: ignored for ignored in source.upstream.ignore}
            ignored.pop(normalized_path, None)
            staged_source = dataclass_replace(
                source,
                resolved_revision=source_revision,
                skills=tuple(sorted((*source.skills, ResolvedSourceSkill(
                    normalized_path, skill_id, layer, content_digest(tx.candidate_root / destination),
                )), key=lambda value: value.path)),
                upstream=dataclass_replace(source.upstream, ignore=tuple(sorted(ignored.values(), key=lambda value: value.path))),
            )
            _stage_source_manifest(tx, staged_source)
            _stage_catalog(tx)
            return _validated_plan(tx, MaintenanceSummary(
                operation="source-adopt", source_ids=(source.source_id,),
                added_skill_ids=(skill_id,) if existing is None else (),
                updated_skill_ids=() if existing is None else (skill_id,),
            ))
        except BaseException:
            tx.discard()
            raise


def _transaction(root: Path) -> RepositoryTransaction:
    return RepositoryTransaction(root)


def _validated_plan(tx: RepositoryTransaction, summary: MaintenanceSummary) -> MaintenancePlan:
    return validated_plan(tx, summary, require_integrity)


def _stage_catalog(tx: RepositoryTransaction) -> None:
    tx.write_text(Path("registry/catalog.json"), _catalog_content(tx.candidate_root))


def _find_skill(root: Path, skill_id: str, *, required: bool) -> _SkillLocation | None:
    found: list[_SkillLocation] = []
    skills_root = root / "skills-src"
    try:
        skills_root.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SkillMaintenanceError("cannot inspect skills-src") from error
    else:
        # All lifecycle planners pass through this inventory scan.  Validate
        # before globbing or loading any governance file so symlinked parents,
        # payloads, and skill.yaml files cannot redirect a repository read.
        _assert_safe_skill_tree(skills_root, "skills-src")
        for governance_path in sorted(skills_root.glob("**/skill.yaml")):
            governance = _load_governance(governance_path)
            if governance.get("id") != skill_id:
                continue
            layer = _string_field(governance, "layer", skill_id)
            destination = _destination(layer, skill_id)
            if governance_path.parent != root / destination:
                raise SkillMaintenanceError(f"Skill path does not match id/layer: {skill_id}")
            found.append(_SkillLocation(skill_id, layer, destination, governance))
    if len(found) > 1:
        raise SkillMaintenanceError(f"duplicate Skill id: {skill_id}")
    if found:
        return found[0]
    if required:
        raise SkillMaintenanceError(f"unknown Skill: {skill_id}")
    return None


def _require_skill(root: Path, skill_id: str) -> _SkillLocation:
    skill = _find_skill(root, skill_id, required=True)
    assert skill is not None
    return skill


def _destination(layer: str, skill_id: str) -> Path:
    try:
        return _skill_destination(layer, skill_id)
    except SourceMaintenanceError as error:
        raise SkillMaintenanceError(str(error)) from error


def _source_kind(governance: dict[str, Any], skill_id: str) -> str:
    source = governance.get("source")
    if not isinstance(source, dict) or source.get("kind") not in {"manual", "upstream"}:
        raise SkillMaintenanceError(f"invalid source kind: {skill_id}")
    return str(source["kind"])


def _resolved_source_for_skill(root: Path, skill_id: str) -> tuple[Path, UpstreamSource, ResolvedSourceSkill]:
    matches: list[tuple[Path, UpstreamSource, ResolvedSourceSkill]] = []
    for path, source in _load_sources_with_paths(root):
        for resolved in source.skills:
            if resolved.skill_id == skill_id:
                matches.append((path, source, resolved))
    if len(matches) != 1:
        raise SkillMaintenanceError(f"upstream Skill is not resolved exactly once: {skill_id}")
    return matches[0]


def _find_source(root: Path, source_id: str) -> tuple[Path, UpstreamSource]:
    matches = [(path, source) for path, source in _load_sources_with_paths(root) if source.source_id == source_id]
    if len(matches) != 1:
        raise SkillMaintenanceError(f"unknown source id: {source_id}")
    return matches[0]


def _write_governance(
    tx: RepositoryTransaction, destination: Path, skill_id: str, layer: str, name: str,
    description: str, license_name: str, provenance: dict[str, str], *, existing: dict[str, Any] | None = None,
) -> None:
    data = dict(existing or {})
    data.update({
        "schema_version": 1,
        "id": skill_id,
        "name": name,
        "description": description,
        "layer": layer,
        "status": data.get("status", "experimental"),
        "source": provenance,
        "license": license_name,
        "content_digest": content_digest(tx.candidate_root / destination),
    })
    tx.write_text(destination / "skill.yaml", _yaml(data))


def _markdown(name: str, description: str, body: str) -> str:
    return "---\n" + yaml.safe_dump({"name": name, "description": description}, allow_unicode=True, sort_keys=False) + "---\n\n" + body


def _markdown_metadata(path: Path) -> tuple[str, str]:
    try:
        metadata, _ = parse_skill_markdown(path.read_text(encoding="utf-8"))
    except (OSError, FrontmatterError, yaml.YAMLError) as error:
        raise SkillMaintenanceError(f"invalid SKILL.md: {error}") from error
    return str(metadata["name"]), str(metadata["description"])


def _rewrite_markdown_name(tx: RepositoryTransaction, relative_path: Path, name: str) -> None:
    path = tx.candidate_root / relative_path
    try:
        metadata, body = parse_skill_markdown(path.read_text(encoding="utf-8"))
    except (OSError, FrontmatterError, yaml.YAMLError) as error:
        raise SkillMaintenanceError(f"invalid SKILL.md: {error}") from error
    metadata["name"] = name
    tx.write_text(relative_path, "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + body)


def _copy_candidate_tree(tx: RepositoryTransaction, old: Path, new: Path) -> None:
    source = tx.candidate_root / old
    _assert_safe_skill_tree(source, f"Skill payload {old.as_posix()}")
    for path in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix()):
        relative = path.relative_to(source)
        if path.is_dir():
            (tx.candidate_root / new / relative).mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            tx.write_bytes(new / relative, path.read_bytes())


def _stage_upstream_payload(tx: RepositoryTransaction, source: Path, destination: Path) -> None:
    _assert_safe_skill_tree(source, "upstream Skill payload")
    tx.delete(destination)
    for path in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix()):
        relative = path.relative_to(source)
        if path.is_dir():
            (tx.candidate_root / destination / relative).mkdir(parents=True, exist_ok=True)
        elif relative.as_posix() != "skill.yaml":
            tx.write_bytes(destination / relative, path.read_bytes())


def _replace_profile_references(tx: RepositoryTransaction, old_id: str, new_id: str) -> tuple[str, ...]:
    affected: list[str] = []
    profiles = tx.candidate_root / "profiles"
    if not profiles.is_dir():
        return ()
    for path in sorted(profiles.glob("*.yaml")):
        data = _read_yaml(path)
        if isinstance(data, dict) and isinstance(data.get("skills"), list) and old_id in data["skills"]:
            try:
                updated = replace_profile_skill_id(data, old_id, new_id)
            except ProfileError as error:
                raise SkillMaintenanceError(f"cannot rename Profile {path.stem}: {error}") from error
            tx.write_text(path.relative_to(tx.candidate_root), _yaml(updated))
            affected.append(path.stem)
    return tuple(affected)


def _remove_profile_references(tx: RepositoryTransaction, skill_id: str) -> tuple[str, ...]:
    affected: list[str] = []
    profiles = tx.candidate_root / "profiles"
    if not profiles.is_dir():
        return ()
    for path in sorted(profiles.glob("*.yaml")):
        data = _read_yaml(path)
        if isinstance(data, dict) and isinstance(data.get("skills"), list) and skill_id in data["skills"]:
            try:
                updated = remove_profile_skill_id(data, skill_id)
            except ProfileError as error:
                raise SkillMaintenanceError(f"cannot update Profile {path.stem}: {error}") from error
            tx.write_text(path.relative_to(tx.candidate_root), _yaml(updated))
            affected.append(path.stem)
    return tuple(affected)


def _replace_test_references(tx: RepositoryTransaction, old_id: str, new_id: str) -> None:
    tests = tx.candidate_root / "tests"
    if not tests.is_dir():
        return
    for path in sorted(tests.glob("**/test.yaml")):
        data = _read_yaml(path)
        target = data.get("target") if isinstance(data, dict) else None
        if isinstance(target, dict) and target.get("kind") == "skill" and target.get("id") == old_id:
            updated = dict(data)
            updated["target"] = dict(target)
            updated["target"]["id"] = new_id
            tx.write_text(path.relative_to(tx.candidate_root), _yaml(updated))


def _safe_upstream_path(path: str) -> str:
    if not isinstance(path, str) or not path or path != path.strip() or "\\" in path:
        raise SkillMaintenanceError("upstream path must be a safe relative path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or path in {".", ".."} or ".." in parsed.parts:
        raise SkillMaintenanceError("upstream path must be a safe relative path")
    return path


def _persisted_source_revision(source: UpstreamSource) -> str | None:
    """Return the immutable snapshot, if the source has been materialized."""
    return None if source.resolved_revision == "0" * 40 else source.resolved_revision


def _adoptable_upstream_payload(checkout: Path, skills_path: str, upstream_path: str) -> Path:
    skills_root = _walk_real_directories(
        checkout, PurePosixPath(skills_path).parts, "upstream skills path",
    )
    path_parts = PurePosixPath(upstream_path).parts
    if len(path_parts) != 1:
        raise SkillMaintenanceError("upstream Skill path must name a direct child of the skills path")
    payload = skills_root / path_parts[0]
    _assert_safe_skill_tree(payload, "upstream Skill payload")
    return payload


def _upstream_metadata(payload: Path, upstream_path: str) -> DiscoveredSkill:
    name, description = _markdown_metadata(payload / "SKILL.md")
    return DiscoveredSkill(path=upstream_path, name=name, description=description)


def _walk_real_directories(root: Path, parts: tuple[str, ...], label: str) -> Path:
    current = root
    _assert_real_directory(current, label)
    for part in parts:
        current = current / part
        _assert_real_directory(current, label)
    return current


def _assert_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SkillMaintenanceError(f"{label} is unavailable: {path.name}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SkillMaintenanceError(f"{label} must be a real directory")


def _assert_safe_skill_tree(source: Path, label: str) -> None:
    _assert_real_directory(source, label)
    try:
        paths = sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix())
    except OSError as error:
        raise SkillMaintenanceError(f"cannot inspect {label}") from error
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SkillMaintenanceError(f"cannot inspect {label}") from error
        if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise SkillMaintenanceError(f"unsafe {label} entry: {path.name}")


def _upstream_provenance(source: UpstreamSource, revision: str, upstream_path: str) -> dict[str, str]:
    return {"kind": "upstream", "source_id": source.source_id, "revision": revision, "upstream_path": upstream_path}


def _string_field(data: dict[str, Any], field: str, skill_id: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SkillMaintenanceError(f"invalid {field} for Skill: {skill_id}")
    return value


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SkillMaintenanceError(f"cannot read YAML {path}: {error}") from error
