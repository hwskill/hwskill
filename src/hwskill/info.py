from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from . import __version__
from .doctor import CheckResult, run_common_checks, run_host_checks
from .profiles import resolve_profile_ids


HOSTS = ("codex", "claude-code", "opencode")
INTEGRATION_CHECKS = {
    "codex": ("mcp-config", "hook-config"),
    "claude-code": ("claude-hook-config", "claude-mcp-config"),
    "opencode": ("opencode-plugin-config", "opencode-mcp-config"),
}


def _git_output(repository: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repository), *args],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _git_info(repository: Path) -> dict[str, str | bool | None]:
    branch = _git_output(repository, "branch", "--show-current")
    commit = _git_output(repository, "rev-parse", "--short", "HEAD")
    status = _git_output(repository, "status", "--porcelain")
    remote = _safe_git_remote(
        _git_output(repository, "remote", "get-url", "origin")
    )
    return {
        "branch": branch or None,
        "commit": commit or None,
        "dirty": None if status is None else bool(status),
        "remote": remote or None,
    }


def _safe_git_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    try:
        parsed = urlsplit(remote)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return remote
    if not parsed.netloc or not parsed.hostname:
        return None
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _summary_status(checks: list[CheckResult]) -> str:
    statuses = {item.status for item in checks}
    if "ERROR" in statuses:
        return "ERROR"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


def _host_info(
    host: str,
    project: Path,
    common_checks: list[CheckResult],
) -> dict[str, Any]:
    try:
        host_checks = run_host_checks(host, project)
    except Exception as exc:
        host_checks = [CheckResult("doctor", "ERROR", str(exc))]
    checks = [*common_checks, *host_checks]
    required = INTEGRATION_CHECKS[host]
    passed = {
        item.name for item in host_checks
        if item.name in required and item.status == "PASS"
    }
    setup_state = project / f".hwskills/state/setup-{host}.json"
    if len(passed) == len(required):
        integration_status = "installed"
    elif (
        not passed
        and not setup_state.is_file()
        and not any(item.status == "ERROR" for item in host_checks)
    ):
        integration_status = "not installed"
    else:
        integration_status = "incomplete"
    return {
        "status": _summary_status(checks),
        "integration_status": integration_status,
        "checks": [asdict(item) for item in checks],
    }


def collect_info(
    repository: Path,
    project: Path,
    *,
    repository_source: str,
) -> dict[str, Any]:
    repository = repository.resolve()
    project = project.resolve()
    launcher = repository / "scripts/hwskill"
    command = os.environ.get("HWSKILL_COMMAND_PATH") or shutil.which("hwskill")
    command_path = Path(command).absolute() if command else None
    command_target = command_path.resolve() if command_path else None
    launcher_executable = launcher.is_file() and os.access(launcher, os.X_OK)
    venv = repository / ".venv"
    venv_python = venv / "bin/python"
    venv_ready = (
        (venv / ".hwskill-installed").is_file()
        and venv_python.is_file()
        and os.access(venv_python, os.X_OK)
    )
    installed = (
        command_path is not None
        and command_path.exists()
        and launcher_executable
        and command_target == launcher.resolve()
        and venv_ready
    )

    profile_error = None
    try:
        profiles = resolve_profile_ids(project)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        profiles = []
        profile_status = "ERROR"
        profile_error = str(exc)
    else:
        profile_status = "PASS" if profiles else "WARN"

    try:
        common_checks = run_common_checks(project, repository)
    except Exception as exc:
        common_checks = [CheckResult("doctor-common", "ERROR", str(exc))]

    return {
        "installation": {
            "status": "PASS" if installed else "WARN",
            "version": __version__,
            "repository": str(repository),
            "repository_source": repository_source,
            "command": str(command_path) if command_path else None,
            "command_target": str(command_target) if command_target else None,
            "launcher": str(launcher.resolve()),
            "launcher_executable": launcher_executable,
            "python": sys.executable,
            "venv_python": str(venv_python),
            "venv": str(venv),
            "venv_ready": venv_ready,
            "git": _git_info(repository),
        },
        "integration": {
            "project": str(project),
            "profiles": profiles,
            "profile_status": profile_status,
            "profile_error": profile_error,
            "hosts": {
                host: _host_info(host, project, common_checks) for host in HOSTS
            },
        },
    }


def format_info_summary(info: dict[str, Any]) -> str:
    installation = info["installation"]
    integration = info["integration"]
    git = installation["git"]
    git_reference = "@".join(
        item for item in (git["branch"], git["commit"]) if item
    )
    if git["dirty"] is None:
        git_value = f"{git_reference} status unknown" if git_reference else "unavailable"
    else:
        git_value = f"{git_reference or 'unavailable'} {'dirty' if git['dirty'] else 'clean'}"
    profiles = integration["profiles"]
    profile_value = (
        "error" if integration["profile_status"] == "ERROR"
        else ", ".join(profiles) if profiles
        else "none"
    )
    installation_status = (
        "installed" if installation["status"] == "PASS" else "incomplete"
    )
    lines = [
        f"hwskill v{_display_cell(installation['version'])} {installation_status}.",
        "",
        "Installation:",
        _summary_line("install path", installation["repository"], width=15),
        _summary_line("executable", installation["command"] or "not found", width=15),
        _summary_line("python", installation["venv_python"], width=15),
        _summary_line("git repo", git.get("remote") or "unavailable", width=15),
        _summary_line("git status", git_value, width=15),
        "",
        "Integrations:",
        _summary_line("project", integration["project"]),
        _summary_line("profiles", profile_value),
    ]
    for host, host_info in integration["hosts"].items():
        lines.append(
            f"{host:<12} -- {_display_cell(host_info['integration_status'])}"
        )
    return "\n".join(lines) + "\n"


def _display_cell(value: object) -> str:
    return re.sub(r"[\t\r\n]+", " ", str(value))


def _summary_line(label: str, value: object, *, width: int = 13) -> str:
    return f"{label + ':':<{width}}{_display_cell(value)}"
