from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from argparse import Namespace

import yaml

from hwskill.cli import main
from hwskill.test_configuration import HostModel, TestConfiguration
from hwskill.test_impact import TestSelection


class TestCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self.config = TestConfiguration(
            runner="local",
            default_host="codex",
            hosts={"codex": HostModel("test-model", "minimal")},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
        return int(code), stdout.getvalue(), stderr.getvalue()

    def manifest(self, relative: str, *, kind: str, target_id: str, post_check: str = "true") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": kind, "id": target_id},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "id": "step", "command": "true"}],
                "post_check": {"type": "command", "id": "post", "command": post_check},
            }],
        }, sort_keys=False), encoding="utf-8")
        return path

    def command(self, *args: str) -> tuple[str, ...]:
        return ("test", *args, "--repo-root", str(self.repo))

    def test_direct_test_path_is_restricted_to_test_roots(self) -> None:
        code, _, error = self.run_cli(*self.command("../outside.yaml", "--runner", "local"))

        self.assertEqual(code, 2)
        self.assertIn("tests/core, tests/skills, or tests/profiles", error)

    def test_all_and_targeted_skill_profile_paths_use_explicit_local_runner(self) -> None:
        self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        self.manifest("tests/profiles/review/test.yaml", kind="profile", target_id="review")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            all_code, all_output, _ = self.run_cli(*self.command("all", "--runner", "local"))
            skill_code, skill_output, _ = self.run_cli(*self.command("skills", "team/review", "--runner", "local"))
            profile_code, profile_output, _ = self.run_cli(*self.command("profiles", "review", "--runner", "local"))

        self.assertEqual(all_code, 0)
        self.assertIn("COLLECTION  skill:team/review", all_output)
        self.assertEqual(skill_code, 0)
        self.assertIn("skill:team/review", skill_output)
        self.assertEqual(profile_code, 0)
        self.assertIn("profile:review", profile_output)

    def test_json_summary_includes_run_and_action_artifact_metadata(self) -> None:
        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, output, _ = self.run_cli(*self.command(manifest.relative_to(self.repo).as_posix(), "--runner", "local", "--json"))

        data = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "PASS")
        self.assertEqual(data["runner"], "local")
        self.assertEqual(data["host"], "codex")
        self.assertEqual(data["model"], "test-model")
        self.assertEqual(data["collections"][0]["cases"][0]["post_check"]["status"], "completed")
        self.assertTrue(data["collections"][0]["cases"][0]["artifact_dir"])

    def test_fail_blocked_and_unavailable_docker_have_stable_exit_codes(self) -> None:
        failing = self.manifest("tests/skills/team/failing/test.yaml", kind="skill", target_id="team/failing", post_check="false")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            failed, _, _ = self.run_cli(*self.command(failing.relative_to(self.repo).as_posix(), "--runner", "local"))
            blocked, _, _ = self.run_cli(*self.command("skills", "--runner", "docker"))

        self.assertEqual(failed, 1)
        self.assertEqual(blocked, 3)

    def test_setup_check_uses_the_setup_service_without_building_a_docker_runner(self) -> None:
        from hwskill.test_setup import SetupCheck, SetupReport

        report = SetupReport("BLOCKED", (SetupCheck("docker-daemon", "BLOCKED", "Docker daemon unavailable"),))
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_cli.inspect_test_setup", return_value=report
        ) as inspected:
            code, output, _ = self.run_cli(*self.command("setup", "--check", "--json"))

        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["checks"][0]["name"], "docker-daemon")
        inspected.assert_called_once()

    def test_affected_uses_base_selection_and_only_clears_exact_complete_pass(self) -> None:
        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        selection = TestSelection(
            skill_ids=("team/review",), collection_paths=(manifest.relative_to(self.repo),),
            digests=(("team/review", "sha256:" + "a" * 64),),
        )
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_cli.select_affected_tests", return_value=selection
        ) as selected, patch("hwskill.test_cli.clear_pending_verification", return_value=True) as cleared:
            code, output, _ = self.run_cli(*self.command("affected", "--base", "HEAD^", "--runner", "local"))

        self.assertEqual(code, 0)
        self.assertIn("PENDING     CLEARED", output)
        selected.assert_called_once_with(self.repo.resolve(), "HEAD^")
        cleared.assert_called_once()

    def test_test_command_does_not_call_integrity_check(self) -> None:
        self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.cli.run_integrity_command", side_effect=AssertionError("integrity must be explicit")
        ):
            code, output, error = self.run_cli(*self.command("skills", "--runner", "local"))

        self.assertEqual(code, 0, error)
        self.assertNotIn("INTEGRITY", output)

    def test_standard_runner_is_an_injected_execution_boundary(self) -> None:
        from hwskill import test_cli

        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        args = Namespace(
            test_target=manifest.relative_to(self.repo).as_posix(), test_id=None,
            runner="docker", host=None, base=None, check=False, json=True,
        )

        class Boundary:
            def __init__(self) -> None:
                self.called = False

            def run(self, collections, environment, artifact_root):
                self.called = True
                self.assertEqual(len(collections), 1)
                return test_cli.TestRunResult(
                    "PASS", artifact_root, (), environment.runner, environment.host,
                    environment.model, "injected",
                )

            def assertEqual(self, left, right) -> None:
                TestCliTest.assertEqual(self_test, left, right)

        self_test = self
        boundary = Boundary()
        stdout = StringIO()
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code = test_cli.run_test_command(args, self.repo, stdout, StringIO(), execution_boundary=boundary)

        self.assertEqual(code, 0)
        self.assertTrue(boundary.called)
        self.assertEqual(json.loads(stdout.getvalue())["runner"], "docker")


if __name__ == "__main__":
    unittest.main()
