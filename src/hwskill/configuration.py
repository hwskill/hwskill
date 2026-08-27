from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


START = "# >>> hwskill managed codex >>>"
END = "# <<< hwskill managed codex <<<"


@dataclass(frozen=True)
class SetupResult:
    config_path: Path
    changed: bool


def _managed_block(registry_root: Path) -> str:
    root = str(registry_root.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    return (
        f"{START}\n"
        "[mcp_servers.hwskill]\n"
        'command = "hwskill"\n'
        f'args = ["serve-mcp", "--repo-root", "{root}"]\n'
        "required = true\n\n"
        "[[hooks.SessionStart]]\n"
        'matcher = "startup|resume|clear|compact"\n'
        "[[hooks.SessionStart.hooks]]\n"
        'type = "command"\n'
        f'command = "hwskill adapter codex session-start --repo-root {root}"\n'
        "additionalContextLimit = 5000\n"
        f"{END}\n"
    )


def setup_codex(project: Path, registry_root: Path) -> SetupResult:
    config = project / ".codex/config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    block = _managed_block(registry_root)
    if START in existing:
        before, rest = existing.split(START, 1)
        _, after = rest.split(END, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip("\n")
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    changed = updated != existing
    if changed:
        config.write_text(updated, encoding="utf-8")
    state = project / ".hwskills/state/setup-codex.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "schema_version": 1,
        "config": str(config.relative_to(project)),
        "managed_digest": "sha256:" + hashlib.sha256(block.encode()).hexdigest(),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return SetupResult(config, changed)
