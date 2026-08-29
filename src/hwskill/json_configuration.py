from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any

from .configuration import SetupResult
from .hosts import CommandRunner, run_command
from .scopes import ScopeTarget, project_scope, setup_state_path


def _target(value: ScopeTarget | Path) -> ScopeTarget:
    return value if isinstance(value, ScopeTarget) else project_scope(value)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be an object: {path}")
    return data


def _json_text(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


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


def _scope_args(target: ScopeTarget) -> list[str]:
    if target.kind == "user":
        return ["--scope", "user"]
    assert target.project_root is not None
    return ["--scope", "project", "--project", str(target.project_root)]


def _mcp_args(
    target: ScopeTarget,
    registry_root: Path,
    audit_path: Path | None,
) -> list[str]:
    args = ["serve-mcp", "--repo-root", str(registry_root.resolve()), *_scope_args(target)]
    if audit_path is not None:
        args.extend(("--audit-path", str(audit_path.resolve())))
    return args


def _adapter_args(
    host: str,
    command: str,
    target: ScopeTarget,
    registry_root: Path,
    audit_path: Path | None,
) -> list[str]:
    args = [
        "hwskill", "adapter", host, command,
        "--repo-root", str(registry_root.resolve()), *_scope_args(target),
    ]
    if audit_path is not None:
        args.extend(("--audit-path", str(audit_path.resolve())))
    return args


def _state_path_value(target: ScopeTarget, path: Path) -> str:
    if target.project_root is None:
        return str(path)
    return str(path.relative_to(target.project_root))


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


def _find_claude_hook(
    settings: dict[str, Any],
    state: dict[str, Any] | None,
) -> tuple[int | None, Any]:
    hooks = settings.get("hooks", {}).get("SessionStart", [])
    if not isinstance(hooks, list):
        raise ValueError("Claude hooks.SessionStart must be an array")
    owned = state.get("hook") if state else None
    if owned is not None:
        for index, hook in enumerate(hooks):
            if hook == owned:
                return index, hook
        candidates = [
            (index, hook)
            for index, hook in enumerate(hooks)
            if "hwskill adapter claude-code" in json.dumps(hook, ensure_ascii=False)
        ]
        return candidates[0] if candidates else (None, None)
    for index, hook in enumerate(hooks):
        if "hwskill adapter claude-code" in json.dumps(hook, ensure_ascii=False):
            return index, hook
    return None, None


def _claude_settings_path(target: ScopeTarget) -> Path:
    if target.kind == "user":
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        return root / "settings.json"
    assert target.project_root is not None
    return target.project_root / ".claude/settings.json"


def _claude_project_mcp_path(target: ScopeTarget) -> Path:
    assert target.project_root is not None
    return target.project_root / ".mcp.json"


def _claude_mcp_get(command_runner: CommandRunner):
    return command_runner(("claude", "mcp", "get", "hwskill"))


def _claude_mcp_matches(
    completed: subprocess.CompletedProcess[str], expected: Any
) -> bool:
    if completed.returncode != 0 or not isinstance(expected, dict):
        return False
    details = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.strip().partition(":")
        if separator and value.strip():
            details[key] = value.strip()
    try:
        args = shlex.split(details.get("Args", ""))
    except ValueError:
        return False
    return (
        details.get("Scope", "").startswith("User config")
        and details.get("Type") == expected.get("type")
        and details.get("Command") == expected.get("command")
        and args == expected.get("args")
    )


def setup_claude_code(
    target: ScopeTarget | Path,
    registry_root: Path,
    audit_path: Path | None = None,
    *,
    command_runner: CommandRunner = run_command,
) -> SetupResult:
    target = _target(target)
    settings_path = _claude_settings_path(target)
    state_path = setup_state_path(target, "claude-code")
    state = _read_state(state_path)
    settings = _read_json(settings_path)
    hook_command = _adapter_args(
        "claude-code", "session-start", target, registry_root, audit_path
    )
    hook = {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
            {
                "type": "command",
                "command": " ".join(
                    json.dumps(part, ensure_ascii=False) for part in hook_command
                ),
            }
        ],
    }
    mcp = {
        "type": "stdio",
        "command": "hwskill",
        "args": _mcp_args(target, registry_root, audit_path),
    }

    hook_index, existing_hook = _find_claude_hook(settings, state)
    _owned_value(existing_hook, state, "hook", "Claude SessionStart hook")
    state_data = {
        "schema_version": 1,
        "settings": _state_path_value(target, settings_path),
        "hook": hook,
        "mcp": mcp,
    }
    mcp_config = None
    mcp_path = None
    if target.kind == "user":
        inspected = _claude_mcp_get(command_runner)
        if state is None and inspected.returncode == 0:
            raise ValueError("existing Claude MCP entry is not owned by hwskill; refusing to edit")
        if state is not None and not _claude_mcp_matches(inspected, state.get("mcp")):
            raise ValueError("managed Claude MCP entry was modified outside hwskill; refusing to edit")
    else:
        mcp_path = _claude_project_mcp_path(target)
        mcp_config = _read_json(mcp_path)
        mcp_servers = mcp_config.setdefault("mcpServers", {})
        if not isinstance(mcp_servers, dict):
            raise ValueError("Claude mcpServers must be an object")
        _owned_value(mcp_servers.get("hwskill"), state, "mcp", "Claude MCP entry")

    session_hooks = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    if hook_index is None:
        session_hooks.append(hook)
    else:
        session_hooks[hook_index] = hook

    changed = _write_json(settings_path, settings)
    if target.kind == "user":
        if state is None or state.get("mcp") != mcp:
            completed = command_runner((
                "claude", "mcp", "add-json", "hwskill",
                json.dumps(mcp, ensure_ascii=False, separators=(",", ":")),
                "--scope", "user",
            ))
            if completed.returncode != 0:
                raise ValueError(f"claude mcp add-json failed: {completed.stderr.strip()}")
            changed = True
    else:
        assert mcp_path is not None and mcp_config is not None
        mcp_servers = mcp_config["mcpServers"]
        mcp_servers["hwskill"] = mcp
        changed = _write_json(mcp_path, mcp_config) or changed
        state_data["mcp_config"] = _state_path_value(target, mcp_path)
    _write_state(state_path, state_data)
    return SetupResult(settings_path, changed)


