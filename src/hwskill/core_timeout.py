"""Bounded time budgets for isolated core unittest selections."""

from __future__ import annotations

import os
from pathlib import Path


_MAX_CORE_TEST_FILES = 256
_MAX_CORE_TIMEOUT_SECONDS = 300.0


def core_timeout_seconds(selection: Path, repository: Path, action_timeout: float) -> float:
    """Scale a full core-suite budget by regular test-file count, with a hard cap."""
    timeout = float(action_timeout)
    if timeout <= 0:
        raise ValueError("core action timeout must be positive")
    if selection != Path("tests/core"):
        return timeout
    count = _core_test_file_count(repository / "tests" / "core")
    return min(timeout * max(count, 1), _MAX_CORE_TIMEOUT_SECONDS)


def _core_test_file_count(core: Path) -> int:
    count = 0
    pending = [core]
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
                continue
            if entry.name.startswith("test_") and entry.name.endswith(".py") and entry.is_file(follow_symlinks=False):
                count += 1
                if count >= _MAX_CORE_TEST_FILES:
                    return _MAX_CORE_TEST_FILES
    return count
