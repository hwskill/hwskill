import json
from dataclasses import replace
import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class _CompletedProcess:
    returncode = 0

    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


class _Process:
    pid = 12345
    returncode = 0

    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)

    def wait(self, timeout=None):
        return self.returncode


def adjacent_pairs(values):
    return set(zip(values, values[1:]))


class TestAgentHostTest(unittest.TestCase):
    def setUp(self) -> None:
        from hwskill.test_manifest import AgentAction
        from hwskill.test_runner import ActionContext, TestEnvironment

        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "project").mkdir()
        self.artifact_dir = root / "artifacts"
        self.artifact_dir.mkdir()
        self.action = AgentAction("run-agent", "complete the task", "project")
        self.environment = TestEnvironment(
            repo_root=Path(__file__).parents[2],
            runner="local",
            host="codex",
            model="gpt-5.6-terra",
            reasoning="high",
            secret_values=("super-secret",),
        )
        self.context = ActionContext(self.workspace, self.artifact_dir, self.environment)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_codex_action_uses_configured_model_reasoning_workdir_and_json_events(self) -> None:
        from hwskill.test_agent import CodexTestHost

        command = CodexTestHost().build_command(self.context, self.action, model="gpt-5.6-terra", reasoning="high")

        self.assertIn(("--model", "gpt-5.6-terra"), adjacent_pairs(command))
        self.assertIn(("-c", 'model_reasoning_effort="high"'), adjacent_pairs(command))
        self.assertIn("--json", command)
        self.assertIn(("--cd", str(self.workspace / "project")), adjacent_pairs(command))
        self.assertIn("--skip-git-repo-check", command)
        self.assertNotIn("--output-last-message", command)
        self.assertEqual(command[-1], self.action.prompt)
        self.assertNotIn("dangerously-bypass-approvals-and-sandbox", " ".join(command))

    def test_claude_and_opencode_actions_are_noninteractive_json_without_danger_bypass(self) -> None:
        from hwskill.test_agent import ClaudeCodeTestHost, OpenCodeTestHost

        for host in (ClaudeCodeTestHost(), OpenCodeTestHost()):
            command = host.build_command(self.context, self.action, model="configured-model", reasoning="medium")
            rendered = " ".join(command)
            self.assertIn("configured-model", command)
            self.assertNotIn("dangerously", rendered)
            self.assertNotIn(str(self.workspace), rendered)
            self.assertEqual(command[-1], self.action.prompt)

    def test_non_git_workspace_codex_command_skips_only_the_git_precondition(self) -> None:
        from hwskill.test_agent import CodexTestHost

        self.assertFalse((self.workspace / ".git").exists())
        command = CodexTestHost().build_command(
            self.context, self.action, model="model", reasoning="low",
        )

        self.assertIn("--skip-git-repo-check", command)
        self.assertNotIn("dangerously-bypass-approvals-and-sandbox", " ".join(command))

    def test_executor_streams_only_observable_redacted_events_and_final_response(self) -> None:
        from hwskill.test_agent import AgentExecutor

        raw_events = "\n".join((
            json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "hidden thought super-secret"}}),
            json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "printf super-secret",
                "exit_code": 0, "status": "completed", "aggregated_output": "super-secret",
            }}),
            json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "final super-secret",
            }}),
        )) + "\n"
        setup_calls = []
        from hwskill.test_agent import CodexTestHost

        executor = AgentExecutor(
            CodexTestHost(),
            credential_available=lambda _host: True,
            setup_runner=lambda command, **kwargs: setup_calls.append((command, kwargs)) or _CompletedProcess(),
        )

        original_write_text = Path.write_text

        def reject_raw_path(path, *args, **kwargs):
            self.assertNotEqual(path.suffix, ".raw")
            return original_write_text(path, *args, **kwargs)

        with patch.object(Path, "write_text", new=reject_raw_path), patch(
            "hwskill.test_agent.subprocess.Popen", return_value=_Process(raw_events)
        ):
            result = executor.run(self.action, self.context)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(setup_calls), 1)
        self.assertIn("--project", setup_calls[0][0])
        self.assertIn(str(self.workspace), setup_calls[0][0])
        self.assertIn("--repo-root", setup_calls[0][0])
        event_text = (self.artifact_dir / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("command_execution", event_text)
        self.assertNotIn("reasoning", event_text)
        self.assertNotIn("super-secret", event_text)
        final = (self.artifact_dir / "final-response.md").read_text(encoding="utf-8")
        self.assertEqual(final, "final [REDACTED]")
        self.assertFalse(any(path.suffix == ".raw" for path in self.artifact_dir.iterdir()))
        for path in self.artifact_dir.iterdir():
            self.assertNotIn("super-secret", path.read_text(encoding="utf-8"))

    def test_agent_process_runs_inside_its_filesystem_boundary(self) -> None:
        from hwskill.test_agent import AgentExecutor, CodexTestHost

        environment = replace(self.environment, agent_command_prefix=("guard", "--allow-network", "--"))
        context = replace(self.context, environment=environment)
        executor = AgentExecutor(
            CodexTestHost(), credential_available=lambda _host: True,
            setup_runner=lambda *_args, **_kwargs: _CompletedProcess(),
        )
        process = _Process(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}) + "\n")

        with patch("hwskill.test_agent.subprocess.Popen", return_value=process) as popen:
            result = executor.run(self.action, context)

        self.assertEqual(result.status, "completed")
        self.assertEqual(popen.call_args.args[0][:3], ("guard", "--allow-network", "--"))
        self.assertEqual(popen.call_args.args[0][3:5], ("codex", "exec"))
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--cd") + 1], ".")

    def test_agent_guard_is_narrowed_to_its_single_workspace_not_the_workspace_pool(self) -> None:
        from hwskill.test_agent import AgentExecutor, CodexTestHost

        pool = self.workspace.parent
        environment = replace(
            self.environment,
            workspace_root=pool,
            agent_command_prefix=("guard", "--read-write", str(pool), "--"),
        )
        context = replace(self.context, environment=environment)
        executor = AgentExecutor(
            CodexTestHost(), credential_available=lambda _host: True,
            setup_runner=lambda *_args, **_kwargs: _CompletedProcess(),
        )
        process = _Process("")
        with patch("hwskill.test_agent.subprocess.Popen", return_value=process) as popen:
            result = executor.run(self.action, context)

        self.assertEqual(result.status, "completed")
        command = popen.call_args.args[0]
        self.assertIn(str(self.workspace), command)
        self.assertNotIn(str(pool), command)

    def test_codex_cd_uses_the_opened_workdir_fd_after_path_swap(self) -> None:
        from hwskill.test_agent import AgentExecutor

        safe_workdir = self.workspace / "project-safe"
        external = Path(self.temp.name) / "external"
        external.mkdir()

        def swap_after_anchor(_workspace, _configured):
            (self.workspace / "project").rename(safe_workdir)
            (self.workspace / "project").symlink_to(external, target_is_directory=True)

        captured = {}

        def fake_popen(command, **_kwargs):
            captured["command"] = command
            cd_path = command[command.index("--cd") + 1]
            Path(cd_path, "anchored-write").write_text("inside", encoding="utf-8")
            return _Process("")

        executor = AgentExecutor(
            credential_available=lambda _host: True,
            setup_runner=lambda *_args, **_kwargs: _CompletedProcess(),
        )
        with patch("hwskill.test_agent._after_workdir_opened", side_effect=swap_after_anchor), patch(
            "hwskill.test_agent.subprocess.Popen", side_effect=fake_popen
        ):
            result = executor.run(self.action, self.context)

        self.assertEqual(result.status, "completed")
        command = captured["command"]
        self.assertTrue(command[command.index("--cd") + 1].startswith("/proc/self/fd/"))
        self.assertTrue((safe_workdir / "anchored-write").is_file())
        self.assertFalse((external / "anchored-write").exists())

    def test_missing_credentials_blocks_without_setup_or_model_process(self) -> None:
        from hwskill.test_agent import AgentExecutor

        setup = unittest.mock.Mock()
        executor = AgentExecutor(credential_available=lambda _host: False, setup_runner=setup)
        with patch("hwskill.test_agent.subprocess.Popen") as popen:
            result = executor.run(self.action, self.context)

        self.assertEqual(result.status, "blocked")
        self.assertIn("credentials", (self.artifact_dir / "result.json").read_text(encoding="utf-8"))
        setup.assert_not_called()
        popen.assert_not_called()

    def test_claude_final_response_is_saved_separately_without_raw_stream(self) -> None:
        from hwskill.test_agent import AgentExecutor, ClaudeCodeTestHost

        context = replace(self.context, environment=replace(self.environment, host="claude-code"))
        raw_events = json.dumps({"type": "result", "result": "final super-secret"}) + "\n"
        executor = AgentExecutor(
            ClaudeCodeTestHost(), credential_available=lambda _host: True,
            setup_runner=lambda *_args, **_kwargs: _CompletedProcess(),
        )

        with patch("hwskill.test_agent.subprocess.Popen", return_value=_Process(raw_events)):
            result = executor.run(self.action, context)

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            (self.artifact_dir / "final-response.md").read_text(encoding="utf-8"),
            "final [REDACTED]",
        )
        self.assertNotIn("result", (self.artifact_dir / "events.jsonl").read_text(encoding="utf-8"))

    def test_final_response_excludes_claude_tool_result_content(self) -> None:
        from hwskill.test_agent import _final_response

        response = _final_response((
            {"type": "user", "message": {"content": [{"type": "text", "text": "tool secret"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "final answer"}]}},
        ))

        self.assertEqual(response, "final answer")

    def test_timeout_blocks_and_terminates_the_agent_process_group(self) -> None:
        from hwskill.test_agent import AgentExecutor

        class TimeoutProcess(_Process):
            def __init__(self):
                super().__init__("")
                self.calls = 0

            def wait(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("agent", timeout)
                return self.returncode

        process = TimeoutProcess()
        executor = AgentExecutor(
            credential_available=lambda _host: True,
            setup_runner=lambda *_args, **_kwargs: _CompletedProcess(),
        )
        with patch("hwskill.test_agent.subprocess.Popen", return_value=process), patch(
            "hwskill.test_agent._terminate_process_group"
        ) as terminate:
            result = executor.run(self.action, self.context)

        self.assertEqual(result.status, "blocked")
        self.assertIsNone(result.exit_code)
        terminate.assert_called_once_with(process)

    def test_repeated_agent_actions_do_not_leak_anchored_workdir_descriptors(self) -> None:
        from hwskill.test_agent import AgentExecutor

        executor = AgentExecutor(
            credential_available=lambda _host: True,
            setup_runner=lambda *_args, **_kwargs: _CompletedProcess(),
        )
        before = len(list(Path("/proc/self/fd").iterdir()))
        with patch("hwskill.test_agent.subprocess.Popen", return_value=_Process("")):
            for _ in range(32):
                result = executor.run(self.action, self.context)

        after = len(list(Path("/proc/self/fd").iterdir()))
        self.assertEqual(result.status, "completed")
        self.assertLessEqual(after, before + 1)


class AgentPostCheckTest(unittest.TestCase):
    def test_post_check_requires_exact_json_schema(self) -> None:
        from hwskill.test_agent import parse_agent_post_check

        self.assertEqual(parse_agent_post_check('{"result":"pass","evidence":["verified"]}'), "PASS")
        self.assertEqual(parse_agent_post_check('{"result":"fail","evidence":["missing output"]}'), "FAIL")
        for invalid in (
            "not json",
            "[]",
            '{"result":"pass","evidence":[]}',
            '{"result":"pass","evidence":[""]}',
            '{"result":"blocked","evidence":["no"]}',
            '{"result":"pass","evidence":["ok"],"extra":true}',
        ):
            self.assertEqual(parse_agent_post_check(invalid), "BLOCKED", invalid)

    def test_agent_post_check_verdict_controls_case_status_and_receives_readonly_metadata(self) -> None:
        from hwskill.test_artifacts import ActionResult
        from hwskill.test_manifest import AgentAction, CommandAction, TestCase
        from hwskill.test_runner import TestEnvironment, run_case

        class PostCheckExecutor:
            def run(self, action, context):
                self.prompt = action.prompt
                (context.artifact_dir / "final-response.md").write_text(
                    '{"result":"fail","evidence":["business check failed"]}', encoding="utf-8"
                )
                return ActionResult(action.action_id, "completed", 0, context.artifact_dir)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            executor = PostCheckExecutor()
            case = TestCase(
                case_id="agent-post-check", description=None, workdir=None, prepare=None,
                steps=(CommandAction("step", "true"),),
                post_check=AgentAction("post-check", "judge the result"),
            )
            environment = TestEnvironment(
                repo_root=Path(__file__).parents[2], runner="local", host="codex",
                model="model", reasoning="low", fixtures_dir=fixtures,
            )

            result = run_case(case, environment, root / "artifacts", agent_executor=executor)

        self.assertEqual(result.status, "FAIL")
        self.assertIn("[harness metadata: read-only]", executor.prompt)
        self.assertIn("HWSKILL_TEST_CONTEXT=", executor.prompt)
        self.assertIn("HWSKILL_TEST_ARTIFACTS=", executor.prompt)
        self.assertIn("HWSKILL_TEST_WORKSPACE=", executor.prompt)

    def test_failed_agent_post_check_cannot_turn_partial_pass_response_into_case_pass(self) -> None:
        from hwskill.test_artifacts import ActionResult
        from hwskill.test_manifest import AgentAction, CommandAction, TestCase
        from hwskill.test_runner import TestEnvironment, run_case

        class FailedPostCheckExecutor:
            def run(self, action, context):
                (context.artifact_dir / "final-response.md").write_text(
                    '{"result":"pass","evidence":["partial response"]}', encoding="utf-8"
                )
                return ActionResult(action.action_id, "failed", 1, context.artifact_dir)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            case = TestCase(
                case_id="failed-agent-post-check", description=None, workdir=None, prepare=None,
                steps=(CommandAction("step", "true"),),
                post_check=AgentAction("post-check", "judge the result"),
            )
            environment = TestEnvironment(
                repo_root=Path(__file__).parents[2], runner="local", host="codex",
                model="model", reasoning="low", fixtures_dir=fixtures,
            )

            result = run_case(
                case, environment, root / "artifacts", agent_executor=FailedPostCheckExecutor(),
            )

        self.assertEqual(result.status, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
