from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Literal

import yaml

from .digest import content_digest
from .git_source import DiscoveredSkill, GitSourceClient, GitSourceError, ResolvedTrack, discover_skills, verify_existing_tag
from .maintenance_transaction import MaintenancePlan, MaintenanceSummary, RepositoryTransaction
from .registry import RegistryValidationError, build_catalog
from .source_manifest import (
    IgnoredSkill,
    ResolvedSourceSkill,
    SourceDefaults,
    UpstreamConfig,
    UpstreamSource,
    load_source_manifest,
)


class SourceMaintenanceError(ValueError):
    """A source maintenance request cannot produce a consistent candidate."""


@dataclass(frozen=True)
class SourceAddRequest:
    source_id: str
    repository: str
    track: str
    skills_path: str
    defaults: SourceDefaults


@dataclass(frozen=True)
class SourceSelection:
    included_paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]


@dataclass(frozen=True)
class SourceInspection:
    old_revision: str | None
    new_revision: str
    added: tuple[DiscoveredSkill, ...]
    updated: tuple[DiscoveredSkill, ...]
    removed: tuple[str, ...]
    ignored: tuple[str, ...]


@dataclass(frozen=True)
class UpdatePolicies:
    on_added: Literal["include", "ignore", "fail"]
    on_removed: Literal["remove", "manualize", "fail"]


@dataclass(frozen=True)
class _MaterializedSource:
    source: UpstreamSource
    resolved: ResolvedTrack
    checkout: Path
    discovered: tuple[DiscoveredSkill, ...]
    inspection: SourceInspection


def inspect_source(repo_root: Path, source: UpstreamSource, git_client: GitSourceClient) -> SourceInspection:
    """Materialize one source once and classify its direct upstream Skill children."""
    with tempfile.TemporaryDirectory(prefix="hwskill-source-inspection-") as temporary:
        snapshot = _materialize_source(source, git_client, Path(temporary))
        return snapshot.inspection


def plan_add_source(
    repo_root: Path,
    request: SourceAddRequest,
    selection: SourceSelection,
    git_client: GitSourceClient,
) -> MaintenancePlan:
    root = Path(repo_root)
    _validate_source_id(request.source_id)
    known_sources = _load_sources_with_paths(root)
    if any(source.source_id == request.source_id for _, source in known_sources):
        raise SourceMaintenanceError(f"source already exists: {request.source_id}")
    source = UpstreamSource(
        source_id=request.source_id,
        upstream=UpstreamConfig(request.repository, request.track, request.skills_path, ()),
        defaults=request.defaults,
        resolved_revision="0" * 40,
        skills=(),
    )

    with tempfile.TemporaryDirectory(prefix="hwskill-source-add-") as temporary:
        snapshot = _materialize_source(source, git_client, Path(temporary), verify_fixed_tag=False)
        discovered = {item.path: item for item in snapshot.discovered}
        included = _unique_paths(selection.included_paths, "included")
        selected_ignored = _unique_paths(selection.ignored_paths, "ignored")
        if set(included) & set(selected_ignored):
            raise SourceMaintenanceError("a source path cannot be both included and ignored")
        unknown_included = sorted(set(included) - discovered.keys())
        if unknown_included:
            raise SourceMaintenanceError(f"selected paths are not present upstream: {', '.join(unknown_included)}")
        unknown_ignored = sorted(set(selected_ignored) - discovered.keys())
        if unknown_ignored:
            raise SourceMaintenanceError(f"ignored paths are not present upstream: {', '.join(unknown_ignored)}")
        ignored_paths = tuple(sorted((set(discovered) - set(included)) | set(selected_ignored)))
        selected = tuple(discovered[path] for path in sorted(included))
        inferred = {item.path: _infer_skill_id(source.defaults, item) for item in selected}
        _assert_new_ids_available(root, inferred.values())

        resolved_skills: list[ResolvedSourceSkill] = []
        tx = _transaction(root)
        try:
            for item in selected:
                skill_id = inferred[item.path]
                destination = _skill_destination(source.defaults.layer, skill_id)
                _stage_payload(tx, snapshot.checkout, source.upstream.skills_path, item.path, destination)
                digest = content_digest(tx.candidate_root / destination)
                _stage_governance(
                    tx, destination, skill_id, item, source.defaults.layer,
                    source.defaults.license, _upstream_provenance(source, snapshot.resolved.commit, item.path),
                )
                resolved_skills.append(ResolvedSourceSkill(item.path, skill_id, source.defaults.layer, digest))
            staged = replace(
                source,
                resolved_revision=snapshot.resolved.commit,
                skills=tuple(sorted(resolved_skills, key=lambda item: item.path)),
                upstream=replace(source.upstream, ignore=tuple(IgnoredSkill(path, "not selected") for path in ignored_paths)),
            )
            _stage_source_manifest(tx, staged)
            _stage_catalog(tx)
            _validate_candidate(tx.candidate_root)
            return MaintenancePlan(tx, MaintenanceSummary(
                operation="source-add", source_ids=(staged.source_id,),
                added_skill_ids=tuple(item.skill_id for item in staged.skills),
                source_details=({"source_id": staged.source_id, "status": "success", "repository": staged.upstream.repository, "track": staged.upstream.track, "old_revision": None, "new_revision": staged.resolved_revision, "deltas": {"added": [item.path for item in staged.skills], "updated": [], "removed": [], "ignored": [item.path for item in staged.upstream.ignore]}},),
            ))
        except BaseException:
            tx.discard()
            raise


