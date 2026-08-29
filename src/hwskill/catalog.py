from __future__ import annotations

from pathlib import Path

from .audit import AuditEvent, AuditWriter
from .configuration import (
    claude_setup_is_current,
    codex_setup_is_current,
    opencode_setup_is_current,
)
from .hosts import canonical_host
from .profiles import resolve_profiles
from .scopes import ScopeTarget, project_scope


def should_suppress_user_adapter(host: str, project: Path) -> bool:
    target = project_scope(project)
    checks = {
        "codex": codex_setup_is_current,
        "claude-code": claude_setup_is_current,
        "opencode": opencode_setup_is_current,
    }
    try:
        return checks[canonical_host(host)](target)
    except (OSError, ValueError):
        return False


def render_effective_catalog(
    project: Path,
    registry_root: Path,
    audit: AuditWriter,
    session_id: str | None = None,
    *,
    user_target: ScopeTarget | None = None,
) -> str:
    resolved_project = project.resolve()
    catalog = resolve_profiles(
        resolved_project,
        registry_root.resolve(),
        user_target=user_target,
    )
    lines = [
        "hwskill Effective Skill Catalog",
        f"catalog_digest: {catalog.catalog_digest}",
        "强制工作流：即使目录中已有匹配 ID，也必须先调用 hwskill_search；"
        "只可在搜索返回后调用 hwskill_load。不要调用宿主原生 Skill 工具打开这些 ID。",
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