def claude_setup_is_current(
    target: ScopeTarget | Path,
    *,
    command_runner: CommandRunner = run_command,
) -> bool:
    target = _target(target)
    state_path = setup_state_path(target, "claude-code")
    try:
        state = _read_state(state_path)
        settings = _read_json(_claude_settings_path(target))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if state is None:
        return False
    _, hook = _find_claude_hook(settings, state)
    if hook != state.get("hook"):
        return False
    if target.kind == "user":
        return _claude_mcp_matches(
            _claude_mcp_get(command_runner), state.get("mcp")
        )
    mcp = _read_json(_claude_project_mcp_path(target)).get("mcpServers", {})
    return isinstance(mcp, dict) and mcp.get("hwskill") == state.get("mcp")


def unsetup_claude_code(
    target: ScopeTarget | Path,
    *,
    command_runner: CommandRunner = run_command,
) -> SetupResult:
    target = _target(target)
    settings_path = _claude_settings_path(target)
    state_path = setup_state_path(target, "claude-code")
    if not state_path.is_file():
        return SetupResult(settings_path, False)
    state = _read_json(state_path)
    settings = _read_json(settings_path)
    hook_index, existing_hook = _find_claude_hook(settings, state)
    _owned_value(existing_hook, state, "hook", "Claude SessionStart hook")
    if target.kind == "user":
        if not _claude_mcp_matches(
            _claude_mcp_get(command_runner), state.get("mcp")
        ):
            raise ValueError("managed Claude MCP entry was modified outside hwskill; refusing to edit")
        completed = command_runner(
            ("claude", "mcp", "remove", "hwskill", "--scope", "user")
        )
        if completed.returncode != 0:
            raise ValueError(f"claude mcp remove failed: {completed.stderr.strip()}")
    else:
        mcp_path = _claude_project_mcp_path(target)
        mcp_config = _read_json(mcp_path)
        mcp_servers = mcp_config.get("mcpServers", {})
        _owned_value(mcp_servers.get("hwskill"), state, "mcp", "Claude MCP entry")
        del mcp_servers["hwskill"]
        if not mcp_servers:
            mcp_config.pop("mcpServers", None)
        _write_json(mcp_path, mcp_config)
    session_hooks = settings["hooks"]["SessionStart"]
    del session_hooks[hook_index]
    if not session_hooks:
        del settings["hooks"]["SessionStart"]
    if not settings.get("hooks"):
        settings.pop("hooks", None)
    _write_json(settings_path, settings)
    state_path.unlink()
    return SetupResult(settings_path, True)