def plan_update_sources(
    repo_root: Path,
    source_ids: tuple[str, ...] | None,
    policies: UpdatePolicies,
    git_client: GitSourceClient,
    track_overrides: dict[str, str] | None = None,
) -> MaintenancePlan:
    _validate_policies(policies)
    root = Path(repo_root)
    source_paths = _load_sources_with_paths(root)
    selected_ids = None if source_ids is None else tuple(source_ids)
    if selected_ids is not None and len(selected_ids) != len(set(selected_ids)):
        raise SourceMaintenanceError("duplicate source id")
    selected = source_paths if selected_ids is None else tuple(
        item for item in source_paths if item[1].source_id in set(selected_ids)
    )
    if selected_ids is not None and {source.source_id for _, source in selected} != set(selected_ids):
        raise SourceMaintenanceError("unknown source id")
    if not selected:
        raise SourceMaintenanceError("no sources selected for update")

    # Keep every checkout alive until every source has resolved successfully.  This is
    # what makes --all a plan, rather than a sequence of independently applied updates.
    with ExitStack() as stack:
        snapshots: list[_MaterializedSource] = []
        for _, source in selected:
            if track_overrides and source.source_id in track_overrides:
                source = replace(source, upstream=replace(source.upstream, track=track_overrides[source.source_id]))
            checkout = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hwskill-source-update-")))
            snapshots.append(_materialize_source(source, git_client, checkout))

        pending_ids: list[str] = []
        for snapshot in snapshots:
            if snapshot.inspection.added and policies.on_added == "fail":
                raise SourceMaintenanceError(f"source {snapshot.source.source_id} has added Skills")
            if snapshot.inspection.removed and policies.on_removed == "fail":
                raise SourceMaintenanceError(f"source {snapshot.source.source_id} has removed Skills")
            for item in snapshot.inspection.added:
                if policies.on_added == "include":
                    pending_ids.append(_infer_skill_id(snapshot.source.defaults, item))
        _assert_new_ids_available(root, pending_ids)

        tx = _transaction(root)
        try:
            summaries: list[MaintenanceSummary] = []
            for snapshot in snapshots:
                summaries.append(_stage_source_update(tx, snapshot, policies))
            _stage_catalog(tx)
            _validate_candidate(tx.candidate_root)
            return MaintenancePlan(tx, _combine_summaries("source-update", summaries))
        except BaseException:
            tx.discard()
            raise


