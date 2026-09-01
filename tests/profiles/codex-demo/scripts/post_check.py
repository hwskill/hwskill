#!/usr/bin/env python3
"""Validate the deterministic business evidence for the codex-demo Profile."""

from __future__ import annotations

from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys


_FIXTURE_ROOT = "tests/profiles/codex-demo/fixtures"
_PROTECTED_FIXTURE_FILES = (
    "order_pricing.py",
    "tests/__init__.py",
    "tests/test_order_pricing.py",
)
_PROFILE_SETUP_FILES = frozenset({".hwskills/profile.yaml", ".hwskills/lock.yaml"})
_MAX_FIXTURE_BYTES = 1024 * 1024


def main() -> int:
    try:
        context = _load_context(_environment_path("HWSKILL_TEST_CONTEXT"))
        artifacts = _real_directory(_environment_path("HWSKILL_TEST_ARTIFACTS"), "artifacts")
        workspace = _real_directory(_environment_path("HWSKILL_TEST_WORKSPACE"), "workspace")
        evidence = _real_directory(_environment_path("HWSKILL_TEST_EVIDENCE_WORKSPACE"), "evidence workspace")
        repository = _real_directory(_environment_path("HWSKILL_TEST_REPO_ROOT"), "repository")
        _require_successful_action(context, "run-agent")
        _require_successful_action(context, "run-business-tests")
        source = evidence / "order_pricing.py"
        _require_exact_fixture_repair(repository, evidence)
        _require_business_oracle(source)
        diff = _read_regular_file(artifacts, "workspace.diff")
        _require_trusted_workspace_changes(diff)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"codex-demo post-check failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _environment_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path


def _real_directory(path: Path, label: str) -> Path:
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected") from exc
    if not stat.S_ISDIR(mode):
        raise ValueError(f"{label} must be a real directory")
    return path.resolve(strict=True)


def _load_context(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("test context must be a JSON object")
    return data


def _require_successful_action(context: dict[str, object], action_id: str) -> None:
    actions = context.get("actions")
    if not isinstance(actions, dict):
        raise ValueError("test context actions are missing")
    action = actions.get(action_id)
    if not isinstance(action, dict) or action.get("status") != "completed" or action.get("exit_code") != 0:
        raise ValueError(f"{action_id} did not complete successfully")


def _require_exact_fixture_repair(repository: Path, workspace: Path) -> None:
    baseline = {
        relative: _normalize_newlines(_read_regular_file(repository, f"{_FIXTURE_ROOT}/{relative}"))
        for relative in _PROTECTED_FIXTURE_FILES
    }
    target = baseline["order_pricing.py"]
    old = "if subtotal > discount_threshold:"
    new = "if subtotal >= discount_threshold:"
    if target.count(old) != 1 or new in target:
        raise ValueError("immutable order pricing fixture has an unexpected boundary shape")
    expected = target.replace(old, new)
    for relative, original in baseline.items():
        actual = _normalize_newlines(_read_regular_file(workspace, relative))
        required = expected if relative == "order_pricing.py" else original
        if actual != required:
            raise ValueError(f"workspace fixture differs outside the permitted boundary repair: {relative}")


def _require_business_oracle(path: Path) -> None:
    specification = importlib.util.spec_from_file_location("_hwskill_codex_demo_oracle", path)
    if specification is None or specification.loader is None:
        raise ValueError("cannot load workspace order pricing implementation")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    calculate_total = getattr(module, "calculate_total", None)
    if not callable(calculate_total):
        raise ValueError("workspace calculate_total is not callable")
    threshold = Decimal("100")
    rate = Decimal("0.10")
    expected = ((Decimal("99"), Decimal("99")), (threshold, Decimal("90.00")), (Decimal("110"), Decimal("99.00")))
    for subtotal, result in expected:
        if calculate_total(subtotal, threshold, rate) != result:
            raise ValueError("workspace implementation fails the immutable discount boundary oracle")


def _require_trusted_workspace_changes(diff: str) -> None:
    changed = {line[2:] for line in diff.splitlines() if len(line) > 2 and line[1] == " " and line[0] in {"A", "D", "M"}}
    if "order_pricing.py" not in changed:
        raise ValueError("workspace did not modify order_pricing.py")
    unexpected = {
        path for path in changed
        if (
            path != "order_pricing.py"
            and path not in _PROFILE_SETUP_FILES
            and not _generated_python_cache(path)
        )
    }
    if unexpected:
        raise ValueError("workspace modified protected or unrelated fixture files")


def _generated_python_cache(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return path.endswith(".pyc") and "__pycache__" in parts


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _read_regular_file(root: Path, relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("fixture path is unsafe")
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        for part in parts[:-1]:
            child_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_FIXTURE_BYTES:
            raise ValueError("fixture file is not a bounded regular file")
        raw = os.read(file_fd, _MAX_FIXTURE_BYTES + 1)
        if len(raw) > _MAX_FIXTURE_BYTES or os.read(file_fd, 1):
            raise ValueError("fixture file exceeds maximum size")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot safely read fixture {relative}") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


if __name__ == "__main__":
    raise SystemExit(main())
