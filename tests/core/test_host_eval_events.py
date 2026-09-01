import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SKILL_ID = "local/gitcode-pr-review-fetch"
SKILL_FILE = "/opt/hwskills/skills-src/l2/local/gitcode-pr-review-fetch/SKILL.md"
SCRIPT = "/opt/hwskills/skills-src/l2/local/gitcode-pr-review-fetch/scripts/fetch_gitcode_pr_patch.py"
URL = "https://gitcode.com/openeuler/OmniStream/pull/587"
OUTPUT = "/workspace/pr-587.patch"


class HostEvalEventsTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "events.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, events):
        self.path.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )

    def observe(self, host):
        from hwskill.eval_observer import observe_script_resolution

        try:
            return observe_script_resolution(
                self.path, SKILL_ID, "fetch_gitcode_pr_patch.py",
                expected_url=URL, expected_output=OUTPUT, host=host,
            )
        except TypeError as exc:
            raise AssertionError("observer does not accept a host") from exc

    def test_claude_stream_json_is_normalized_into_direct_execution(self):
        command = f"python3 {SCRIPT} {URL} --output {OUTPUT}"
        self.write([
            {"type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": "search-1",
                "name": "mcp__hwskill__hwskill_search", "input": {"query": "PR review"},
            }]}},
            {"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": "search-1",
                "content": json.dumps({"results": [{"skill_id": SKILL_ID}]}),
            }]}},
            {"type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": "load-1",
                "name": "mcp__hwskill__hwskill_load", "input": {"skill_id": SKILL_ID},
            }]}},
            {"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": "load-1",
                "content": json.dumps({"structuredContent": {"skill_file": SKILL_FILE}}),
            }]}},
            {"type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": "bash-1", "name": "Bash",
                "input": {"command": command},
            }]}},
            {"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": "bash-1", "is_error": False,
                "content": "patch: state=open api_base=abc head=def123 files=2\n",
            }]}},
        ])

        result = self.observe("claude-code")

        self.assertEqual(result["host"], "claude-code")
        self.assertTrue(result["direct_resolution"])
        self.assertTrue(result["execution_succeeded"])
        self.assertEqual(result["patch_diagnostic"]["files"], 2)

    def test_claude_concurrent_load_and_script_is_not_reordered_into_direct_execution(self):
        command = f"python3 {SCRIPT} {URL} --output {OUTPUT}"
        self.write([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "search-1", "name": "mcp__hwskill__hwskill_search", "input": {"query": "PR review"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "search-1", "content": json.dumps({"results": [{"skill_id": SKILL_ID}]})},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "load-1", "name": "mcp__hwskill__hwskill_load", "input": {"skill_id": SKILL_ID}},
                {"type": "tool_use", "id": "bash-1", "name": "Bash", "input": {"command": command}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "bash-1", "content": "done"},
                {"type": "tool_result", "tool_use_id": "load-1", "content": json.dumps({"skill_file": SKILL_FILE})},
            ]}},
        ])

        with self.assertRaisesRegex(ValueError, "script invocation not found after completed"):
            self.observe("claude-code")

    def test_claude_discovery_concurrent_with_load_is_recorded_but_not_after_load(self):
        discovery = f"find /opt/hwskills -name {Path(SCRIPT).name}"
        command = f"python3 {SCRIPT} {URL} --output {OUTPUT}"
        self.write([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "search-1", "name": "mcp__hwskill__hwskill_search", "input": {"query": "PR review"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "search-1", "content": json.dumps({"results": [{"skill_id": SKILL_ID}]})},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "load-1", "name": "mcp__hwskill__hwskill_load", "input": {"skill_id": SKILL_ID}},
                {"type": "tool_use", "id": "find-1", "name": "Bash", "input": {"command": discovery}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "load-1", "content": json.dumps({"skill_file": SKILL_FILE})},
                {"type": "tool_result", "tool_use_id": "find-1", "content": SCRIPT},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "bash-1", "name": "Bash", "input": {"command": command}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "bash-1", "content": "done"},
            ]}},
        ])

        result = self.observe("claude-code")

        self.assertTrue(result["direct_resolution"])
        self.assertEqual(result["discovery_commands_before_invocation"], [discovery])
        self.assertEqual(result["discovery_commands_after_load"], [])

    def opencode_tool(self, tool, call_id, input_data, output, metadata=None):
        return {
            "type": "tool_use",
            "sessionID": "open-1",
            "part": {
                "type": "tool", "callID": call_id, "tool": tool,
                "state": {
                    "status": "completed", "input": input_data, "output": output,
                    "metadata": metadata or {},
                },
            },
        }

    def test_opencode_json_is_normalized_into_direct_execution(self):
        command = f"python3 {SCRIPT} {URL} --output={OUTPUT}"
        self.write([
            self.opencode_tool(
                "hwskill_search", "search-1", {"query": "PR review"},
                json.dumps({"results": [{"skill_id": SKILL_ID}]}),
            ),
            self.opencode_tool(
                "hwskill_load", "load-1", {"skill_id": SKILL_ID},
                json.dumps({"skill_file": SKILL_FILE}),
            ),
            self.opencode_tool(
                "bash", "bash-1", {"command": command},
                "patch: state=open api_base=abc head=def123 files=2\n",
                {"exitCode": 0},
            ),
        ])

        result = self.observe("opencode")

        self.assertEqual(result["host"], "opencode")
        self.assertTrue(result["direct_resolution"])
        self.assertEqual(result["parsed_script_argv"][1], SCRIPT)

    def test_opencode_directory_discovery_remains_allowed_but_is_not_direct(self):
        command = f"python3 {SCRIPT} {URL} --output {OUTPUT}"
        self.write([
            self.opencode_tool(
                "hwskill_search", "search-1", {"query": "PR review"},
                json.dumps({"results": [{"skill_id": SKILL_ID}]}),
            ),
            self.opencode_tool(
                "hwskill_load", "load-1", {"skill_id": SKILL_ID},
                json.dumps({"skill_file": SKILL_FILE}),
            ),
            self.opencode_tool(
                "bash", "find-1", {"command": f"find /opt/hwskills -name {Path(SCRIPT).name}"},
                SCRIPT + "\n", {"exitCode": 0},
            ),
            self.opencode_tool(
                "bash", "bash-1", {"command": command}, "done\n", {"exitCode": 0},
            ),
        ])

        result = self.observe("opencode")

        self.assertFalse(result["direct_resolution"])
        self.assertEqual(len(result["discovery_commands_before_invocation"]), 1)

    def test_opencode_real_exit_metadata_preserves_command_failure(self):
        from hwskill.eval_events import normalize_events

        self.write([
            self.opencode_tool(
                "bash", "bash-1", {"command": "false"}, "", {"exit": 1}
            )
        ])

        events = normalize_events(self.path, "opencode")

        self.assertEqual(events[0]["item"]["exit_code"], 1)
        self.assertEqual(events[0]["item"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
