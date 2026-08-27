from __future__ import annotations

from typing import Any

import yaml


class FrontmatterError(ValueError):
    pass


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise FrontmatterError("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise FrontmatterError("SKILL.md frontmatter is not terminated")
    metadata = yaml.safe_load(text[4:end]) or {}
    if not isinstance(metadata, dict):
        raise FrontmatterError("SKILL.md frontmatter must be a mapping")
    for field in ("name", "description"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise FrontmatterError(f"SKILL.md frontmatter requires {field}")
    return metadata, text[end + 5 :]
