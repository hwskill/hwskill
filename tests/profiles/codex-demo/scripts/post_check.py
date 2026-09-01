#!/usr/bin/env python3
"""Validate the deterministic business evidence for the codex-demo Profile."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    try:
        context = _load_context(_environment_path("HWSKILL_TEST_CONTEXT"))
        artifacts = _environment_path("HWSKILL_TEST_ARTIFACTS")
        workspace = _environment_path("HWSKILL_TEST_WORKSPACE")
        _require_successful_action(context, "run-agent")
        _require_successful_action(context, "run-business-tests")
        source = workspace / "order_pricing.py"
        if "subtotal >= discount_threshold" not in source.read_text(encoding="utf-8"):
            raise ValueError("inclusive discount threshold fix is absent")
        diff = (artifacts / "workspace.diff").read_text(encoding="utf-8")
        if "+    if subtotal >= discount_threshold:" not in diff:
            raise ValueError("workspace diff does not contain the inclusive boundary fix")
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


if __name__ == "__main__":
    raise SystemExit(main())
