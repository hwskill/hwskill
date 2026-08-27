from __future__ import annotations

from pathlib import Path

import yaml

from .digest import content_digest
from .frontmatter import parse_skill_markdown
from .models import EffectiveCatalog, LoadedSkill


class LoadError(ValueError):
    pass


def load_skill(
    catalog: EffectiveCatalog,
    skill_id: str,
    expected_digest: str | None = None,
    raw: bool = False,
) -> LoadedSkill:
    skill = next((item for item in catalog.skills if item.skill_id == skill_id), None)
    if skill is None:
        raise LoadError(f"{skill_id} is not in the Effective Skill Catalog")
    root = catalog.registry_root.resolve()
    skill_dir = skill.path.resolve()
    if root not in skill_dir.parents:
        raise LoadError("skill path is outside the Registry root")
    actual = content_digest(skill_dir)
    if actual != skill.content_digest or (expected_digest and expected_digest != actual):
        raise LoadError("content_digest mismatch")
    skill_file = skill_dir / "SKILL.md"
    original = skill_file.read_text(encoding="utf-8")
    content = original
    if not raw:
        metadata, body = parse_skill_markdown(original)
        if "x-hwskill-runtime" in metadata:
            raise LoadError("reserved frontmatter key x-hwskill-runtime")
        metadata["x-hwskill-runtime"] = {
            "id": skill.skill_id,
            "revision": skill.revision,
            "content_digest": actual,
            "skill_dir": str(skill_dir),
            "skill_file": str(skill_file),
            "registry_root": str(root),
            "resources": {
                name: f"{name}/" if (skill_dir / name).is_dir() else None
                for name in ("scripts", "references", "assets")
            },
        }
        content = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + body
    return LoadedSkill(skill.skill_id, skill.revision, actual, str(skill_file), content)
