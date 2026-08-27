from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Any

from .audit import AuditEvent, AuditWriter
from .profiles import resolve_profiles


def session_start(
    input_data: Mapping[str, Any],
    registry_root: Path,
    audit: AuditWriter,
) -> dict[str, Any]:
    project = Path(str(input_data.get("cwd", "."))).resolve()
    catalog = resolve_profiles(project, registry_root.resolve())
    lines = [
        "hwskill Effective Skill Catalog",
        f"catalog_digest: {catalog.catalog_digest}",
        "仅通过 hwskill_search 搜索候选，并用 hwskill_load 按需加载完整技能。",
        "Skills:",
    ]
    lines.extend(f"- {item.skill_id}: {item.description}" for item in catalog.skills)
    audit.write(AuditEvent(
        event="catalog", result="ok",
        session_id=str(input_data.get("session_id")) if input_data.get("session_id") else None,
        cwd=str(project), profile_ids=catalog.profile_ids,
        catalog_digest=catalog.catalog_digest,
    ))
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }


def run_session_start(registry_root: Path, audit_path: Path, input_text: str) -> str:
    data = json.loads(input_text)
    return json.dumps(
        session_start(data, registry_root, AuditWriter(audit_path)),
        ensure_ascii=False,
    )
