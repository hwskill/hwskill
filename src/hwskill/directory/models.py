from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectoryIssue:
    file: str
    field: str
    code: str
    severity: str
    message: str
    suggested_action: str


@dataclass(frozen=True)
class ValidationReport:
    schema_version: int
    source_commit: str | None
    input_digest: str
    result: str
    issues: tuple[DirectoryIssue, ...]
    publishable_entry_ids: tuple[str, ...]
    publishable_recommendation_ids: tuple[str, ...]
