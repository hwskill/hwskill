from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Sequence

import yaml

from .models import EffectiveCatalog, SkillRecord
from .scopes import ScopeTarget, lock_path, profile_path, project_scope, user_scope


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileDefinition:
    profile_id: str
    description: str
    skill_ids: tuple[str, ...]


def parse_csv(value: str, label: str) -> tuple[str, ...]:
    members = [member.strip() for member in value.split(",")]
    if not members or any(not member for member in members):
        raise ProfileError(f"empty {label} selector")
    return tuple(dict.fromkeys(members))


def list_profiles(registry_root: Path) -> tuple[ProfileDefinition, ...]:
    definitions = []
    for path in sorted((registry_root / "profiles").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not data.get("id"):
            raise ProfileError(f"invalid profile definition: {path}")
        skills = data.get("skills", ())
        if not isinstance(skills, (list, tuple)):
            raise ProfileError(f"invalid profile skills: {path}")
        definitions.append(
            ProfileDefinition(
                profile_id=str(data["id"]),
                description=str(data.get("description", "")),
                skill_ids=tuple(str(item) for item in skills),
            )
        )
    return tuple(definitions)


def read_profile_ids(target: ScopeTarget) -> tuple[str, ...] | None:
    path = profile_path(target)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ProfileError(f"invalid profile mapping: {path}")
    values = data.get("profiles", ())
    if not isinstance(values, (list, tuple)):
        raise ProfileError(f"invalid profiles list: {path}")
    profile_ids = tuple(str(item) for item in values)
    if any(not profile_id for profile_id in profile_ids):
        raise ProfileError(f"invalid empty profile id: {path}")
    return tuple(dict.fromkeys(profile_ids))


def _catalog_for_profile_ids(
    project: Path,
    registry_root: Path,
    profile_ids: Sequence[str],
    *,
    effective_scope: str | None,
    source: Path | None,
) -> EffectiveCatalog:
    definitions = {item.profile_id: item for item in list_profiles(registry_root)}
    selected_ids: set[str] = set()
    normalized_ids = tuple(dict.fromkeys(str(item) for item in profile_ids))
    for profile_id in normalized_ids:
        definition = definitions.get(profile_id)
        if definition is None:
            raise ProfileError(f"unknown profile: {profile_id}")
        selected_ids.update(definition.skill_ids)

    catalog_data = json.loads(
        (registry_root / "registry/catalog.json").read_text(encoding="utf-8")
    )
    by_id = {item["id"]: item for item in catalog_data["skills"]}
    records = []
    for skill_id in sorted(selected_ids):
        if skill_id not in by_id:
            raise ProfileError(f"unknown skill in profile: {skill_id}")
        item = by_id[skill_id]
        records.append(
            SkillRecord(
                skill_id=skill_id,
                name=item["name"],
                description=item["description"],
                layer=item["layer"],
                source_id=item["source_id"],
                revision=item["revision"],
                license=item["license"],
                content_digest=item["content_digest"],
                path=registry_root / item["path"],
            )
        )
    digest_input = json.dumps(
        [(item.skill_id, item.content_digest) for item in records],
        separators=(",", ":"),
    ).encode()
    return EffectiveCatalog(
        project=project,
        registry_root=registry_root,
        profile_ids=normalized_ids,
        skills=tuple(records),
        catalog_digest="sha256:" + hashlib.sha256(digest_input).hexdigest(),
        effective_scope=effective_scope,
        profile_source=source,
    )


def _lock_data(catalog: EffectiveCatalog) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "catalog_digest": catalog.catalog_digest,
        "skills": [
            {
                "id": item.skill_id,
                "revision": item.revision,
                "content_digest": item.content_digest,
            }
            for item in catalog.skills
        ],
    }


def _write_yaml_pair(
    first_path: Path,
    first_data: dict[str, Any],
    second_path: Path,
    second_data: dict[str, Any],
) -> None:
    first_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_paths = []
    try:
        for final_path, data in ((first_path, first_data), (second_path, second_data)):
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=final_path.parent,
                prefix=f".{final_path.name}.",
                delete=False,
            ) as temporary:
                yaml.safe_dump(data, temporary, allow_unicode=True, sort_keys=False)
                temporary_paths.append(Path(temporary.name))
        temporary_paths[0].replace(first_path)
        temporary_paths[1].replace(second_path)
    finally:
        for path in temporary_paths:
            if path.exists():
                path.unlink()


def set_profiles(
    target: ScopeTarget,
    registry_root: Path,
    profile_ids: Sequence[str],
) -> EffectiveCatalog:
    project = target.project_root or target.config_root
    catalog = _catalog_for_profile_ids(
        project,
        registry_root,
        profile_ids,
        effective_scope=target.kind,
        source=profile_path(target),
    )
    _write_yaml_pair(
        profile_path(target),
        {"schema_version": 1, "profiles": list(catalog.profile_ids)},
        lock_path(target),
        _lock_data(catalog),
    )
    return catalog


def unset_profiles(target: ScopeTarget) -> bool:
    paths = (profile_path(target), lock_path(target))
    existed = any(path.exists() for path in paths)
    for path in paths:
        if path.exists():
            path.unlink()
    return existed


def _verify_lock(target: ScopeTarget, catalog: EffectiveCatalog) -> None:
    path = lock_path(target)
    if not path.is_file():
        return
    lock = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    expected = _lock_data(catalog)
    actual = {
        "schema_version": lock.get("schema_version") if isinstance(lock, dict) else None,
        "catalog_digest": lock.get("catalog_digest") if isinstance(lock, dict) else None,
        "skills": lock.get("skills") if isinstance(lock, dict) else None,
    }
    if actual != expected:
        raise ProfileError("lock does not match the resolved profile; set the profile again")


def resolve_profiles(
    project: Path,
    registry_root: Path,
    verify_lock: bool = True,
    *,
    user_target: ScopeTarget | None = None,
) -> EffectiveCatalog:
    resolved_project = project.resolve()
    project_target = project_scope(resolved_project)
    project_ids = read_profile_ids(project_target)
    if project_ids is not None:
        target = project_target
        profile_ids = project_ids
    else:
        target = user_target or user_scope()
        profile_ids = read_profile_ids(target)

    if profile_ids is None:
        return EffectiveCatalog(
            resolved_project,
            registry_root,
            (),
            (),
            "sha256:" + hashlib.sha256(b"").hexdigest(),
        )

    catalog = _catalog_for_profile_ids(
        resolved_project,
        registry_root,
        profile_ids,
        effective_scope=target.kind,
        source=profile_path(target),
    )
    if verify_lock:
        _verify_lock(target, catalog)
    return catalog


def resolve_profile_ids(project: Path) -> list[str]:
    return list(read_profile_ids(project_scope(project)) or ())


def bind_profile(project: Path, registry_root: Path, profile_id: str) -> EffectiveCatalog:
    target = project_scope(project)
    profile_ids = list(read_profile_ids(target) or ())
    if profile_id not in profile_ids:
        profile_ids.append(profile_id)
    return set_profiles(target, registry_root, profile_ids)


def unbind_profile(project: Path, registry_root: Path, profile_id: str) -> EffectiveCatalog:
    target = project_scope(project)
    profile_ids = [
        existing
        for existing in (read_profile_ids(target) or ())
        if existing != profile_id
    ]
    return set_profiles(target, registry_root, profile_ids)