def plan_ignore_change(
    repo_root: Path,
    source_id: str,
    path: str,
    operation: Literal["add", "remove"],
    git_client: GitSourceClient | None = None,
) -> MaintenancePlan:
    root = Path(repo_root)
    source_path, source = _find_source(root, source_id)
    normalized = _safe_source_path(path)
    ignored = {item.path: item for item in source.upstream.ignore}
    resolved_paths = {item.path for item in source.skills}
    if operation not in {"add", "remove"}:
        raise SourceMaintenanceError("ignore operation must be add or remove")
    if operation == "add":
        if normalized in resolved_paths:
            raise SourceMaintenanceError("cannot ignore a resolved Skill; manualize or delete it first")
        if normalized in ignored:
            raise SourceMaintenanceError(f"path is already ignored: {normalized}")
        new_ignore = tuple(sorted((*ignored.values(), IgnoredSkill(normalized, "ignored by user")), key=lambda item: item.path))
    else:
        if normalized not in ignored:
            raise SourceMaintenanceError(f"path is not ignored: {normalized}")
        if git_client is None:
            raise SourceMaintenanceError("removing an ignore requires a Git source client")
        with tempfile.TemporaryDirectory(prefix="hwskill-source-unignore-") as temporary:
            snapshot = _materialize_source(source, git_client, Path(temporary))
            item = next((entry for entry in snapshot.discovered if entry.path == normalized), None)
            if item is not None:
                inferred = _infer_skill_id(source.defaults, item)
                if _is_manual_skill(root, inferred):
                    raise SourceMaintenanceError(
                        f"manual Skill collision; use hwskill source adopt {source_id} {inferred} --path {normalized}"
                    )
        new_ignore = tuple(item for item in source.upstream.ignore if item.path != normalized)

    tx = _transaction(root)
    try:
        _stage_source_manifest(tx, replace(source, upstream=replace(source.upstream, ignore=new_ignore)))
        _stage_catalog(tx)
        _validate_candidate(tx.candidate_root)
        return MaintenancePlan(tx, MaintenanceSummary(operation=f"source-ignore-{operation}", source_ids=(source_id,)))
    except BaseException:
        tx.discard()
        raise


def plan_delete_source(
    repo_root: Path,
    source_id: str,
    skill_policy: Literal["delete", "manualize"],
    remove_from_profiles: bool,
) -> MaintenancePlan:
    if skill_policy not in {"delete", "manualize"}:
        raise SourceMaintenanceError("skill policy must be delete or manualize")
    root = Path(repo_root)
    source_path, source = _find_source(root, source_id)
    profile_references = _profile_references(root, {item.skill_id for item in source.skills})
    if skill_policy == "delete" and profile_references and not remove_from_profiles:
        names = ", ".join(profile_references)
        raise SourceMaintenanceError(f"source Skills are referenced by Profile(s): {names}")

    tx = _transaction(root)
    try:
        if skill_policy == "delete":
            for item in source.skills:
                tx.delete(_skill_destination(item.layer, item.skill_id))
            if remove_from_profiles:
                _remove_source_skills_from_profiles(tx, {item.skill_id for item in source.skills})
            summary = MaintenanceSummary(
                operation="source-delete", source_ids=(source_id,),
                removed_skill_ids=tuple(sorted(item.skill_id for item in source.skills)),
                affected_profile_ids=profile_references,
            )
        else:
            for item in source.skills:
                destination = _skill_destination(item.layer, item.skill_id)
                governance = _load_governance(tx.candidate_root / destination / "skill.yaml")
                governance["source"] = {"kind": "manual"}
                governance["content_digest"] = content_digest(tx.candidate_root / destination)
                tx.write_text(destination / "skill.yaml", _yaml(governance))
            summary = MaintenanceSummary(
                operation="source-manualize", source_ids=(source_id,),
                manualized_skill_ids=tuple(sorted(item.skill_id for item in source.skills)),
            )
        tx.delete(source_path.relative_to(root))
        _stage_catalog(tx)
        _validate_candidate(tx.candidate_root)
        return MaintenancePlan(tx, summary)
    except BaseException:
        tx.discard()
        raise


def _materialize_source(
    source: UpstreamSource,
    git_client: GitSourceClient,
    checkout: Path,
    *,
    verify_fixed_tag: bool = True,
) -> _MaterializedSource:
    resolved = git_client.materialize(source.upstream.repository, source.upstream.track, checkout)
    if verify_fixed_tag and source.upstream.track.startswith("refs/tags/"):
        verify_existing_tag(source.upstream.track, source.resolved_revision, resolved.commit)
    discovered = discover_skills(checkout, source.upstream.skills_path)
    old_by_path = {item.path: item for item in source.skills}
    ignored_paths = {item.path for item in source.upstream.ignore}
    added: list[DiscoveredSkill] = []
    updated: list[DiscoveredSkill] = []
    ignored: list[str] = []
    current_paths = {item.path for item in discovered}
    for item in discovered:
        if item.path in ignored_paths:
            ignored.append(item.path)
        elif item.path not in old_by_path:
            added.append(item)
        else:
            current_digest = content_digest(checkout / source.upstream.skills_path / item.path)
            if current_digest != old_by_path[item.path].content_digest:
                updated.append(item)
    removed = tuple(sorted(set(old_by_path) - current_paths))
    return _MaterializedSource(
        source, resolved, checkout, discovered,
        SourceInspection(source.resolved_revision, resolved.commit, tuple(added), tuple(updated), removed, tuple(ignored)),
    )


