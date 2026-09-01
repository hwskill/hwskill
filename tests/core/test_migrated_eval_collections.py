from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).parents[2]
PR_URL = "https://gitcode.com/openeuler/OmniStream/pull/587"


def _completed(item: dict[str, object]) -> dict[str, object]:
    return {"type": "item.completed", "item": item}


class MigratedEvalCollectionTest(unittest.TestCase):
    def test_repository_collections_are_discoverable_with_declared_actions(self) -> None:
        from hwskill.test_manifest import AgentAction, CommandAction, discover_test_collections

        collections = discover_test_collections(ROOT)
        targets = {(item.target.kind, item.target.target_id): item for item in collections}
        gitcode = targets[("skill", "local/gitcode-pr-review-fetch")]
        demo = targets[("profile", "codex-demo")]

        self.assertEqual(gitcode.cases[0].case_id, "fetch-pr-587")
        self.assertIsInstance(gitcode.cases[0].prepare, CommandAction)
        self.assertIsInstance(gitcode.cases[0].steps[0], AgentAction)
        self.assertIsInstance(gitcode.cases[0].post_check, CommandAction)
        self.assertNotIn("model", (ROOT / "tests/skills/local/gitcode-pr-review-fetch/test.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("host:", (ROOT / "tests/skills/local/gitcode-pr-review-fetch/test.yaml").read_text(encoding="utf-8"))

        self.assertEqual(demo.cases[0].case_id, "repair-inclusive-discount-boundary")
        self.assertIsInstance(demo.cases[0].steps[0], AgentAction)
        self.assertIsInstance(demo.cases[0].steps[1], CommandAction)

    def test_gitcode_post_check_requires_runtime_resolution_patch_and_diagnostics(self) -> None:
        script = ROOT / "tests/skills/local/gitcode-pr-review-fetch/scripts/post_check.py"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "pr-587.patch").write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
            artifacts = root / "artifacts"
            agent = artifacts / "actions/run-agent"
            agent.mkdir(parents=True)
            skill_file = "/registry/skills-src/l2/local/gitcode-pr-review-fetch/SKILL.md"
            event_path = agent / "events.jsonl"
            event_path.write_text("".join(json.dumps(event) + "\n" for event in (
                _completed({
                    "type": "mcp_tool_call", "tool": "hwskill_search", "status": "completed",
                    "result": {"structured_content": {"results": [{"skill_id": "local/gitcode-pr-review-fetch"}]}},
                }),
                _completed({
                    "type": "mcp_tool_call", "tool": "hwskill_load", "status": "completed",
                    "arguments": {"skill_id": "local/gitcode-pr-review-fetch"},
                    "result": {"structured_content": {"skill_file": skill_file}},
                }),
                _completed({
                    "type": "command_execution", "exit_code": 0,
                    "command": f"python3 {Path(skill_file).parent / 'scripts/fetch_gitcode_pr_patch.py'} {PR_URL} --output pr-587.patch",
                    "aggregated_output": "patch: state=open api_base=api head=abc123 files=1\n",
                }),
            )), encoding="utf-8")
            context = {
                "case_id": "fetch-pr-587",
                "actions": {"run-agent": {"status": "completed", "exit_code": 0, "artifact_dir": "actions/run-agent"}},
            }
            context_path = artifacts / "context.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")

            result = self._post_check(script, context_path, artifacts, workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            resolution = json.loads((artifacts / "script-resolution.json").read_text(encoding="utf-8"))
            self.assertTrue(resolution["direct_resolution"])
            self.assertTrue(resolution["execution_succeeded"])
            self.assertEqual(resolution["patch_diagnostic"]["files"], 1)
            self.assertEqual(resolution["patch_path"], str(workspace / "pr-587.patch"))
            (workspace / "pr-587.patch").unlink()
            self.assertEqual(self._post_check(script, context_path, artifacts, workspace).returncode, 1)

    def test_profile_post_check_requires_business_tests_and_inclusive_boundary_diff(self) -> None:
        script = ROOT / "tests/profiles/codex-demo/scripts/post_check.py"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "order_pricing.py").write_text(
                "if subtotal >= discount_threshold:\n    return subtotal\n", encoding="utf-8",
            )
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "workspace.diff").write_text(
                "+    if subtotal >= discount_threshold:\n", encoding="utf-8",
            )
            context_path = artifacts / "context.json"
            context_path.write_text(json.dumps({
                "case_id": "repair-inclusive-discount-boundary",
                "actions": {
                    "run-agent": {"status": "completed", "exit_code": 0, "artifact_dir": "actions/run-agent"},
                    "run-business-tests": {"status": "completed", "exit_code": 0, "artifact_dir": "actions/run-business-tests"},
                },
            }), encoding="utf-8")

            result = self._post_check(script, context_path, artifacts, workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            (artifacts / "workspace.diff").write_text("", encoding="utf-8")
            self.assertEqual(self._post_check(script, context_path, artifacts, workspace).returncode, 1)

    def test_legacy_wrappers_forward_exact_collection_path_and_preserve_blocked_exit(self) -> None:
        wrappers = {
            "run_codex_live_eval.sh": ("tests/profiles/codex-demo/test.yaml", "codex", {"CODEX_API_KEY": "test-key"}),
            "run_gitcode_pr_agent_eval.sh": ("tests/skills/local/gitcode-pr-review-fetch/test.yaml", "codex", {"CODEX_API_KEY": "test-key"}),
            "run_claude_code_gitcode_pr_agent_eval.sh": ("tests/skills/local/gitcode-pr-review-fetch/test.yaml", "claude-code", {}),
            "run_opencode_gitcode_pr_agent_eval.sh": ("tests/skills/local/gitcode-pr-review-fetch/test.yaml", "opencode", {}),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            venv = root / ".venv/bin"
            venv.mkdir(parents=True)
            fake = venv / "hwskill"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\nexit 3\n", encoding="utf-8")
            fake.chmod(0o755)
            login = root / "opencode-auth.json"
            login.write_text("{}\n", encoding="utf-8")
            for name, (path, host, extra) in wrappers.items():
                shutil.copy2(ROOT / "scripts" / name, scripts / name)
                environment = {
                    "PATH": os.environ.get("PATH", ""),
                    "OPENCODE_AUTH_FILE": str(login),
                    **extra,
                }
                completed = subprocess.run(
                    ("/bin/sh", str(scripts / name), "--json"), text=True,
                    capture_output=True, check=False, env=environment,
                )
                with self.subTest(name=name):
                    self.assertEqual(completed.returncode, 3, completed.stderr)
                    self.assertIn(
                        f"test {path} --runner docker --host {host} --repo-root",
                        completed.stdout,
                    )
                    self.assertTrue(completed.stdout.rstrip().endswith("--json"))

    @staticmethod
    def _post_check(script: Path, context: Path, artifacts: Path, workspace: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("python3", str(script)), text=True, capture_output=True, check=False,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(ROOT / "src"),
                "HWSKILL_TEST_CONTEXT": str(context),
                "HWSKILL_TEST_ARTIFACTS": str(artifacts),
                "HWSKILL_TEST_WORKSPACE": str(workspace),
                "HWSKILL_TEST_REPO_ROOT": str(ROOT),
            },
        )


if __name__ == "__main__":
    unittest.main()
