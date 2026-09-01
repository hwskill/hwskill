from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import yaml

from .digest import content_digest
from .frontmatter import parse_skill_markdown
from .models import SkillRecord


class RegistryValidationError(ValueError):
    pass


_SHA1_RE = re.compile(r"[0-9a-fA-F]{40}")


def validate_registry(repo_root: Path) -> list[SkillRecord]:
    records: list[SkillRecord] = []
    seen: set[str] = set()
    for governance_path in sorted((repo_root / "skills-src").glob("**/skill.yaml")):
        record = validate_skill(repo_root, governance_path)
        skill_id = record.skill_id
        if not skill_id or skill_id in seen:
            raise RegistryValidationError(f"duplicate or missing id: {skill_id}")
        seen.add(skill_id)
        records.append(record)
    if not records:
        raise RegistryValidationError("no skills found")
    for profile_path in sorted((repo_root / "profiles").glob("*.yaml")):
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or profile.get("id") != profile_path.stem:
            raise RegistryValidationError(f"invalid profile id: {profile_path}")
        skill_ids = profile.get("skills")
        if not isinstance(skill_ids, list) or len(skill_ids) != len(set(skill_ids)):
            raise RegistryValidationError(f"invalid or duplicate profile skills: {profile_path.stem}")
        for profile_skill_id in skill_ids:
            if profile_skill_id not in seen:
                raise RegistryValidationError(
                    f"unknown skill in profile {profile_path.stem}: {profile_skill_id}"
                )
    return sorted(records, key=lambda item: item.skill_id)


def validate_skill(repo_root: Path, governance_path: Path) -> SkillRecord:
    """Validate one governed Skill without consulting sources, Catalog, or Profiles."""
    skill_dir = governance_path.parent
    try:
        data = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RegistryValidationError(f"cannot read governance: {governance_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryValidationError(f"invalid governance mapping: {governance_path}")
    if data.get("schema_version") != 1:
        raise RegistryValidationError(f"unsupported governance schema_version: {governance_path}")
    skill_id = data.get("id")
    name = data.get("name")
    layer = data.get("layer")
    if not all(isinstance(value, str) and value.strip() == value and value for value in (skill_id, name, layer)):
        raise RegistryValidationError(f"invalid Skill identity: {governance_path}")
    _validate_physical_path(repo_root, skill_dir, skill_id, layer, name)
    markdown = skill_dir / "SKILL.md"
    if not markdown.is_file():
        raise RegistryValidationError(f"missing SKILL.md: {skill_dir}")
    try:
        metadata, _ = parse_skill_markdown(markdown.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RegistryValidationError(f"invalid SKILL.md: {skill_id}: {exc}") from exc
    if "x-hwskill-runtime" in metadata:
        raise RegistryValidationError(f"reserved x-hwskill-runtime: {skill_id}")
    if metadata["name"] != name:
        raise RegistryValidationError(f"frontmatter/name mismatch: {skill_id}")
    actual = content_digest(skill_dir)
    if actual != data.get("content_digest"):
        raise RegistryValidationError(
            f"content_digest mismatch for {skill_id}: {data.get('content_digest')} != {actual}"
        )
    source_kind, source_id, revision = _parse_provenance(data.get("source"), skill_id)
    description = data.get("description", metadata["description"])
    license_name = data.get("license")
    if not isinstance(description, str) or not isinstance(license_name, str) or not license_name.strip():
        raise RegistryValidationError(f"invalid governance metadata: {skill_id}")
    return SkillRecord(
        skill_id=skill_id,
        name=name,
        description=description,
        layer=layer,
        source_kind=source_kind,
        source_id=source_id,
        revision=revision,
        license=license_name,
        content_digest=actual,
        path=skill_dir,
    )


def _validate_physical_path(repo_root: Path, skill_dir: Path, skill_id: str, layer: str, name: str) -> None:
    parts = skill_id.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise RegistryValidationError(f"invalid Skill id: {skill_id}")
    try:
        relative = skill_dir.relative_to(repo_root / "skills-src")
    except ValueError as exc:
        raise RegistryValidationError(f"Skill physical path escapes skills-src: {skill_id}") from exc
    expected = (layer, parts[0], name)
    if relative.parts != expected:
        raise RegistryValidationError(f"physical path mismatch: {skill_id}")


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
    if not isinstance(source_id, str) or not source_id.strip():
        raise RegistryValidationError(f"invalid upstream source kind: {skill_id}")
    if not isinstance(revision, str) or not _SHA1_RE.fullmatch(revision):
        raise RegistryValidationError(f"upstream revision must be a full 40-character Git revision: {skill_id}")
    if not isinstance(upstream_path, str) or not _safe_upstream_path(upstream_path):
        raise RegistryValidationError(f"invalid upstream path: {skill_id}")
    return "upstream", source_id, revision


def _safe_upstream_path(path: str) -> bool:
    if not path or path != path.strip() or "\\" in path:
        return False
    parts = path.split("/")
    return all(part and part not in {".", ".."} for part in parts)


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
