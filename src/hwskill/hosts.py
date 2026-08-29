from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Callable, Sequence


@dataclass(frozen=True)
class HostSpec:
    host_id: str
    executable: str
    verified_version: str


HOST_SPECS = {
    "codex": HostSpec("codex", "codex", "0.147.0"),
    "claude-code": HostSpec("claude-code", "claude", "2.1.141"),
    "opencode": HostSpec("opencode", "opencode", "1.14.48"),
}


def canonical_host(value: str) -> str:
    host = value.replace("_", "-")
    if host not in HOST_SPECS:
        raise ValueError(f"unsupported host: {value}")
    return host


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