def _stage_source_update(tx: RepositoryTransaction, snapshot: _MaterializedSource, policies: UpdatePolicies) -> MaintenanceSummary:
    source = snapshot.source
    by_path = {item.path: item for item in source.skills}
    discovered = {item.path: item for item in snapshot.discovered}
    ignored = {item.path: item for item in source.upstream.ignore}
    removed = set(snapshot.inspection.removed)
    included_added = snapshot.inspection.added if policies.on_added == "include" else ()
    if policies.on_added == "ignore":
        for item in snapshot.inspection.added:
            ignored[item.path] = IgnoredSkill(item.path, "ignored on update")
    retained: list[ResolvedSourceSkill] = []
    manualized: list[str] = []
    deleted: list[str] = []

    for old in source.skills:
        if old.path in removed:
            ignored[old.path] = IgnoredSkill(old.path, "removed upstream")
            destination = _skill_destination(old.layer, old.skill_id)
            if policies.on_removed == "remove":
                tx.delete(destination)
                deleted.append(old.skill_id)
            else:
                governance = _load_governance(tx.candidate_root / destination / "skill.yaml")
                governance["source"] = {"kind": "manual"}
                governance["content_digest"] = content_digest(tx.candidate_root / destination)
                tx.write_text(destination / "skill.yaml", _yaml(governance))
                manualized.append(old.skill_id)
            continue
        item = discovered[old.path]
        destination = _skill_destination(old.layer, old.skill_id)
        upstream_digest = content_digest(snapshot.checkout / source.upstream.skills_path / item.path)
        if item in snapshot.inspection.updated or content_digest(tx.candidate_root / destination) != upstream_digest:
            _stage_payload(tx, snapshot.checkout, source.upstream.skills_path, item.path, destination)
        digest = content_digest(tx.candidate_root / destination)
        _stage_governance(
            tx, destination, old.skill_id, item, old.layer, source.defaults.license,
            _upstream_provenance(source, snapshot.resolved.commit, old.path),
        )
        retained.append(ResolvedSourceSkill(old.path, old.skill_id, old.layer, digest))

    added_ids: list[str] = []
    for item in included_added:
        skill_id = _infer_skill_id(source.defaults, item)
        destination = _skill_destination(source.defaults.layer, skill_id)
        _stage_payload(tx, snapshot.checkout, source.upstream.skills_path, item.path, destination)
        digest = content_digest(tx.candidate_root / destination)
        _stage_governance(
            tx, destination, skill_id, item, source.defaults.layer, source.defaults.license,
            _upstream_provenance(source, snapshot.resolved.commit, item.path),
        )
        retained.append(ResolvedSourceSkill(item.path, skill_id, source.defaults.layer, digest))
        added_ids.append(skill_id)

    staged = replace(
        source,
        resolved_revision=snapshot.resolved.commit,
        skills=tuple(sorted(retained, key=lambda item: item.path)),
        upstream=replace(source.upstream, ignore=tuple(sorted(ignored.values(), key=lambda item: item.path))),
    )
    _stage_source_manifest(tx, staged)
    return MaintenanceSummary(
        operation="source-update", source_ids=(source.source_id,),
        added_skill_ids=tuple(sorted(added_ids)),
        updated_skill_ids=tuple(sorted(_infer_existing_id(by_path, item.path) for item in snapshot.inspection.updated)),
        removed_skill_ids=tuple(sorted(deleted)), manualized_skill_ids=tuple(sorted(manualized)),
        source_details=({"source_id": staged.source_id, "status": "success", "repository": staged.upstream.repository, "track": staged.upstream.track, "old_revision": snapshot.inspection.old_revision, "new_revision": staged.resolved_revision, "deltas": {"added": [item.path for item in snapshot.inspection.added], "updated": [item.path for item in snapshot.inspection.updated], "removed": list(snapshot.inspection.removed), "ignored": list(snapshot.inspection.ignored)}},),
    )


