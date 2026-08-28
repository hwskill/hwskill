from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .configuration import SetupResult


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be an object: {path}")
    return data


def _json_text(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_text(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _write_json(path: Path, data: dict[str, Any]) -> bool:
    return _write_text(path, _json_text(data))


def _digest_text(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def _read_state(path: Path) -> dict[str, Any] | None:
    return _read_json(path) if path.is_file() else None


def _write_state(path: Path, state: dict[str, Any]) -> None:
    _write_json(path, state)


def _mcp_args(project: Path, registry_root: Path, audit_path: Path | None) -> list[str]:
    args = [
        "serve-mcp",
        "--project", str(project.resolve()),
        "--repo-root", str(registry_root.resolve()),
    ]
    if audit_path is not None:
        args.extend(("--audit-path", str(audit_path.resolve())))
    return args


def _adapter_args(
    host: str,
    command: str,
    project: Path,
    registry_root: Path,
    audit_path: Path | None,
) -> list[str]:
    args = [
        "hwskill", "adapter", host, command,
        "--project", str(project.resolve()),
        "--repo-root", str(registry_root.resolve()),
    ]
    if audit_path is not None:
        args.extend(("--audit-path", str(audit_path.resolve())))
    return args


def _owned_value(
    existing: Any,
    state: dict[str, Any] | None,
    state_key: str,
    label: str,
) -> None:
    if existing is None:
        if state is not None:
            raise ValueError(f"managed {label} was modified outside hwskill; refusing to edit")
        return
    if state is None:
        raise ValueError(f"existing {label} is not owned by hwskill; refusing to edit")
    if existing != state.get(state_key):
        raise ValueError(f"managed {label} was modified outside hwskill; refusing to edit")


def _find_claude_hook(settings: dict[str, Any], state: dict[str, Any] | None) -> tuple[int | None, Any]:
    hooks = settings.get("hooks", {}).get("SessionStart", [])
    if not isinstance(hooks, list):
        raise ValueError("Claude hooks.SessionStart must be an array")
    owned = state.get("hook") if state else None
    if owned is not None:
        for index, hook in enumerate(hooks):
            if hook == owned:
                return index, hook
        candidates = [
            (index, hook) for index, hook in enumerate(hooks)
            if "hwskill adapter claude-code" in json.dumps(hook, ensure_ascii=False)
        ]
        if candidates:
            return candidates[0]
        return None, None
    for index, hook in enumerate(hooks):
        if "hwskill adapter claude-code" in json.dumps(hook, ensure_ascii=False):
            return index, hook
    return None, None


def setup_claude_code(
    project: Path,
    registry_root: Path,
    audit_path: Path | None = None,
) -> SetupResult:
    project = project.resolve()
    settings_path = project / ".claude/settings.json"
    mcp_path = project / ".mcp.json"
    state_path = project / ".hwskills/state/setup-claude-code.json"
    state = _read_state(state_path)
    settings = _read_json(settings_path)
    mcp_config = _read_json(mcp_path)

    hook_command = _adapter_args(
        "claude-code", "session-start", project, registry_root, audit_path
    )
    hook = {
        "matcher": "startup|resume|clear|compact",
        "hooks": [{"type": "command", "command": " ".join(
            json.dumps(part, ensure_ascii=False) for part in hook_command
        )}],
    }
    mcp = {
        "type": "stdio",
        "command": "hwskill",
        "args": _mcp_args(project, registry_root, audit_path),
    }

    mcp_servers = mcp_config.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError("Claude mcpServers must be an object")
    _owned_value(mcp_servers.get("hwskill"), state, "mcp", "Claude MCP entry")
    hook_index, existing_hook = _find_claude_hook(settings, state)
    _owned_value(existing_hook, state, "hook", "Claude SessionStart hook")

    session_hooks = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    if hook_index is None:
        session_hooks.append(hook)
    else:
        session_hooks[hook_index] = hook
    mcp_servers["hwskill"] = mcp

    changed = _write_json(settings_path, settings)
    changed = _write_json(mcp_path, mcp_config) or changed
    _write_state(state_path, {
        "schema_version": 1,
        "settings": str(settings_path.relative_to(project)),
        "mcp_config": str(mcp_path.relative_to(project)),
        "hook": hook,
        "mcp": mcp,
    })
    return SetupResult(settings_path, changed)


def unsetup_claude_code(project: Path) -> SetupResult:
    project = project.resolve()
    settings_path = project / ".claude/settings.json"
    mcp_path = project / ".mcp.json"
    state_path = project / ".hwskills/state/setup-claude-code.json"
    if not state_path.is_file():
        return SetupResult(settings_path, False)
    state = _read_json(state_path)
    settings = _read_json(settings_path)
    mcp_config = _read_json(mcp_path)
    mcp_servers = mcp_config.get("mcpServers", {})
    _owned_value(mcp_servers.get("hwskill"), state, "mcp", "Claude MCP entry")
    hook_index, existing_hook = _find_claude_hook(settings, state)
    _owned_value(existing_hook, state, "hook", "Claude SessionStart hook")

    del mcp_servers["hwskill"]
    session_hooks = settings["hooks"]["SessionStart"]
    del session_hooks[hook_index]
    if not session_hooks:
        del settings["hooks"]["SessionStart"]
    if not settings.get("hooks"):
        settings.pop("hooks", None)
    if not mcp_servers:
        mcp_config.pop("mcpServers", None)
    changed = _write_json(settings_path, settings)
    changed = _write_json(mcp_path, mcp_config) or changed
    state_path.unlink()
    return SetupResult(settings_path, changed)


def _opencode_plugin(
    project: Path,
    registry_root: Path,
    audit_path: Path | None,
) -> str:
    args = _adapter_args("opencode", "catalog", project, registry_root, audit_path)
    command = json.dumps(args, ensure_ascii=False)
    return (
        "export const HwskillPlugin = async () => ({\n"
        '  "experimental.chat.system.transform": async (_input, output) => {\n'
        f"    const child = Bun.spawn({command}, {{ stdout: \"pipe\", stderr: \"pipe\" }});\n"
        "    const context = await new Response(child.stdout).text();\n"
        "    const error = await new Response(child.stderr).text();\n"
        "    const status = await child.exited;\n"
        "    if (status !== 0) throw new Error(`hwskill catalog failed: ${error.trim()}`);\n"
        "    output.system.push(context.trim());\n"
        "  },\n"
        "});\n\n"
        "export default HwskillPlugin;\n"
    )


def setup_opencode(
    project: Path,
    registry_root: Path,
    audit_path: Path | None = None,
) -> SetupResult:
    project = project.resolve()
    config_path = project / "opencode.json"
    plugin_path = project / ".opencode/plugins/hwskill.js"
    state_path = project / ".hwskills/state/setup-opencode.json"
    state = _read_state(state_path)
    config = _read_json(config_path)
    mcp_entries = config.setdefault("mcp", {})
    if not isinstance(mcp_entries, dict):
        raise ValueError("OpenCode mcp must be an object")
    _owned_value(mcp_entries.get("hwskill"), state, "mcp", "OpenCode MCP entry")

    existing_plugin = plugin_path.read_text(encoding="utf-8") if plugin_path.is_file() else None
    if existing_plugin is not None:
        if state is None:
            raise ValueError("existing OpenCode plugin is not owned by hwskill; refusing to edit")
        if _digest_text(existing_plugin) != state.get("plugin_digest"):
            raise ValueError("managed OpenCode plugin was modified outside hwskill; refusing to edit")
    elif state is not None:
        raise ValueError("managed OpenCode plugin was modified outside hwskill; refusing to edit")

    mcp = {
        "type": "local",
        "command": ["hwskill", *_mcp_args(project, registry_root, audit_path)],
        "enabled": True,
    }
    plugin = _opencode_plugin(project, registry_root, audit_path)
    mcp_entries["hwskill"] = mcp
    changed = _write_json(config_path, config)
    changed = _write_text(plugin_path, plugin) or changed
    _write_state(state_path, {
        "schema_version": 1,
        "config": str(config_path.relative_to(project)),
        "plugin": str(plugin_path.relative_to(project)),
        "mcp": mcp,
        "plugin_digest": _digest_text(plugin),
    })
    return SetupResult(config_path, changed)


def unsetup_opencode(project: Path) -> SetupResult:
    project = project.resolve()
    config_path = project / "opencode.json"
    plugin_path = project / ".opencode/plugins/hwskill.js"
    state_path = project / ".hwskills/state/setup-opencode.json"
    if not state_path.is_file():
        return SetupResult(config_path, False)
    state = _read_json(state_path)
    config = _read_json(config_path)
    mcp_entries = config.get("mcp", {})
    _owned_value(mcp_entries.get("hwskill"), state, "mcp", "OpenCode MCP entry")
    if not plugin_path.is_file() or _digest_text(plugin_path.read_text(encoding="utf-8")) != state.get("plugin_digest"):
        raise ValueError("managed OpenCode plugin was modified outside hwskill; refusing to edit")

    del mcp_entries["hwskill"]
    if not mcp_entries:
        config.pop("mcp", None)
    _write_json(config_path, config)
    plugin_path.unlink()
    state_path.unlink()
    return SetupResult(config_path, True)
