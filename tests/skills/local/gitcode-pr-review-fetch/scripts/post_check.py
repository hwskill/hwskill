#!/usr/bin/env python3
"""Deterministically validate the observable GitCode PR Skill evaluation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from hwskill.eval_observer import observe_script_resolution


SKILL_ID = "local/gitcode-pr-review-fetch"
SCRIPT_NAME = "fetch_gitcode_pr_patch.py"
PR_URL = "https://gitcode.com/openeuler/OmniStream/pull/587"


def main() -> int:
    try:
        context_path = _environment_path("HWSKILL_TEST_CONTEXT")
        artifacts = _environment_path("HWSKILL_TEST_ARTIFACTS")
        workspace = _environment_path("HWSKILL_TEST_WORKSPACE")
        context = _load_context(context_path)
        _require_successful_agent(context)
        patch = workspace / "pr-587.patch"
        _require_patch(patch)
        resolution = observe_script_resolution(
            artifacts / "actions" / "run-agent" / "events.jsonl",
            SKILL_ID,
            SCRIPT_NAME,
            expected_url=PR_URL,
        )
        _require_resolution(resolution, patch)
        resolution["patch_path"] = str(patch)
        (artifacts / "script-resolution.json").write_text(
            json.dumps(resolution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"GitCode PR post-check failed: {exc}", file=sys.stderr)
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


def _require_successful_agent(context: dict[str, object]) -> None:
    actions = context.get("actions")
    if not isinstance(actions, dict):
        raise ValueError("test context actions are missing")
    agent = actions.get("run-agent")
    if not isinstance(agent, dict) or agent.get("status") != "completed" or agent.get("exit_code") != 0:
        raise ValueError("Agent action did not complete successfully")


def _require_patch(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Agent did not create pr-587.patch in the workspace")
    if not path.read_text(encoding="utf-8").startswith("diff --git "):
        raise ValueError("pr-587.patch is not a non-empty unified Git patch")


def _require_resolution(resolution: dict[str, object], patch: Path) -> None:
    if not resolution.get("direct_resolution"):
        raise ValueError("Agent searched for the Skill location after loading it")
    if not resolution.get("execution_succeeded"):
        raise ValueError("Agent did not successfully run the Skill script")
    diagnostic = resolution.get("patch_diagnostic")
    if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("files"), int):
        raise ValueError("successful script invocation did not report patch diagnostics")
    patch_files = sum(
        line.startswith("diff --git ")
        for line in patch.read_text(encoding="utf-8").splitlines()
    )
    if diagnostic["files"] != patch_files:
        raise ValueError("patch diagnostics file count does not match patch headers")


if __name__ == "__main__":
    raise SystemExit(main())
