from __future__ import annotations

from pathlib import Path

from .audit import AuditEvent, AuditWriter
from .profiles import resolve_profiles


def render_effective_catalog(
    project: Path,
    registry_root: Path,
    audit: AuditWriter,
    session_id: str | None = None,
) -> str:
    resolved_project = project.resolve()
    catalog = resolve_profiles(resolved_project, registry_root.resolve())
    lines = [
        "hwskill Effective Skill Catalog",
        f"catalog_digest: {catalog.catalog_digest}",
        "仅通过 hwskill_search 搜索候选，并用 hwskill_load 按需加载完整技能。",
        "Skills:",
    ]
    lines.extend(f"- {item.skill_id}: {item.description}" for item in catalog.skills)
    audit.write(AuditEvent(
        event="catalog",
        result="ok",
        session_id=session_id,
        cwd=str(resolved_project),
        profile_ids=catalog.profile_ids,
        catalog_digest=catalog.catalog_digest,
    ))
    return "\n".join(lines)
