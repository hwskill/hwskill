from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .audit import AuditWriter
from .catalog import render_effective_catalog, should_suppress_user_adapter
from .scopes import ScopeTarget


def session_start(
    input_data: Mapping[str, Any],
    registry_root: Path,
    audit: AuditWriter,
    *,
    scope: str = "project",
    user_target: ScopeTarget | None = None,
) -> dict[str, Any]:
    project = Path(str(input_data.get("cwd", ".")))
    if scope == "user" and should_suppress_user_adapter("claude-code", project):
        return {}
    context = render_effective_catalog(
        project,
        registry_root,
        audit,
        session_id=(
            str(input_data["session_id"]) if input_data.get("session_id") else None
        ),
        user_target=user_target,
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }


def run_session_start(
    registry_root: Path,
    audit_path: Path,
    input_text: str,
    *,
    scope: str = "project",
    user_target: ScopeTarget | None = None,
) -> str:
    data = json.loads(input_text)
    project = Path(str(data.get("cwd", ".")))
    if scope == "user" and should_suppress_user_adapter("claude-code", project):
        return ""
    return json.dumps(
        session_start(
            data,
            registry_root,
            AuditWriter(audit_path),
            scope=scope,
            user_target=user_target,
        ),
        ensure_ascii=False,
    )
