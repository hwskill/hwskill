from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

from .hosts import canonical_host
from .projects import find_project


@dataclass(frozen=True)
class ScopeTarget:
    kind: Literal["user", "project"]
    project_root: Path | None
    config_root: Path
    state_root: Path


def user_scope() -> ScopeTarget:
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    return ScopeTarget("user", None, config_home / "hwskill", state_home / "hwskill")


def project_scope(
    value: str | Path | None,
    cwd: Path | None = None,
) -> ScopeTarget:
    project = (
        Path(value).expanduser().resolve()
        if value is not None
        else find_project(cwd or Path.cwd())
    )
    root = project / ".hwskills"
    return ScopeTarget("project", project, root, root / "state")


def profile_path(target: ScopeTarget) -> Path:
    return target.config_root / "profile.yaml"


def lock_path(target: ScopeTarget) -> Path:
    return target.config_root / "lock.yaml"


def setup_state_path(target: ScopeTarget, host: str) -> Path:
    return target.state_root / f"setup-{canonical_host(host)}.json"
