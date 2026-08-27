from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from .models import EffectiveCatalog, SkillRecord


class ProfileError(ValueError):
    pass


def resolve_profile_ids(project: Path) -> list[str]:
    path = project / ".hwskills/profile.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return sorted(str(item) for item in data.get("profiles", ()))


def _write_lock(project: Path, catalog: EffectiveCatalog) -> None:
    lock_path = project / ".hwskills/lock.yaml"
    lock_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "catalog_digest": catalog.catalog_digest,
        "skills": [
            {"id": item.skill_id, "revision": item.revision, "content_digest": item.content_digest}
            for item in catalog.skills
        ],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")


def bind_profile(project: Path, registry_root: Path, profile_id: str) -> EffectiveCatalog:
    profile_path = registry_root / "profiles" / f"{profile_id}.yaml"
    if not profile_path.is_file():
        raise ProfileError(f"unknown profile: {profile_id}")
    binding_path = project / ".hwskills/profile.yaml"
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(binding_path.read_text(encoding="utf-8")) if binding_path.exists() else {}
    profile_ids = list(existing.get("profiles", ()))
    if profile_id not in profile_ids:
        profile_ids.append(profile_id)
    binding_path.write_text(yaml.safe_dump({
        "schema_version": 1, "profiles": sorted(profile_ids)
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    catalog = resolve_profiles(project, registry_root, verify_lock=False)
    _write_lock(project, catalog)
    return catalog


def unbind_profile(project: Path, registry_root: Path, profile_id: str) -> EffectiveCatalog:
    profile_ids = resolve_profile_ids(project)
    if profile_id in profile_ids:
        profile_ids.remove(profile_id)
    path = project / ".hwskills/profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        "schema_version": 1, "profiles": profile_ids
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    catalog = resolve_profiles(project, registry_root, verify_lock=False)
    _write_lock(project, catalog)
    return catalog


def resolve_profiles(project: Path, registry_root: Path, verify_lock: bool = True) -> EffectiveCatalog:
    binding_path = project / ".hwskills/profile.yaml"
    if not binding_path.is_file():
        return EffectiveCatalog(project, registry_root, (), (), "sha256:" + hashlib.sha256(b"").hexdigest())
    binding = yaml.safe_load(binding_path.read_text(encoding="utf-8")) or {}
    profile_ids = tuple(binding.get("profiles", ()))
    catalog_data = json.loads((registry_root / "registry/catalog.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in catalog_data["skills"]}
    selected_ids: set[str] = set()
    for profile_id in profile_ids:
        path = registry_root / "profiles" / f"{profile_id}.yaml"
        if not path.is_file():
            raise ProfileError(f"unknown profile: {profile_id}")
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        selected_ids.update(profile.get("skills", ()))
    records = []
    for skill_id in sorted(selected_ids):
        if skill_id not in by_id:
            raise ProfileError(f"unknown skill in profile: {skill_id}")
        item = by_id[skill_id]
        records.append(SkillRecord(
            skill_id=skill_id, name=item["name"], description=item["description"],
            layer=item["layer"], source_id=item["source_id"], revision=item["revision"],
            license=item["license"], content_digest=item["content_digest"],
            path=registry_root / item["path"],
        ))
    digest_input = json.dumps(
        [(item.skill_id, item.content_digest) for item in records], separators=(",", ":")
    ).encode()
    catalog = EffectiveCatalog(
        project, registry_root, profile_ids, tuple(records),
        "sha256:" + hashlib.sha256(digest_input).hexdigest(),
    )
    lock_path = project / ".hwskills/lock.yaml"
    if verify_lock and lock_path.is_file():
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}
        expected = {
            "catalog_digest": catalog.catalog_digest,
            "skills": [
                {"id": item.skill_id, "revision": item.revision,
                 "content_digest": item.content_digest}
                for item in catalog.skills
            ],
        }
        actual = {"catalog_digest": lock.get("catalog_digest"), "skills": lock.get("skills")}
        if actual != expected:
            raise ProfileError("lock does not match the resolved profile; rebind the profile")
    return catalog
