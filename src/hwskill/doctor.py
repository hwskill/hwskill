from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from .configuration import codex_setup_is_current
from .profiles import resolve_profiles
from .registry import validate_registry


VERIFIED_OPENCODE_VERSION = "1.14.48"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def run_common_checks(project: Path, registry_root: Path) -> list[CheckResult]:
    project = project.resolve()
    registry_root = registry_root.resolve()
    records = validate_registry(registry_root)
    catalog = resolve_profiles(project, registry_root)
    checks = [
        CheckResult("registry", "PASS", f"{len(records)} skills"),
        CheckResult("profile", "PASS", f"{len(catalog.skills)} effective skills"),
    ]
    audit_directory = Path.home() / ".hwskills/logs"
    writable_parent = next(
        (item for item in (audit_directory, *audit_directory.parents) if item.exists()), None
    )
    audit_ok = writable_parent is not None and os.access(writable_parent, os.W_OK)
    checks.append(CheckResult(
        "audit-directory", "PASS" if audit_ok else "WARN", str(audit_directory)
    ))

    native_roots = (
        project / ".agents/skills",
        project / ".claude/skills",
        project / ".opencode/skills",
    )
    managed = [str(root / "hwskill") for root in native_roots if (root / "hwskill").exists()]
    checks.append(CheckResult(
        "native-projection", "ERROR" if managed else "PASS",
        ", ".join(managed) if managed else "virtual catalog only",
    ))
    unmanaged = [
        f"{root.relative_to(project)}/{item.name}"
        for root in native_roots if root.is_dir()
        for item in sorted(root.iterdir()) if item.name != "hwskill"
    ]
    checks.append(CheckResult(
        "unmanaged-native-skills", "WARN" if unmanaged else "PASS",
        ", ".join(unmanaged) if unmanaged else "none",
    ))
    return checks


def _codex_checks(project: Path) -> list[CheckResult]:
    config = project / ".codex/config.toml"
    setup_ok = codex_setup_is_current(project)
    executable = shutil.which("codex")
    return [
        CheckResult("codex-config", "PASS" if config.is_file() else "WARN", str(config)),
        CheckResult(
            "mcp-config", "PASS" if setup_ok else "WARN",
            "required hwskill MCP configured" if setup_ok else "MCP missing or modified",
        ),
        CheckResult(
            "hook-config", "PASS" if setup_ok else "WARN",
            "SessionStart catalog hook configured" if setup_ok else "hook missing or modified",
        ),
        CheckResult("codex-cli", "PASS" if executable else "WARN", executable or "not found on PATH"),
    ]


def _claude_checks(project: Path) -> list[CheckResult]:
    settings_path = project / ".claude/settings.json"
    mcp_path = project / ".mcp.json"
    settings = _json(settings_path)
    mcp = _json(mcp_path)
    state = _json(project / ".hwskills/state/setup-claude-code.json")
    hooks = settings.get("hooks", {}).get("SessionStart", [])
    expected_hook = state.get("hook")
    hook_ok = (
        isinstance(hooks, list)
        and isinstance(expected_hook, dict)
        and expected_hook in hooks
    )
    actual_mcp = mcp.get("mcpServers", {}).get("hwskill") if isinstance(mcp.get("mcpServers"), dict) else None
    expected_mcp = state.get("mcp")
    mcp_ok = (
        isinstance(actual_mcp, dict)
        and actual_mcp == expected_mcp
        and actual_mcp.get("type") == "stdio"
        and actual_mcp.get("command") == "hwskill"
        and isinstance(actual_mcp.get("args"), list)
        and actual_mcp["args"][:1] == ["serve-mcp"]
    )
    executable = shutil.which("claude")
    return [
        CheckResult("claude-settings", "PASS" if settings_path.is_file() else "WARN", str(settings_path)),
        CheckResult("claude-hook-config", "PASS" if hook_ok else "WARN", "SessionStart catalog hook configured" if hook_ok else "hook missing"),
        CheckResult("claude-mcp-config", "PASS" if mcp_ok else "WARN", "hwskill MCP configured" if mcp_ok else "MCP missing"),
        CheckResult("claude-cli", "PASS" if executable else "WARN", executable or "not found on PATH"),
    ]


def _opencode_checks(project: Path) -> list[CheckResult]:
    config_path = project / "opencode.json"
    plugin_path = project / ".opencode/plugins/hwskill.js"
    config = _json(config_path)
    state = _json(project / ".hwskills/state/setup-opencode.json")
    actual_mcp = config.get("mcp", {}).get("hwskill") if isinstance(config.get("mcp"), dict) else None
    expected_mcp = state.get("mcp")
    mcp_ok = (
        isinstance(actual_mcp, dict)
        and actual_mcp == expected_mcp
        and actual_mcp.get("type") == "local"
        and actual_mcp.get("enabled") is True
        and isinstance(actual_mcp.get("command"), list)
        and actual_mcp["command"][:2] == ["hwskill", "serve-mcp"]
    )
    actual_digest = None
    if plugin_path.is_file():
        actual_digest = "sha256:" + hashlib.sha256(
            plugin_path.read_bytes()
        ).hexdigest()
    plugin_ok = actual_digest is not None and actual_digest == state.get("plugin_digest")
    executable = shutil.which("opencode")
    version = None
    if executable:
        try:
            completed = subprocess.run(
                [executable, "--version"], text=True, capture_output=True,
                timeout=5, check=False,
            )
            if completed.returncode == 0:
                version = completed.stdout.strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            version = None
    version_ok = version == VERIFIED_OPENCODE_VERSION
    return [
        CheckResult("opencode-config", "PASS" if config_path.is_file() else "WARN", str(config_path)),
        CheckResult("opencode-plugin-config", "PASS" if plugin_ok else "WARN", str(plugin_path)),
        CheckResult("opencode-mcp-config", "PASS" if mcp_ok else "WARN", "hwskill MCP configured" if mcp_ok else "MCP missing"),
        CheckResult("opencode-cli", "PASS" if executable else "WARN", executable or "not found on PATH"),
        CheckResult(
            "opencode-version", "PASS" if version_ok else "WARN",
            f"installed: {version or 'unknown'}; verified: {VERIFIED_OPENCODE_VERSION}",
        ),
    ]


def run_host_checks(host: str, project: Path) -> list[CheckResult]:
    host = host.replace("_", "-")
    project = project.resolve()
    host_checks = {
        "codex": _codex_checks,
        "claude-code": _claude_checks,
        "opencode": _opencode_checks,
    }
    if host not in host_checks:
        raise ValueError(f"unsupported host: {host}")
    return host_checks[host](project)


def run_doctor(host: str, project: Path, registry_root: Path) -> list[CheckResult]:
    return [
        *run_common_checks(project, registry_root),
        *run_host_checks(host, project),
    ]
