from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from .profiles import resolve_profiles
from .registry import validate_registry


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def run_doctor(project: Path, registry_root: Path) -> list[CheckResult]:
    records = validate_registry(registry_root)
    catalog = resolve_profiles(project, registry_root)
    checks = [
        CheckResult("registry", "PASS", f"{len(records)} skills"),
        CheckResult("profile", "PASS", f"{len(catalog.skills)} effective skills"),
    ]
    config = project / ".codex/config.toml"
    config_text = config.read_text(encoding="utf-8") if config.is_file() else ""
    checks.append(CheckResult(
        "codex-config", "PASS" if config.is_file() else "WARN",
        str(config),
    ))
    checks.append(CheckResult(
        "mcp-config", "PASS" if "[mcp_servers.hwskill]" in config_text else "WARN",
        "required hwskill MCP configured" if "required = true" in config_text else "MCP missing or optional",
    ))
    checks.append(CheckResult(
        "hook-config", "PASS" if "[[hooks.SessionStart]]" in config_text else "WARN",
        "SessionStart catalog hook configured" if "[[hooks.SessionStart]]" in config_text else "hook missing",
    ))
    codex = shutil.which("codex")
    checks.append(CheckResult("codex-cli", "PASS" if codex else "WARN", codex or "not found on PATH"))
    audit_directory = Path.home() / ".hwskills/logs"
    writable_parent = next((item for item in (audit_directory, *audit_directory.parents) if item.exists()), None)
    audit_ok = writable_parent is not None and os.access(writable_parent, os.W_OK)
    checks.append(CheckResult(
        "audit-directory", "PASS" if audit_ok else "WARN", str(audit_directory),
    ))
    managed_native = project / ".agents/skills/hwskill"
    checks.append(CheckResult(
        "native-projection", "ERROR" if managed_native.exists() else "PASS",
        "managed native projection exists" if managed_native.exists() else "virtual catalog only",
    ))
    native_root = project / ".agents/skills"
    unmanaged = sorted(item.name for item in native_root.iterdir()) if native_root.is_dir() else []
    unmanaged = [item for item in unmanaged if item != "hwskill"]
    checks.append(CheckResult(
        "unmanaged-native-skills", "WARN" if unmanaged else "PASS",
        ", ".join(unmanaged) if unmanaged else "none",
    ))
    return checks
