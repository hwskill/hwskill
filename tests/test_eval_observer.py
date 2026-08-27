import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SKILL_FILE = "/opt/hwskills/skills-src/l2/local/gitcode-pr-review-fetch/SKILL.md"
SCRIPT = "/opt/hwskills/skills-src/l2/local/gitcode-pr-review-fetch/scripts/fetch_gitcode_pr_patch.py"


def completed(item):
    return {"type": "item.completed", "item": item}


class EvalObserverTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "codex.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write_events(self, events):
        self.path.write_text(
            "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8"
        )

    def observe(self):
        try:
            from hwskill.eval_observer import observe_script_resolution
        except ModuleNotFoundError:
            self.fail("eval observer is not implemented")
        return observe_script_resolution(
            self.path,
            "local/gitcode-pr-review-fetch",
            "fetch_gitcode_pr_patch.py",
            expected_url="https://gitcode.com/openeuler/OmniStream/pull/587",
            expected_output="/workspace/pr-587.patch",
        )

    def search_event(self):
        return completed({
            "type": "mcp_tool_call",
            "tool": "hwskill_search",
            "status": "completed",
            "result": {"structured_content": {"results": [{
                "skill_id": "local/gitcode-pr-review-fetch",
            }]}},
        })

    def load_event(self):
        return completed({
            "type": "mcp_tool_call",
            "tool": "hwskill_load",
            "arguments": {"skill_id": "local/gitcode-pr-review-fetch"},
            "status": "completed",
            "result": {"structured_content": {"skill_file": SKILL_FILE}},
        })

    def test_reports_direct_runtime_anchored_script_execution(self):
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({
                "type": "command_execution",
                "command": f"/bin/bash -lc 'python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch'",
                "exit_code": 0,
                "aggregated_output": "patch: state=open api_base=abc head=def123 files=2\n",
            }),
        ])
        result = self.observe()
        self.assertTrue(result["direct_resolution"])
        self.assertEqual(result["expected_script"], SCRIPT)
        self.assertEqual(result["discovery_commands_before_invocation"], [])

    def test_reports_directory_discovery_before_script_execution(self):
        discovery = "/bin/bash -lc 'find /opt/hwskills/skills-src -name fetch_gitcode_pr_patch.py'"
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({"type": "command_execution", "command": discovery, "exit_code": 0}),
            completed({
                "type": "command_execution",
                "command": f"/bin/bash -lc 'python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch'",
                "exit_code": 0,
            }),
        ])
        result = self.observe()
        self.assertFalse(result["direct_resolution"])
        self.assertEqual(result["discovery_commands_before_invocation"], [discovery])

    def test_reports_successful_retry_after_sandbox_failure(self):
        command = f"python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch"
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({
                "type": "command_execution",
                "command": command,
                "exit_code": 1,
                "status": "failed",
            }),
            completed({
                "type": "command_execution",
                "command": command,
                "exit_code": 0,
                "status": "completed",
            }),
        ])
        result = self.observe()
        self.assertTrue(result["direct_resolution"])
        self.assertEqual(result["script_invocation_attempts"], 2)
        self.assertTrue(result["execution_succeeded"])
        self.assertEqual(result["successful_script_invocation_command"], command)

    def test_rejects_command_that_only_mentions_script_path(self):
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({
                "type": "command_execution",
                "command": f"/bin/bash -lc 'echo {SCRIPT}'",
                "exit_code": 0,
            }),
        ])
        with self.assertRaisesRegex(ValueError, "script invocation not found"):
            self.observe()

    def test_reports_invocation_before_load_as_not_direct(self):
        command = f"python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch"
        self.write_events([
            completed({"type": "command_execution", "command": command, "exit_code": 0}),
            self.search_event(),
            self.load_event(),
            completed({"type": "command_execution", "command": command, "exit_code": 0}),
        ])
        result = self.observe()
        self.assertTrue(result["invocation_before_load"])
        self.assertFalse(result["direct_resolution"])

    def test_requires_completed_search_containing_target_skill(self):
        command = f"python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch"
        self.write_events([
            self.load_event(),
            completed({"type": "command_execution", "command": command, "exit_code": 0}),
        ])
        with self.assertRaisesRegex(ValueError, "completed hwskill_search"):
            self.observe()

    def test_requires_exact_pr_url_and_output_arguments(self):
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({
                "type": "command_execution",
                "command": f"python3 {SCRIPT} --help",
                "exit_code": 0,
            }),
        ])
        with self.assertRaisesRegex(ValueError, "script invocation not found"):
            self.observe()

    def test_reports_directory_discovery_before_load(self):
        discovery = "/bin/bash -lc 'find /opt/hwskills/skills-src -name fetch_gitcode_pr_patch.py'"
        command = f"python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch"
        self.write_events([
            self.search_event(),
            completed({"type": "command_execution", "command": discovery, "exit_code": 0}),
            self.load_event(),
            completed({"type": "command_execution", "command": command, "exit_code": 0}),
        ])
        result = self.observe()
        self.assertFalse(result["direct_resolution"])
        self.assertEqual(result["discovery_commands_before_invocation"], [discovery])

    def test_reports_discovery_after_unspaced_shell_separator(self):
        discovery = "/bin/bash -lc 'pwd;find /opt/hwskills/skills-src -name fetch_gitcode_pr_patch.py'"
        command = f"python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch"
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({"type": "command_execution", "command": discovery, "exit_code": 0}),
            completed({"type": "command_execution", "command": command, "exit_code": 0}),
        ])
        result = self.observe()
        self.assertFalse(result["direct_resolution"])
        self.assertEqual(result["discovery_commands_before_invocation"], [discovery])

    def test_reports_discovery_after_shell_newline(self):
        discovery = "/bin/bash -lc 'pwd\nfind /opt/hwskills/skills-src -name SKILL.md'"
        command = f"python3 {SCRIPT} https://gitcode.com/openeuler/OmniStream/pull/587 --output /workspace/pr-587.patch"
        self.write_events([
            self.search_event(),
            self.load_event(),
            completed({"type": "command_execution", "command": discovery, "exit_code": 0}),
            completed({"type": "command_execution", "command": command, "exit_code": 0}),
        ])
        result = self.observe()
        self.assertFalse(result["direct_resolution"])
        self.assertEqual(result["discovery_commands_before_invocation"], [discovery])


if __name__ == "__main__":
    unittest.main()
