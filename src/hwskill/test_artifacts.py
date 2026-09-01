"""Safe, normalized artifact persistence for declarative test runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal


ActionStatus = Literal["completed", "failed", "blocked"]
CaseStatus = Literal["PASS", "FAIL", "BLOCKED"]


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: ActionStatus
    exit_code: int | None
    artifact_dir: Path


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: CaseStatus
    artifact_dir: Path
    actions: tuple[ActionResult, ...]


@dataclass(frozen=True)
class CollectionResult:
    target: Any
    status: CaseStatus
    cases: tuple[CaseResult, ...]


def safe_artifact_id(value: str) -> str:
    """Return a deterministic filesystem component without trusting manifest IDs."""
    if value and value not in {".", ".."} and all(
        character.isascii() and (character.isalnum() or character in "._-")
        for character in value
    ):
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
    return f"invalid-{digest}"


def redact_text(value: str, secret_values: tuple[str, ...]) -> str:
    """Replace configured non-empty secret values before data reaches disk."""
    redacted = value
    for secret in sorted({item for item in secret_values if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def redact_value(value: Any, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, list):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_value(item, secret_values) for key, item in value.items()}
    return value


def write_text(path: Path, value: str, secret_values: tuple[str, ...]) -> None:
    path.write_text(redact_text(value, secret_values), encoding="utf-8")


def write_json(
    path: Path,
    value: Any,
    secret_values: tuple[str, ...],
    *,
    sort_keys: bool = True,
) -> None:
    path.write_text(
        json.dumps(redact_value(value, secret_values), ensure_ascii=False, sort_keys=sort_keys, indent=2) + "\n",
        encoding="utf-8",
    )
