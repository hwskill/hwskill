from __future__ import annotations

from pathlib import Path
from typing import Any

from .yaml_io import load_yaml_text


class RecommendationContractError(ValueError):
    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def load_recommendation(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise RecommendationContractError(
            "recommendation-frontmatter-missing",
            "$",
            "Recommendation must start with YAML Frontmatter.",
        )
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\n") == "---"),
        None,
    )
    if closing is None:
        raise RecommendationContractError(
            "recommendation-frontmatter-unclosed",
            "$",
            "Recommendation Frontmatter requires a closing delimiter.",
        )
    metadata = load_yaml_text("".join(lines[1:closing]))
    for reserved in ("body", "body_format"):
        if reserved in metadata:
            raise RecommendationContractError(
                "schema-additionalProperties",
                reserved,
                f"Recommendation Frontmatter must not define {reserved!r}.",
            )
    body = "".join(lines[closing + 1 :]).strip()
    if not body:
        raise RecommendationContractError(
            "recommendation-body-empty",
            "body",
            "Recommendation Markdown body must not be empty.",
        )
    return {**metadata, "body": body + "\n", "body_format": "markdown"}
