from __future__ import annotations

from pathlib import Path

from .audit import AuditWriter
from .catalog import render_effective_catalog, should_suppress_user_adapter
from .scopes import ScopeTarget


def render_catalog(
    project: Path,
    registry_root: Path,
    audit: AuditWriter,
    session_id: str | None = None,
    *,
    scope: str = "project",
    user_target: ScopeTarget | None = None,
) -> str:
    if scope == "user" and should_suppress_user_adapter("opencode", project):
        return ""
    return render_effective_catalog(
        project,
        registry_root,
        audit,
        session_id=session_id,
        user_target=user_target,
    )
