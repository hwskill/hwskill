from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile

import yaml

from .digest import content_digest
from .frontmatter import parse_skill_markdown
from .models import SkillRecord, SourceSpec


class SkillImportError(ValueError):
    pass


class ImportConflictError(SkillImportError):
    pass


def _validate_source_tree(skill_dir: Path) -> None:
    root = skill_dir.resolve()
    for path in skill_dir.rglob("*"):
        if path.is_symlink():
            resolved = path.resolve()
            if root != resolved and root not in resolved.parents:
                raise SkillImportError(f"symlink is outside the skill directory: {path}")
        elif not path.is_file() and not path.is_dir():
            raise SkillImportError(f"unsupported special file: {path}")


def import_source(
    spec: SourceSpec,
    repo_root: Path,
    update: bool = False,
    imported_at: str | None = None,
) -> list[SkillRecord]:
    if spec.kind != "local":
        raise SkillImportError(f"unsupported source kind: {spec.kind}")
    timestamp = imported_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records: list[SkillRecord] = []
    for selected in spec.skills:
        source = spec.root / selected.name
        if not (source / "SKILL.md").is_file():
            raise SkillImportError(f"missing SKILL.md: {source}")
        _validate_source_tree(source)
        metadata, _ = parse_skill_markdown((source / "SKILL.md").read_text(encoding="utf-8"))
        if "x-hwskill-runtime" in metadata:
            raise SkillImportError("reserved frontmatter key x-hwskill-runtime")
        if metadata["name"] != selected.name:
            raise SkillImportError(f"directory and skill name differ: {selected.name}")

        target = repo_root / "skills-src" / selected.layer / spec.namespace / selected.name
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{selected.name}-", dir=target.parent))
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=False)
            digest = content_digest(staging)
            governance = {
                "schema_version": 1,
                "id": f"{spec.namespace}/{selected.name}",
                "name": selected.name,
                "description": metadata["description"],
                "layer": selected.layer,
                "status": "experimental",
                "source": {
                    "source_id": spec.source_id,
                    "revision": spec.revision,
                    "upstream_path": selected.name,
                    "imported_at": timestamp,
                    **({"upstream_url": spec.upstream_url} if spec.upstream_url else {}),
                },
                "license": spec.license,
                "content_digest": digest,
            }
            (staging / "skill.yaml").write_text(
                yaml.safe_dump(governance, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            if target.exists():
                if content_digest(target) == digest:
                    shutil.rmtree(staging)
                elif not update:
                    raise ImportConflictError(f"{target} differs; pass --update to replace it")
                else:
                    backup = target.with_name(f".{target.name}.backup")
                    if backup.exists():
                        shutil.rmtree(backup)
                    os.replace(target, backup)
                    try:
                        os.replace(staging, target)
                    except BaseException:
                        os.replace(backup, target)
                        raise
                    shutil.rmtree(backup)
            else:
                os.replace(staging, target)
            records.append(SkillRecord(
                skill_id=f"{spec.namespace}/{selected.name}",
                name=selected.name,
                description=str(metadata["description"]),
                layer=selected.layer,
                source_id=spec.source_id,
                revision=spec.revision,
                license=spec.license,
                content_digest=digest,
                path=target,
            ))
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return records
