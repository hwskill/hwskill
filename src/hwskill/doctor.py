from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    checks.append(CheckResult(
        "codex-config", "PASS" if config.is_file() and "mcp_servers.hwskill" in config.read_text(encoding="utf-8") else "WARN",
        str(config),
    ))
    managed_native = project / ".agents/skills/hwskill"
    checks.append(CheckResult(
        "native-projection", "WARN" if managed_native.exists() else "PASS",
        "managed native projection exists" if managed_native.exists() else "virtual catalog only",
    ))
    return checks
