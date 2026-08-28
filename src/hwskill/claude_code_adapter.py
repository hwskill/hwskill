from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .audit import AuditWriter
from .catalog import render_effective_catalog


def session_start(
    input_data: Mapping[str, Any],
    registry_root: Path,
    audit: AuditWriter,
) -> dict[str, Any]:
    context = render_effective_catalog(
        Path(str(input_data.get("cwd", "."))),
        registry_root,
        audit,
        session_id=(
            str(input_data["session_id"]) if input_data.get("session_id") else None
        ),
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }


def run_session_start(registry_root: Path, audit_path: Path, input_text: str) -> str:
    return json.dumps(
        session_start(json.loads(input_text), registry_root, AuditWriter(audit_path)),
        ensure_ascii=False,
    )
