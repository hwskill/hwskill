from __future__ import annotations

from pathlib import Path
from typing import Any

from .yaml_io import load_yaml_text


class TranslationContractError(ValueError):
    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def translation_path(root: Path, skill_id: str) -> Path:
    parts = skill_id.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("Skill ID must contain one namespace and one name.")
    return root / "translations" / parts[0] / f"{parts[1]}.md"


def load_translation(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise TranslationContractError(
            "translation-frontmatter-missing",
            "$",
            "Translation must start with YAML Frontmatter.",
        )
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\n") == "---"),
        None,
    )
    if closing is None:
        raise TranslationContractError(
            "translation-frontmatter-unclosed",
            "$",
            "Translation Frontmatter requires a closing delimiter.",
        )
    metadata = load_yaml_text("".join(lines[1:closing]))
    body = "".join(lines[closing + 1 :]).strip()
    if not body:
        raise TranslationContractError(
            "translation-body-empty",
            "body",
            "Translation Markdown body must not be empty.",
        )
    return {**metadata, "body": body + "\n", "body_format": "markdown"}
