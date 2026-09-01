"""Inspectable, non-secret preparation checks for the test service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Literal, Sequence

from .hosts import HOST_SPECS
from .test_configuration import HostModel, TestConfiguration, resolve_host_model, write_test_configuration


STANDARD_TEST_IMAGE = "hwskill/test:latest"
_TIMEOUT_SECONDS = 10.0
_PROBE_PROMPT = "Reply with exactly READY."

SetupStatus = Literal["READY", "BLOCKED"]
SetupRunner = Callable[..., subprocess.CompletedProcess[str]]
CredentialInspector = Callable[[str, Mapping[str, str], Path, Callable[[Path], bool]], tuple[str, bool]]


@dataclass(frozen=True)
class SetupCheck:
    name: str
    status: SetupStatus
    detail: str


@dataclass(frozen=True)
class SetupReport:
    status: SetupStatus
    checks: tuple[SetupCheck, ...]


def inspect_test_setup(
    config: TestConfiguration,
    runner: SetupRunner,
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    credential_inspector: CredentialInspector | None = None,
    is_file: Callable[[Path], bool] | None = None,
) -> SetupReport:
    """Return deterministic, redacted readiness checks without changing state.

    ``runner``, environment, home and file predicate are injected explicitly so
    callers can inspect setup without binding unit tests to a host machine.
    """
    env = os.environ if environment is None else environment
    user_home = Path.home() if home is None else Path(home)
    file_exists = Path.is_file if is_file is None else is_file
    credentials = credential_inspector or inspect_credentials
    host, model = resolve_host_model(config)

    checks: list[SetupCheck] = []
    docker_ready = _docker_check(config, runner)
    checks.append(docker_ready)
    checks.append(_image_check(config, runner, docker_ready.status == "READY"))
    host_check = _host_check(host, runner)
    checks.append(host_check)
    source, available = credentials(host, env, user_home, file_exists)
    checks.append(SetupCheck(
        "authentication", "READY" if available else "BLOCKED",
        f"source: {source}; {'available' if available else 'unavailable'}",
    ))
    checks.append(SetupCheck(
        "configured-model", "READY",
        f"host: {host}; model: {model.model}; reasoning: {model.reasoning}",
    ))
    can_probe = host_check.status == "READY" and available
    checks.append(_probe_check(host, model, runner, can_probe))
    return SetupReport(
        "READY" if all(item.status == "READY" for item in checks) else "BLOCKED",
        tuple(checks),
    )


def configure_test_setup(
    current: TestConfiguration,
    *,
    runner: SetupRunner,
    check: bool = False,
    replacement: TestConfiguration | None = None,
    prompt: Callable[[str], str] = input,
    inspect: Callable[..., SetupReport] = inspect_test_setup,
    write: Callable[[TestConfiguration], None] = write_test_configuration,
) -> SetupReport:
    """Keep a usable model by default; require a replacement when it is unusable.

    Check mode is intentionally an early return: it neither prompts nor writes
    and inspection never invokes Docker build commands.
    """
    report = inspect(current, runner)
    if check:
        return report
    available = _check_status(report, "model-availability") == "READY"
    if available:
        answer = prompt("Current test model is usable. Keep it? [Y/r] ").strip().lower()
        if answer in {"", "y", "yes", "keep", "k"}:
            return report
        if answer not in {"r", "replace"}:
            raise ValueError("choose keep or replace")
    if replacement is None:
        raise ValueError("a replacement configuration is required when the current model is unavailable")
    write(replacement)
    return inspect(replacement, runner)


def inspect_credentials(
    host: str,
    environment: Mapping[str, str],
    home: Path,
    is_file: Callable[[Path], bool],
) -> tuple[str, bool]:
    """Report only a credential source kind and boolean availability, never values."""
    environment_names, store = {
        "codex": (("CODEX_API_KEY",), home / ".codex" / "auth.json"),
        "claude-code": (("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), home / ".claude" / ".credentials.json"),
        "opencode": (("OPENCODE_API_KEY",), home / ".local" / "share" / "opencode" / "auth.json"),
    }[host]
    if any(bool(environment.get(name)) for name in environment_names):
        return "environment", True
    try:
        available = bool(is_file(store)) and not store.is_symlink()
    except OSError:
        available = False
    return ("host-store" if available else "none"), available


def _docker_check(config: TestConfiguration, runner: SetupRunner) -> SetupCheck:
    if config.runner == "local":
        return SetupCheck("docker-daemon", "READY", "not required for local runner")
    completed = _run(runner, ("docker", "info", "--format", "{{.ServerVersion}}"))
    if completed is None or completed.returncode != 0 or not _bounded_output(completed.stdout):
        return SetupCheck("docker-daemon", "BLOCKED", "Docker daemon unavailable")
    return SetupCheck("docker-daemon", "READY", "Docker daemon available")


def _image_check(config: TestConfiguration, runner: SetupRunner, docker_ready: bool) -> SetupCheck:
    if config.runner == "local":
        return SetupCheck("standard-image", "READY", "not required for local runner")
    if not docker_ready:
        return SetupCheck("standard-image", "BLOCKED", "Docker daemon unavailable")
    completed = _run(runner, ("docker", "image", "inspect", "--format", "{{.Id}}", STANDARD_TEST_IMAGE))
    digest = _bounded_output(None if completed is None else completed.stdout)
    if completed is None or completed.returncode != 0 or not digest.startswith("sha256:"):
        return SetupCheck("standard-image", "BLOCKED", "standard test image digest unavailable")
    return SetupCheck("standard-image", "READY", f"digest: {digest}")


def _host_check(host: str, runner: SetupRunner) -> SetupCheck:
    executable = HOST_SPECS[host].executable
    completed = _run(runner, (executable, "--version"))
    version = _bounded_output(None if completed is None else completed.stdout)
    if completed is None or completed.returncode != 0 or not version:
        return SetupCheck("host-cli", "BLOCKED", f"host CLI unavailable: {host}")
    return SetupCheck("host-cli", "READY", f"host: {host}; version: {version}")


def _probe_check(host: str, model: HostModel, runner: SetupRunner, enabled: bool) -> SetupCheck:
    if not enabled:
        return SetupCheck("model-availability", "BLOCKED", "host CLI or authentication unavailable")
    completed = _run(runner, _probe_command(host, model))
    if completed is None or completed.returncode != 0:
        return SetupCheck("model-availability", "BLOCKED", "minimal availability probe failed")
    return SetupCheck("model-availability", "READY", "minimal availability probe succeeded")


def _probe_command(host: str, model: HostModel) -> tuple[str, ...]:
    if host == "codex":
        return (
            "codex", "exec", "--model", model.model, "-c",
            f'model_reasoning_effort="{model.reasoning}"', "--json",
            "--skip-git-repo-check", _PROBE_PROMPT,
        )
    if host == "claude-code":
        return ("claude", "--print", "--output-format", "json", "--model", model.model, _PROBE_PROMPT)
    return ("opencode", "run", "--format", "json", "--model", model.model, _PROBE_PROMPT)


def _run(runner: SetupRunner, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return runner(tuple(argv), timeout=_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None


def _bounded_output(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    line = value.strip().splitlines()[0] if value.strip() else ""
    return line[:160]


def _check_status(report: SetupReport, name: str) -> SetupStatus:
    for check in report.checks:
        if check.name == name:
            return check.status
    return "BLOCKED"
