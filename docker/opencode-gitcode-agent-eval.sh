#!/bin/sh
set -eu

workspace=/workspace
artifacts="$workspace/artifacts"
audit_path="$artifacts/hwskill-audit.jsonl"
events_path="$artifacts/opencode.jsonl"
error_path="$artifacts/opencode.stderr"
patch_path="$workspace/pr-587.patch"
auth_file=/credentials/opencode-auth.json
eval_model=minimax-cn-coding-plan/MiniMax-M2.5
data_home="$HOME/.local/share"

mkdir -p "$artifacts" "$data_home/opencode"
test -r "$auth_file"
cp "$auth_file" "$data_home/opencode/auth.json"
chmod 600 "$data_home/opencode/auth.json"
hwskill profile bind codex-demo --project "$workspace" \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --yes >/dev/null
hwskill setup opencode --project "$workspace" \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --audit-path "$audit_path" --yes >/dev/null

opencode_version=$(opencode --version)
export EVAL_AGENT_VERSION="$opencode_version" EVAL_MODEL="$eval_model"

opencode run --format json --dangerously-skip-permissions \
  --model "$eval_model" --dir "$workspace" \
  "请获取 https://gitcode.com/openeuler/OmniStream/pull/587 的完整代码 patch，保存到 /workspace/pr-587.patch；然后报告 PR state、head SHA 和变更文件数。" \
  >"$events_path" 2>"$error_path"

test -s "$patch_path"
grep -q '^diff --git ' "$patch_path"

python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hwskill.eval_observer import observe_script_resolution

artifacts = Path("/workspace/artifacts")
patch = Path("/workspace/pr-587.patch")
result = observe_script_resolution(
    artifacts / "opencode.jsonl",
    "local/gitcode-pr-review-fetch",
    "fetch_gitcode_pr_patch.py",
    expected_url="https://gitcode.com/openeuler/OmniStream/pull/587",
    expected_output="/workspace/pr-587.patch",
    host="opencode",
)
result["agent"] = {
    "tool": "opencode",
    "version": os.environ["EVAL_AGENT_VERSION"],
    "model": os.environ["EVAL_MODEL"],
    "credential_provider": "minimax-cn-coding-plan",
}
result["observed_at"] = datetime.now(timezone.utc).isoformat()
result["patch_diff_file_count"] = sum(
    line.startswith("diff --git ")
    for line in patch.read_text(encoding="utf-8").splitlines()
)
(artifacts / "script-resolution.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
if not result["direct_resolution"]:
    raise SystemExit("Agent searched for the skill location before invoking its script")
if not result["execution_succeeded"]:
    raise SystemExit("Agent did not successfully execute the runtime-anchored script")
diagnostic = result["patch_diagnostic"]
if diagnostic is None:
    raise SystemExit("successful script invocation did not report patch metadata")
if diagnostic["files"] != result["patch_diff_file_count"]:
    raise SystemExit("reported changed-file count does not match patch file headers")
PY

grep -q '"event": "catalog"' "$audit_path"
grep -q '"event": "search"' "$audit_path"
grep -q '"event": "load"' "$audit_path"
cp "$patch_path" "$artifacts/pr-587.patch"
echo "OpenCode GitCode PR agent eval PASS"