def _infer_existing_id(by_path: dict[str, ResolvedSourceSkill], path: str) -> str:
    return by_path[path].skill_id


def _transaction(root: Path) -> RepositoryTransaction:
    return RepositoryTransaction(root, validate=_validate_candidate)


def _stage_source_manifest(tx: RepositoryTransaction, source: UpstreamSource) -> None:
    _validate_source_id(source.source_id)
    data = {
        "schema_version": 2, "source_id": source.source_id, "kind": "upstream",
        "upstream": {
            "repository": source.upstream.repository, "track": source.upstream.track,
            "skills_path": source.upstream.skills_path,
            "ignore": [{"path": item.path, "reason": item.reason} for item in source.upstream.ignore],
        },
        "defaults": {"namespace": source.defaults.namespace, "layer": source.defaults.layer, "license": source.defaults.license},
        "resolved": {"revision": source.resolved_revision, "skills": [
            {"path": item.path, "id": item.skill_id, "layer": item.layer, "content_digest": item.content_digest}
            for item in source.skills
        ]},
    }
    tx.write_text(Path("sources") / f"{source.source_id}.yaml", _yaml(data))


def _stage_payload(tx: RepositoryTransaction, checkout: Path, skills_path: str, upstream_path: str, destination: Path) -> None:
    source = checkout / Path(*PurePosixPath(skills_path).parts) / Path(*PurePosixPath(upstream_path).parts)
    _assert_safe_payload(source)
    tx.delete(destination)
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        relative = path.relative_to(source)
        if relative.as_posix() == "skill.yaml":
            continue
        if path.is_dir():
            continue
        tx.write_bytes(destination / relative, path.read_bytes())


def _stage_governance(
    tx: RepositoryTransaction, destination: Path, skill_id: str, item: DiscoveredSkill,
    layer: str, license_name: str, provenance: dict[str, str],
) -> None:
    existing_path = tx.candidate_root / destination / "skill.yaml"
    data = _load_governance(existing_path) if existing_path.is_file() else {}
    data.update({
        "schema_version": 1, "id": skill_id, "name": item.name, "description": item.description,
        "layer": layer, "status": data.get("status", "experimental"), "source": provenance,
        "license": license_name, "content_digest": content_digest(tx.candidate_root / destination),
    })
    tx.write_text(destination / "skill.yaml", _yaml(data))


def _upstream_provenance(source: UpstreamSource, revision: str, upstream_path: str) -> dict[str, str]:
    return {"kind": "upstream", "source_id": source.source_id, "revision": revision, "upstream_path": upstream_path}


def _stage_catalog(tx: RepositoryTransaction) -> None:
    content = _catalog_content(tx.candidate_root)
    tx.write_text(Path("registry/catalog.json"), content)