def _opencode_paths(target: ScopeTarget) -> tuple[Path, Path]:
    if target.kind == "user":
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        root = xdg / "opencode"
        return root / "opencode.json", root / "plugins/hwskill.js"
    assert target.project_root is not None
    return (
        target.project_root / "opencode.json",
        target.project_root / ".opencode/plugins/hwskill.js",
    )


def _opencode_plugin(
    target: ScopeTarget,
    registry_root: Path,
    audit_path: Path | None,
) -> str:
    args = _adapter_args("opencode", "catalog", target, registry_root, audit_path)
    if target.kind == "user":
        args.extend(("--runtime-project", "process.cwd()"))
    quoted = [json.dumps(part, ensure_ascii=False) for part in args]
    command = "[" + ", ".join(
        "process.cwd()" if part == json.dumps("process.cwd()") else part
        for part in quoted
    ) + "]"
    return (
        "export const HwskillPlugin = async () => ({\n"
        '  "experimental.chat.system.transform": async (_input, output) => {\n'
        f"    const child = Bun.spawn({command}, {{ stdout: \"pipe\", stderr: \"pipe\" }});\n"
        "    const context = await new Response(child.stdout).text();\n"
        "    const error = await new Response(child.stderr).text();\n"
        "    const status = await child.exited;\n"
        "    if (status !== 0) throw new Error(`hwskill catalog failed: ${error.trim()}`);\n"
        "    if (context.trim()) output.system.push(context.trim());\n"
        "  },\n"
        "});\n\n"
        "export default HwskillPlugin;\n"
    )


def setup_opencode(
    target: ScopeTarget | Path,
    registry_root: Path,
    audit_path: Path | None = None,
) -> SetupResult:
    target = _target(target)
    config_path, plugin_path = _opencode_paths(target)
    state_path = setup_state_path(target, "opencode")
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
        "command": ["hwskill", *_mcp_args(target, registry_root, audit_path)],
        "enabled": True,
    }
    plugin = _opencode_plugin(target, registry_root, audit_path)
    mcp_entries["hwskill"] = mcp
    changed = _write_json(config_path, config)
    changed = _write_text(plugin_path, plugin) or changed
    _write_state(state_path, {
        "schema_version": 1,
        "config": _state_path_value(target, config_path),
        "plugin": _state_path_value(target, plugin_path),
        "mcp": mcp,
        "plugin_digest": _digest_text(plugin),
    })
    return SetupResult(config_path, changed)


def opencode_setup_is_current(target: ScopeTarget | Path) -> bool:
    target = _target(target)
    config_path, plugin_path = _opencode_paths(target)
    try:
        state = _read_state(setup_state_path(target, "opencode"))
        config = _read_json(config_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if state is None or not plugin_path.is_file():
        return False
    mcp = config.get("mcp", {})
    return (
        isinstance(mcp, dict)
        and mcp.get("hwskill") == state.get("mcp")
        and _digest_text(plugin_path.read_text(encoding="utf-8"))
        == state.get("plugin_digest")
    )


def unsetup_opencode(target: ScopeTarget | Path) -> SetupResult:
    target = _target(target)
    config_path, plugin_path = _opencode_paths(target)
    state_path = setup_state_path(target, "opencode")
    if not state_path.is_file():
        return SetupResult(config_path, False)
    state = _read_json(state_path)
    config = _read_json(config_path)
    mcp_entries = config.get("mcp", {})
    _owned_value(mcp_entries.get("hwskill"), state, "mcp", "OpenCode MCP entry")
    if (
        not plugin_path.is_file()
        or _digest_text(plugin_path.read_text(encoding="utf-8"))
        != state.get("plugin_digest")
    ):
        raise ValueError("managed OpenCode plugin was modified outside hwskill; refusing to edit")
    del mcp_entries["hwskill"]
    if not mcp_entries:
        config.pop("mcp", None)
    _write_json(config_path, config)
    plugin_path.unlink()
    state_path.unlink()
    return SetupResult(config_path, True)
