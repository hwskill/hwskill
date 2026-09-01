#!/usr/bin/env python3
"""Validate the deterministic business evidence for the codex-demo Profile."""

from __future__ import annotations

import ast
from decimal import Decimal
import importlib.util
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
        _require_inclusive_calculate_total(source)
        _require_business_oracle(source)
        diff = (artifacts / "workspace.diff").read_text(encoding="utf-8")
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


def _require_inclusive_calculate_total(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        (item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "calculate_total"),
        None,
    )
    if not isinstance(function, ast.FunctionDef):
        raise ValueError("calculate_total function is absent")
    comparisons = [
        item for item in ast.walk(function)
        if isinstance(item, ast.Compare)
        and isinstance(item.left, ast.Name) and item.left.id == "subtotal"
        and len(item.ops) == 1 and isinstance(item.ops[0], ast.GtE)
        and len(item.comparators) == 1 and isinstance(item.comparators[0], ast.Name)
        and item.comparators[0].id == "discount_threshold"
    ]
    if not comparisons:
        raise ValueError("calculate_total does not contain the effective inclusive threshold comparison")


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
    if any(path.startswith("tests/") and path.endswith(".py") and "/__pycache__/" not in path for path in changed):
        raise ValueError("workspace modified mutable business test fixtures")


if __name__ == "__main__":
    raise SystemExit(main())
