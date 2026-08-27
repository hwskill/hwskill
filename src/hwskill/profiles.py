from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from .models import EffectiveCatalog, SkillRecord


class ProfileError(ValueError):
    pass


def resolve_profiles(project: Path, registry_root: Path) -> EffectiveCatalog:
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
    return EffectiveCatalog(
        project, registry_root, profile_ids, tuple(records),
        "sha256:" + hashlib.sha256(digest_input).hexdigest(),
    )
