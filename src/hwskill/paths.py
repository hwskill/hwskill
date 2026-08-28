from __future__ import annotations

import os
from pathlib import Path


def resolve_repo_root(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("HWSKILL_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    script_root = os.environ.get("HWSKILL_SCRIPT_ROOT")
    if script_root:
        return Path(script_root).expanduser().resolve()
    raise ValueError(
        "skill repository is unknown; pass --repo-root or set HWSKILL_HOME"
    )
