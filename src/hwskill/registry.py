from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .digest import content_digest
from .frontmatter import parse_skill_markdown
from .models import SkillRecord


class RegistryValidationError(ValueError):
    pass


def validate_registry(repo_root: Path) -> list[SkillRecord]:
    records: list[SkillRecord] = []
    seen: set[str] = set()
    for governance_path in sorted((repo_root / "skills-src").glob("**/skill.yaml")):
        skill_dir = governance_path.parent
        data = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RegistryValidationError(f"invalid governance mapping: {governance_path}")
        skill_id = str(data.get("id", ""))
        if not skill_id or skill_id in seen:
            raise RegistryValidationError(f"duplicate or missing id: {skill_id}")
        seen.add(skill_id)
        markdown = skill_dir / "SKILL.md"
        if not markdown.is_file():
            raise RegistryValidationError(f"missing SKILL.md: {skill_dir}")
        metadata, _ = parse_skill_markdown(markdown.read_text(encoding="utf-8"))
        if "x-hwskill-runtime" in metadata:
            raise RegistryValidationError(f"reserved x-hwskill-runtime: {skill_id}")
        if metadata["name"] != data.get("name") or skill_dir.name != data.get("name"):
            raise RegistryValidationError(f"directory/name mismatch: {skill_id}")
        actual = content_digest(skill_dir)
        if actual != data.get("content_digest"):
            raise RegistryValidationError(
                f"content_digest mismatch for {skill_id}: {data.get('content_digest')} != {actual}"
            )
        source_kind, source_id, revision = _parse_provenance(data.get("source"), skill_id)
        records.append(SkillRecord(
            skill_id=skill_id,
            name=str(data["name"]),
            description=str(data.get("description", metadata["description"])),
            layer=str(data["layer"]),
            source_kind=source_kind,
            source_id=source_id,
            revision=revision,
            license=str(data["license"]),
            content_digest=actual,
            path=skill_dir,
        ))
    if not records:
        raise RegistryValidationError("no skills found")
    for profile_path in sorted((repo_root / "profiles").glob("*.yaml")):
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or profile.get("id") != profile_path.stem:
            raise RegistryValidationError(f"invalid profile id: {profile_path}")
        skill_ids = profile.get("skills")
        if not isinstance(skill_ids, list) or len(skill_ids) != len(set(skill_ids)):
            raise RegistryValidationError(f"invalid or duplicate profile skills: {profile_path.stem}")
        for skill_id in skill_ids:
            if skill_id not in seen:
                raise RegistryValidationError(
                    f"unknown skill in profile {profile_path.stem}: {skill_id}"
                )
    return sorted(records, key=lambda item: item.skill_id)


def _parse_provenance(source: object, skill_id: str) -> tuple[str, str | None, str]:
    if not isinstance(source, dict):
        raise RegistryValidationError(f"invalid source kind: {skill_id}")
    kind = source.get("kind")
    if kind == "manual":
        if set(source) != {"kind"}:
            raise RegistryValidationError(f"invalid manual source kind: {skill_id}")
        return "manual", None, "manual"
    if kind != "upstream":
        raise RegistryValidationError(f"invalid source kind: {skill_id}")
    if set(source) != {"kind", "source_id", "revision", "upstream_path"}:
        raise RegistryValidationError(f"invalid upstream source kind: {skill_id}")
    source_id = source.get("source_id")
    revision = source.get("revision")
    upstream_path = source.get("upstream_path")
    if (
        not isinstance(source_id, str) or not source_id.strip()
        or not isinstance(revision, str) or not revision.strip() or revision == "manual"
        or not isinstance(upstream_path, str) or not upstream_path.strip()
    ):
        raise RegistryValidationError(f"invalid upstream source kind: {skill_id}")
    return "upstream", source_id, revision


def build_catalog(repo_root: Path) -> dict[str, Any]:
    records = validate_registry(repo_root)
    return {
        "schema_version": 2,
        "skills": [
            {
                "id": item.skill_id,
                "name": item.name,
                "description": item.description,
                "layer": item.layer,
                "source_kind": item.source_kind,
                "source_id": item.source_id,
                "revision": item.revision,
                "license": item.license,
                "content_digest": item.content_digest,
                "path": item.path.relative_to(repo_root).as_posix(),
            }
            for item in records
        ],
    }


def write_catalog(repo_root: Path, check: bool = False) -> bool:
    content = json.dumps(build_catalog(repo_root), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = repo_root / "registry/catalog.json"
    if check:
        return path.is_file() and path.read_text(encoding="utf-8") == content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
