from __future__ import annotations

from pathlib import Path

from .audit import AuditWriter
from .catalog import render_effective_catalog


def render_catalog(
    project: Path,
    registry_root: Path,
    audit: AuditWriter,
    session_id: str | None = None,
) -> str:
    return render_effective_catalog(
        project, registry_root, audit, session_id=session_id
    )
