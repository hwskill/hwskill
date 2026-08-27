#!/bin/sh
set -eu

workspace=/workspace
artifacts="$workspace/artifacts"
audit_path="$artifacts/hwskill-audit.jsonl"
events_path="$artifacts/codex.jsonl"
patch_path="$workspace/pr-587.patch"
eval_model=gpt-5.6-sol
eval_reasoning=medium

mkdir -p "$artifacts"
hwskill profile bind codex-demo --project "$workspace" \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --yes >/dev/null
hwskill setup codex --project "$workspace" \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --audit-path "$audit_path" --yes >/dev/null

export EVAL_CODEX_VERSION EVAL_MODEL="$eval_model" EVAL_REASONING="$eval_reasoning"
EVAL_CODEX_VERSION=$(codex --version)

codex exec --json --ephemeral --approve-for-me \
  --dangerously-bypass-hook-trust --skip-git-repo-check \
  --model "$eval_model" -c 'model_reasoning_effort="medium"' \
  -C "$workspace" \
  "请获取 https://gitcode.com/openeuler/OmniStream/pull/587 的完整代码 patch，保存到 /workspace/pr-587.patch；然后报告 PR state、head SHA 和变更文件数。" \
  >"$events_path"

test -s "$patch_path"
grep -q '^diff --git ' "$patch_path"

python3 - <<'PY'
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from hwskill.eval_observer import observe_script_resolution

artifacts = Path("/workspace/artifacts")
result = observe_script_resolution(
    artifacts / "codex.jsonl",
    "local/gitcode-pr-review-fetch",
    "fetch_gitcode_pr_patch.py",
    expected_url="https://gitcode.com/openeuler/OmniStream/pull/587",
    expected_output="/workspace/pr-587.patch",
)
result["agent"] = {
    "tool": "codex",
    "version": os.environ["EVAL_CODEX_VERSION"],
    "model": os.environ["EVAL_MODEL"],
    "reasoning_effort": os.environ["EVAL_REASONING"],
}
result["observed_at"] = datetime.now(timezone.utc).isoformat()
diagnostic = result["patch_diagnostic"]
if diagnostic is None:
    raise SystemExit("successful script invocation did not report patch metadata")
patch_files = sum(
    line.startswith("diff --git ")
    for line in Path("/workspace/pr-587.patch").read_text(encoding="utf-8").splitlines()
)
result["patch_diff_file_count"] = patch_files
(artifacts / "script-resolution.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
if not result["direct_resolution"]:
    raise SystemExit("Agent searched for the skill location before invoking its script")
if not result["execution_succeeded"]:
    raise SystemExit("Agent did not successfully execute the runtime-anchored script")
if diagnostic["files"] != patch_files:
    raise SystemExit("reported changed-file count does not match patch file headers")
PY

grep -q '"event": "load"' "$audit_path"
cp "$patch_path" "$artifacts/pr-587.patch"
echo "GitCode PR agent eval PASS"
