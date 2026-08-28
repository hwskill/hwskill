#!/bin/sh
set -eu

test ! -e "$HOME/.agents/skills"
test ! -e /workspace-demo/.agents/skills
test "$(claude --version)" = "2.1.141 (Claude Code)"
test "$(opencode --version)" = "1.14.48"

hwskill registry validate --repo-root "$HWSKILL_REGISTRY_ROOT" >/tmp/registry.txt
hwskill registry build --repo-root "$HWSKILL_REGISTRY_ROOT" --check
hwskill profile resolve --project /workspace-demo --repo-root "$HWSKILL_REGISTRY_ROOT" --json >/tmp/effective.json
grep -q 'superpowers/systematic-debugging' /tmp/effective.json

printf '{"cwd":"/workspace-demo","session_id":"docker-smoke","source":"startup"}\n' \
  | hwskill adapter codex session-start \
      --repo-root "$HWSKILL_REGISTRY_ROOT" \
      --audit-path "$HWSKILL_AUDIT_PATH" >/tmp/hook.json
grep -q 'catalog_digest' /tmp/hook.json

hwskill setup claude-code --project /workspace-demo \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --audit-path "$HWSKILL_AUDIT_PATH" --yes
hwskill doctor claude-code --project /workspace-demo \
  --repo-root "$HWSKILL_REGISTRY_ROOT" >/tmp/claude-doctor.txt
printf '{"cwd":"/workspace-demo","session_id":"docker-claude","source":"startup"}\n' \
  | hwskill adapter claude-code session-start \
      --repo-root "$HWSKILL_REGISTRY_ROOT" \
      --audit-path "$HWSKILL_AUDIT_PATH" >/tmp/claude-hook.json
grep -q 'catalog_digest' /tmp/claude-hook.json

hwskill setup opencode --project /workspace-demo \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --audit-path "$HWSKILL_AUDIT_PATH" --yes
hwskill doctor opencode --project /workspace-demo \
  --repo-root "$HWSKILL_REGISTRY_ROOT" >/tmp/opencode-doctor.txt
hwskill adapter opencode catalog --project /workspace-demo \
  --repo-root "$HWSKILL_REGISTRY_ROOT" \
  --audit-path "$HWSKILL_AUDIT_PATH" >/tmp/opencode-catalog.txt
grep -q 'catalog_digest' /tmp/opencode-catalog.txt
node --check /workspace-demo/.opencode/plugins/hwskill.js
test ! -e /workspace-demo/.claude/skills
test ! -e /workspace-demo/.opencode/skills

python3 - <<'PY'
import asyncio
import os
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="hwskill",
        args=[
            "serve-mcp",
            "--project", "/workspace-demo",
            "--repo-root", os.environ["HWSKILL_REGISTRY_ROOT"],
            "--audit-path", os.environ["HWSKILL_AUDIT_PATH"],
        ],
        env=dict(os.environ),
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {"hwskill_search", "hwskill_load"}
            found = await session.call_tool(
                "hwskill_search", {"query": "debug failing Python boundary test"}
            )
            assert not found.isError
            assert "superpowers/systematic-debugging" in str(found.structuredContent)
            loaded = await session.call_tool(
                "hwskill_load", {"skill_id": "superpowers/systematic-debugging"}
            )
            assert not loaded.isError
            assert "x-hwskill-runtime" in str(loaded.structuredContent)

asyncio.run(main())
PY

if python3 -m unittest discover -s tests -v >/tmp/demo-tests.txt 2>&1; then
  echo "Demo unexpectedly passed before Live Eval" >&2
  exit 1
fi
grep -q 'test_discount_threshold_is_inclusive' /tmp/demo-tests.txt
grep -q '"event": "catalog"' "$HWSKILL_AUDIT_PATH"
grep -q '"event": "search"' "$HWSKILL_AUDIT_PATH"
grep -q '"event": "load"' "$HWSKILL_AUDIT_PATH"

unbound=$(mktemp -d /tmp/hwskill-unbound-XXXXXX)
hwskill skill search debug --project "$unbound" \
  --repo-root "$HWSKILL_REGISTRY_ROOT" --json >/tmp/unbound-search.json
grep -q '"results": \[\]' /tmp/unbound-search.json
if hwskill skill load superpowers/systematic-debugging --project "$unbound" \
    --repo-root "$HWSKILL_REGISTRY_ROOT" >/tmp/unbound-load.txt 2>&1; then
  echo "Unbound project unexpectedly loaded a skill" >&2
  exit 1
fi
grep -q 'not in the Effective Skill Catalog' /tmp/unbound-load.txt

echo "Docker offline smoke PASS"
