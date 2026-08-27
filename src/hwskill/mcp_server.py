from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .audit import AuditEvent, AuditWriter
from .loader import load_skill
from .profiles import resolve_profiles
from .search import search_skills


@dataclass(frozen=True)
class RuntimeToolResult:
    text: str
    structured: dict[str, Any]


class HwskillMcpRuntime:
    def __init__(self, project: Path, registry_root: Path, audit: AuditWriter):
        self.project = project.resolve()
        self.registry_root = registry_root.resolve()
        self.audit = audit

    async def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        catalog = resolve_profiles(self.project, self.registry_root)
        results = search_skills(catalog, query, limit)
        self.audit.write(AuditEvent(
            event="search", result="ok", cwd=str(self.project),
            profile_ids=catalog.profile_ids, catalog_digest=catalog.catalog_digest,
        ))
        return {"catalog_digest": catalog.catalog_digest,
                "results": [asdict(item) for item in results]}

    async def load(self, skill_id: str, expected_digest: str | None = None) -> RuntimeToolResult:
        catalog = resolve_profiles(self.project, self.registry_root)
        loaded = load_skill(catalog, skill_id, expected_digest)
        skill_dir = str(Path(loaded.skill_file).parent)
        structured = {
            "id": loaded.skill_id,
            "revision": loaded.revision,
            "content_digest": loaded.content_digest,
            "skill_dir": skill_dir,
            "skill_file": loaded.skill_file,
        }
        self.audit.write(AuditEvent(
            event="load", result="ok", cwd=str(self.project),
            profile_ids=catalog.profile_ids, catalog_digest=catalog.catalog_digest,
            skill_id=loaded.skill_id, revision=loaded.revision,
            content_digest=loaded.content_digest,
        ))
        return RuntimeToolResult(loaded.content, structured)


def run_server(project: Path, registry_root: Path, audit_path: Path) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("install the mcp dependency to run serve-mcp") from exc

    runtime = HwskillMcpRuntime(project, registry_root, AuditWriter(audit_path))
    server = FastMCP("hwskill")

    @server.tool()
    async def hwskill_search(query: str, limit: int = 10) -> dict[str, Any]:
        """Search the current project's Effective Skill Catalog."""
        return await runtime.search(query, limit)

    @server.tool()
    async def hwskill_load(skill_id: str, expected_digest: str | None = None) -> dict[str, Any]:
        """Load one allowed Skill with runtime path metadata."""
        result = await runtime.load(skill_id, expected_digest)
        return {"content": result.text, **result.structured}

    server.run(transport="stdio")
