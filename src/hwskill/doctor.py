from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess

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


def _common_checks(project: Path, registry_root: Path) -> list[CheckResult]:
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
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    executable = shutil.which("codex")
    return [
        CheckResult("codex-config", "PASS" if config.is_file() else "WARN", str(config)),
        CheckResult(
            "mcp-config", "PASS" if "[mcp_servers.hwskill]" in text else "WARN",
            "required hwskill MCP configured" if "required = true" in text else "MCP missing or optional",
        ),
        CheckResult(
            "hook-config", "PASS" if "[[hooks.SessionStart]]" in text else "WARN",
            "SessionStart catalog hook configured" if "[[hooks.SessionStart]]" in text else "hook missing",
        ),
        CheckResult("codex-cli", "PASS" if executable else "WARN", executable or "not found on PATH"),
    ]


def _claude_checks(project: Path) -> list[CheckResult]:
    settings_path = project / ".claude/settings.json"
    mcp_path = project / ".mcp.json"
    settings = _json(settings_path)
    mcp = _json(mcp_path)
    hooks = settings.get("hooks", {}).get("SessionStart", [])
    hook_ok = isinstance(hooks, list) and any(
        all(token in json.dumps(item, ensure_ascii=False) for token in (
            "hwskill", "adapter", "claude-code"
        ))
        for item in hooks
    )
    mcp_ok = isinstance(mcp.get("mcpServers"), dict) and "hwskill" in mcp["mcpServers"]
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
    mcp_ok = isinstance(config.get("mcp"), dict) and "hwskill" in config["mcp"]
    plugin_ok = plugin_path.is_file()
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


def run_doctor(host: str, project: Path, registry_root: Path) -> list[CheckResult]:
    host = host.replace("_", "-")
    project = project.resolve()
    registry_root = registry_root.resolve()
    host_checks = {
        "codex": _codex_checks,
        "claude-code": _claude_checks,
        "opencode": _opencode_checks,
    }
    if host not in host_checks:
        raise ValueError(f"unsupported host: {host}")
    return [*_common_checks(project, registry_root), *host_checks[host](project)]
