#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$repo_root/.venv/bin/hwskill" ]; then
  PATH="$repo_root/.venv/bin:$PATH"
else
  PATH="$repo_root/scripts:$PATH"
fi
if [ -d "$repo_root/.runtime-deps" ]; then
  PYTHONPATH="$repo_root/.runtime-deps${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONPATH
fi
if ! command -v hwskill >/dev/null 2>&1; then
  echo "Install hwskill first (for example: .venv/bin/pip install -e .)" >&2
  exit 2
fi
if ! python3 -c 'from mcp.server.fastmcp import FastMCP' >/dev/null 2>&1; then
  echo "The Python environment used by hwskill must provide the mcp package" >&2
  exit 2
fi
if [ -z "${CODEX_API_KEY:-}" ] && ! codex login status >/dev/null 2>&1; then
  echo "Codex authentication is required: existing login or CODEX_API_KEY" >&2
  exit 2
fi

work_dir=$(mktemp -d /tmp/hwskill-live-XXXXXX)
trap 'rm -rf "$work_dir"' EXIT INT TERM
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
cp -a "$repo_root/examples/codex-demo/." "$work_dir/"
mkdir -p "$repo_root/artifacts"

audit_path="$repo_root/artifacts/hwskill-audit.jsonl"
rm -f "$audit_path"
hwskill setup codex --project "$work_dir" --repo-root "$repo_root" \
  --audit-path "$audit_path" --yes >/dev/null

HWSKILL_AUDIT_PATH="$audit_path" codex exec \
  --json --ephemeral --approve-for-me \
  --dangerously-bypass-hook-trust --skip-git-repo-check -C "$work_dir" \
  "订单折扣在阈值边界失败。必须先用 hwskill_search 查询 systematic debugging，再仅用 hwskill_load 加载 superpowers/systematic-debugging；然后系统化定位并修复，最后运行测试。" \
  >"$repo_root/artifacts/codex.jsonl"

python3 -m unittest discover -s "$work_dir/tests" -t "$work_dir" -v \
  >"$repo_root/artifacts/test-output.txt" 2>&1
git diff --no-index -- "$repo_root/examples/codex-demo" "$work_dir" \
  >"$repo_root/artifacts/demo.diff" || test $? -eq 1

grep -q 'hwskill_search' "$repo_root/artifacts/codex.jsonl"
grep -q 'hwskill_load' "$repo_root/artifacts/codex.jsonl"
grep -q 'subtotal >= discount_threshold' "$work_dir/order_pricing.py"
grep -q '"event": "load"' "$audit_path"
echo "Codex Live Eval PASS"