def _catalog_content(root: Path) -> str:
    if not _has_governance(root):
        return json.dumps({"schema_version": 1, "skills": []}, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        catalog = build_catalog(root)
    except RegistryValidationError as error:
        raise SourceMaintenanceError(str(error)) from error
    return json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _validate_candidate(root: Path) -> None:
    sources = _load_sources_with_paths(root)
    records = _collect_catalog_records(root)
    by_id = {item["id"]: item for item in records}
    governance_sources = _governance_sources(root, records)
    expected: dict[str, tuple[UpstreamSource, ResolvedSourceSkill]] = {}
    for _, source in sources:
        for item in source.skills:
            if item.skill_id in expected:
                raise SourceMaintenanceError(f"duplicate resolved Skill id: {item.skill_id}")
            expected[item.skill_id] = (source, item)
            record = by_id.get(item.skill_id)
            if record is None:
                raise SourceMaintenanceError(f"resolved Skill payload is missing: {item.skill_id}")
            provenance = governance_sources[item.skill_id]
            if record["source_kind"] != "upstream" or provenance.get("kind") != "upstream":
                raise SourceMaintenanceError(f"resolved Skill provenance mismatch: {item.skill_id}")
            if (
                record["source_id"] != source.source_id
                or record["revision"] != source.resolved_revision
                or provenance.get("source_id") != source.source_id
                or provenance.get("revision") != source.resolved_revision
            ):
                raise SourceMaintenanceError(f"resolved Skill metadata mismatch: {item.skill_id}")
            if provenance.get("upstream_path") != item.path:
                raise SourceMaintenanceError(f"resolved Skill upstream path mismatch: {item.skill_id}")
            if (
                record["content_digest"] != item.content_digest
                or record["layer"] != item.layer
                or record["path"] != _skill_destination(item.layer, item.skill_id).as_posix()
            ):
                raise SourceMaintenanceError(f"resolved Skill location mismatch: {item.skill_id}")
    for record in records:
        if record["source_kind"] == "upstream" and record["id"] not in expected:
            raise SourceMaintenanceError(f"upstream Skill is not resolved: {record['id']}")
    for profile in sorted((root / "profiles").glob("*.yaml")) if (root / "profiles").is_dir() else ():
        data = _load_yaml(profile)
        if not isinstance(data, dict) or data.get("id") != profile.stem or not isinstance(data.get("skills"), list):
            raise SourceMaintenanceError(f"invalid Profile: {profile.name}")
        if len(data["skills"]) != len(set(data["skills"])):
            raise SourceMaintenanceError(f"duplicate Profile Skill: {profile.stem}")
        missing = [item for item in data["skills"] if item not in by_id]
        if missing:
            raise SourceMaintenanceError(f"unknown Skill in Profile {profile.stem}: {', '.join(missing)}")
    catalog = root / "registry/catalog.json"
    if not catalog.is_file() or catalog.read_text(encoding="utf-8") != _catalog_content(root):
        raise SourceMaintenanceError("catalog is not consistent with candidate Skills")


def _collect_catalog_records(root: Path) -> list[dict[str, object]]:
    if not _has_governance(root):
        return []
    try:
        return [dict(item) for item in build_catalog(root)["skills"]]
    except RegistryValidationError as error:
        raise SourceMaintenanceError(str(error)) from error


def _has_governance(root: Path) -> bool:
    skills_root = root / "skills-src"
    return skills_root.is_dir() and next(skills_root.glob("**/skill.yaml"), None) is not None


def _governance_sources(root: Path, records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    provenance: dict[str, dict[str, object]] = {}
    for record in records:
        skill_id = str(record["id"])
        data = _load_governance(root / str(record["path"]) / "skill.yaml")
        source = data.get("source")
        if not isinstance(source, dict) or source.get("kind") not in {"manual", "upstream"}:
            raise SourceMaintenanceError(f"invalid source kind: {skill_id}")
        if source["kind"] == "manual" and set(source) != {"kind"}:
            raise SourceMaintenanceError(f"invalid manual source kind: {skill_id}")
        if source["kind"] == "upstream" and set(source) != {"kind", "source_id", "revision", "upstream_path"}:
            raise SourceMaintenanceError(f"invalid upstream source kind: {skill_id}")
        provenance[skill_id] = source
    return provenance


def _load_sources_with_paths(root: Path) -> tuple[tuple[Path, UpstreamSource], ...]:
    sources_dir = root / "sources"
    if not sources_dir.is_dir():
        return ()
    loaded = tuple((path, load_source_manifest(path)) for path in sorted(sources_dir.glob("*.yaml")))
    ids = [source.source_id for _, source in loaded]
    if len(ids) != len(set(ids)):
        raise SourceMaintenanceError("duplicate source id")
    return loaded


def _find_source(root: Path, source_id: str) -> tuple[Path, UpstreamSource]:
    for item in _load_sources_with_paths(root):
        if item[1].source_id == source_id:
            return item
    raise SourceMaintenanceError(f"unknown source id: {source_id}")


def _profile_references(root: Path, skill_ids: set[str]) -> tuple[str, ...]:
    matches: list[str] = []
    for path in sorted((root / "profiles").glob("*.yaml")) if (root / "profiles").is_dir() else ():
        data = _load_yaml(path)
        if isinstance(data, dict) and isinstance(data.get("skills"), list) and skill_ids & set(data["skills"]):
            matches.append(path.stem)
    return tuple(matches)


def _remove_source_skills_from_profiles(tx: RepositoryTransaction, skill_ids: set[str]) -> None:
    profiles = tx.candidate_root / "profiles"
    if not profiles.is_dir():
        return
    for path in sorted(profiles.glob("*.yaml")):
        data = _load_yaml(path)
        if isinstance(data, dict) and isinstance(data.get("skills"), list):
            remaining = [item for item in data["skills"] if item not in skill_ids]
            if remaining != data["skills"]:
                data["skills"] = remaining
                tx.write_text(path.relative_to(tx.candidate_root), _yaml(data))


def _assert_new_ids_available(root: Path, skill_ids: object) -> None:
    values = tuple(skill_ids)
    if len(values) != len(set(values)):
        raise SourceMaintenanceError("duplicate inferred Skill id")
    existing = {item["id"] for item in _collect_catalog_records(root)}
    collisions = sorted(existing & set(values))
    if collisions:
        raise SourceMaintenanceError(f"Skill id already exists: {', '.join(collisions)}")


def _is_manual_skill(root: Path, skill_id: str) -> bool:
    for record in _collect_catalog_records(root):
        if record["id"] == skill_id:
            return record["source_kind"] == "manual"
    return False


def _combine_summaries(operation: str, summaries: list[MaintenanceSummary]) -> MaintenanceSummary:
    return MaintenanceSummary(
        operation=operation,
        source_ids=tuple(sorted(source for summary in summaries for source in summary.source_ids)),
        added_skill_ids=tuple(sorted(skill for summary in summaries for skill in summary.added_skill_ids)),
        updated_skill_ids=tuple(sorted(skill for summary in summaries for skill in summary.updated_skill_ids)),
        removed_skill_ids=tuple(sorted(skill for summary in summaries for skill in summary.removed_skill_ids)),
        manualized_skill_ids=tuple(sorted(skill for summary in summaries for skill in summary.manualized_skill_ids)),
        affected_profile_ids=tuple(sorted(profile for summary in summaries for profile in summary.affected_profile_ids)),
        affected_test_paths=tuple(sorted(path for summary in summaries for path in summary.affected_test_paths)),
        source_details=tuple(sorted((detail for summary in summaries for detail in summary.source_details), key=lambda detail: str(detail["source_id"]))),
    )


def _assert_safe_payload(source: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise SourceMaintenanceError("upstream Skill payload must be a real directory")
    for path in source.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise SourceMaintenanceError(f"unsafe upstream payload entry: {path.name}")


def _skill_destination(layer: str, skill_id: str) -> Path:
    if not isinstance(layer, str) or not layer or "/" in layer or "\\" in layer:
        raise SourceMaintenanceError("invalid Skill layer")
    parts = skill_id.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise SourceMaintenanceError("Skill id must be <namespace>/<name>")
    return Path("skills-src") / layer / parts[0] / parts[1]


def _infer_skill_id(defaults: SourceDefaults, item: DiscoveredSkill) -> str:
    return f"{defaults.namespace}/{item.name}"


def _safe_source_path(path: str) -> str:
    if not isinstance(path, str) or not path or path != path.strip() or "\\" in path:
        raise SourceMaintenanceError("source path must be a safe relative path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or path in {".", ".."} or ".." in parsed.parts:
        raise SourceMaintenanceError("source path must be a safe relative path")
    return path


def _unique_paths(paths: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(_safe_source_path(item) for item in paths)
    if len(normalized) != len(set(normalized)):
        raise SourceMaintenanceError(f"duplicate {label} path")
    return normalized


def _validate_source_id(source_id: str) -> None:
    if not isinstance(source_id, str) or not source_id or source_id != source_id.strip() or any(char in source_id for char in "/\\") or source_id in {".", ".."}:
        raise SourceMaintenanceError("source id must be safe for a source manifest filename")


def _validate_policies(policies: UpdatePolicies) -> None:
    if policies.on_added not in {"include", "ignore", "fail"} or policies.on_removed not in {"remove", "manualize", "fail"}:
        raise SourceMaintenanceError("invalid update policy")


def _load_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SourceMaintenanceError(f"cannot read YAML {path}: {error}") from error


def _load_governance(path: Path) -> dict[str, object]:
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise SourceMaintenanceError(f"invalid governance: {path}")
    return dict(data)


def _yaml(data: object) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
