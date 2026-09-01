from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from argparse import Namespace

import yaml

from hwskill.cli import main
from hwskill.test_configuration import HostModel, TestConfiguration, TestConfigurationError
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
        self.host_version_probe = patch(
            "hwskill.test_cli._probe_local_host_version", return_value="0.147.0"
        )
        self.host_version_probe.start()

    def tearDown(self) -> None:
        self.host_version_probe.stop()
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
        self.assertIn("Collection  skill:team/review", all_output)
        self.assertIn("Trust       local debugging only; not immutable or security evidence", all_output)
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
            "hwskill.test_cli.configure_test_setup", return_value=report
        ) as configured:
            code, output, _ = self.run_cli(*self.command("setup", "--check", "--json"))

        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["checks"][0]["name"], "docker-daemon")
        configured.assert_called_once()

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
        self.assertIn("Pending     cleared", output)
        selected.assert_called_once_with(self.repo.resolve(), "HEAD^")
        cleared.assert_called_once()

    def test_affected_does_not_clear_pending_when_docker_binding_blocks_forged_result(self) -> None:
        from hwskill import test_cli

        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        selection = TestSelection(
            skill_ids=("team/review",), collection_paths=(manifest.relative_to(self.repo),),
            digests=(("team/review", "sha256:" + "a" * 64),),
        )
        boundary = Mock()
        boundary.run.return_value = test_cli.TestRunResult(
            "BLOCKED", self.repo / "artifacts", (), "docker", "codex", "test-model", "unavailable",
            blocked_reason="Docker worker result selection does not match request",
        )
        docker_config = TestConfiguration(
            runner="docker", default_host="codex", hosts={"codex": HostModel("test-model", "minimal")},
        )
        with patch("hwskill.test_cli.load_test_configuration", return_value=docker_config), patch(
            "hwskill.test_cli.select_affected_tests", return_value=selection,
        ), patch("hwskill.test_cli.clear_pending_verification") as cleared:
            args = Namespace(
                test_target="affected", test_id=None, runner="docker", host=None, base="HEAD^",
                check=False, json=True, model=None, reasoning=None,
            )
            code = test_cli.run_test_command(
                args, self.repo, StringIO(), StringIO(), execution_boundary=boundary,
            )

        self.assertEqual(code, 3)
        cleared.assert_not_called()

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

    def test_direct_core_root_and_file_use_one_real_core_action_without_post_check(self) -> None:
        core = self.repo / "tests/core"
        core.mkdir(parents=True)
        test_file = core / "test_safe.py"
        test_file.write_text(
            "import unittest\nclass Safe(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            root_code, root_output, root_error = self.run_cli(*self.command("tests/core", "--runner", "local", "--json"))
            file_code, file_output, file_error = self.run_cli(*self.command("tests/core/test_safe.py", "--runner", "local", "--json"))

        self.assertEqual(root_code, 0, root_error)
        self.assertEqual(file_code, 0, file_error)
        data = json.loads(file_output)
        case = data["collections"][0]["cases"][0]
        self.assertIsNone(case["post_check"])
        self.assertEqual([action["id"] for action in case["actions"]], ["unittest"])
        action_dir = Path(case["actions"][0]["artifact_dir"])
        self.assertTrue((action_dir / "result.json").is_file())
        self.assertTrue((action_dir / "stdout.log").is_file())
        self.assertTrue((action_dir / "stderr.log").is_file())
        self.assertNotIn("POST", root_output)

    def test_core_path_swap_after_descriptor_anchor_blocks_without_running_external_test(self) -> None:
        core = self.repo / "tests/core"
        core.mkdir(parents=True)
        (core / "test_safe.py").write_text(
            "import unittest\nclass Safe(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        marker = outside / "executed"
        (outside / "test_external.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n",
            encoding="utf-8",
        )

        def swap_after_anchor(_root, _core):
            core.rename(self.repo / "tests/core-safe")
            core.symlink_to(outside, target_is_directory=True)

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_cli._after_core_directories_opened", side_effect=swap_after_anchor
        ):
            code, _, error = self.run_cli(*self.command("tests/core", "--runner", "local"))

        self.assertEqual(code, 3, error)
        self.assertFalse(marker.exists())

    def test_core_file_swap_after_anchor_blocks_without_running_external_test(self) -> None:
        core = self.repo / "tests/core"
        core.mkdir(parents=True)
        selected = core / "test_safe.py"
        selected.write_text(
            "import unittest\nclass Safe(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        outside = Path(self.temporary.name) / "outside.py"
        marker = Path(self.temporary.name) / "outside-executed"
        outside.write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n",
            encoding="utf-8",
        )

        def swap_selected_file(_root, _core):
            selected.unlink()
            selected.symlink_to(outside)

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_cli._after_core_directories_opened", side_effect=swap_selected_file
        ):
            code, _, error = self.run_cli(*self.command("tests/core/test_safe.py", "--runner", "local"))

        self.assertEqual(code, 3, error)
        self.assertFalse(marker.exists())

    def test_nested_package_direct_core_file_runs_only_the_selected_same_named_test(self) -> None:
        core = self.repo / "tests/core"
        marker_a = Path(self.temporary.name) / "a-ran"
        marker_b = Path(self.temporary.name) / "b-ran"
        for directory, marker in (("a", marker_a), ("b", marker_b)):
            package = core / directory
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "test_same.py").write_text(
                "import unittest\nfrom pathlib import Path\n"
                "class Exact(unittest.TestCase):\n"
                "    def test_exact(self): Path(" + repr(str(marker)) + ").write_text('ran')\n",
                encoding="utf-8",
            )

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, _, error = self.run_cli(*self.command("tests/core/a/test_same.py", "--runner", "local"))

        self.assertEqual(code, 0, error)
        self.assertTrue(marker_a.exists())
        self.assertFalse(marker_b.exists())

    def test_nested_non_package_direct_core_file_runs_the_selected_test(self) -> None:
        core = self.repo / "tests/core"
        marker_a = Path(self.temporary.name) / "non-package-a-ran"
        marker_b = Path(self.temporary.name) / "non-package-b-ran"
        for directory, marker in (("a", marker_a), ("b", marker_b)):
            test_dir = core / directory
            test_dir.mkdir(parents=True)
            (test_dir / "test_same.py").write_text(
                "import unittest\nfrom pathlib import Path\n"
                "class Exact(unittest.TestCase):\n"
                "    def test_exact(self): Path(" + repr(str(marker)) + ").write_text('ran')\n",
                encoding="utf-8",
            )

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, _, error = self.run_cli(*self.command("tests/core/a/test_same.py", "--runner", "local"))

        self.assertEqual(code, 0, error)
        self.assertTrue(marker_a.exists())
        self.assertFalse(marker_b.exists())

    def test_direct_core_file_with_no_tests_is_not_a_pass(self) -> None:
        core = self.repo / "tests/core"
        core.mkdir(parents=True)
        (core / "test_empty.py").write_text("def helper():\n    return True\n", encoding="utf-8")

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, output, error = self.run_cli(*self.command("tests/core/test_empty.py", "--runner", "local", "--json"))

        self.assertEqual(code, 1, error)
        action = json.loads(output)["collections"][0]["cases"][0]["actions"][0]
        self.assertEqual(action["status"], "failed")

    def test_all_runs_only_nested_non_package_core_tests(self) -> None:
        nested = self.repo / "tests/core/nested"
        nested.mkdir(parents=True)
        marker = Path(self.temporary.name) / "nested-all-ran"
        (nested / "test_nested.py").write_text(
            "import unittest\nfrom pathlib import Path\n"
            "class Nested(unittest.TestCase):\n"
            "    def test_nested(self): Path(" + repr(str(marker)) + ").write_text('ran')\n",
            encoding="utf-8",
        )

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, output, error = self.run_cli(*self.command("all", "--runner", "local", "--json"))

        self.assertEqual(code, 0, error)
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(output)["collections"][0]["target"], "core")

    def test_core_root_runs_nested_non_package_same_named_files(self) -> None:
        core = self.repo / "tests/core"
        markers = []
        for directory in ("a", "b"):
            marker = Path(self.temporary.name) / f"{directory}-root-ran"
            markers.append(marker)
            test_dir = core / directory
            test_dir.mkdir(parents=True)
            (test_dir / "test_same.py").write_text(
                "import unittest\nfrom pathlib import Path\n"
                "class Nested(unittest.TestCase):\n"
                "    def test_nested(self): Path(" + repr(str(marker)) + ").write_text('ran')\n",
                encoding="utf-8",
            )

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, _, error = self.run_cli(*self.command("tests/core", "--runner", "local"))

        self.assertEqual(code, 0, error)
        self.assertTrue(all(marker.exists() for marker in markers))

    def test_docker_placeholder_reports_unavailable_actual_version(self) -> None:
        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, output, _ = self.run_cli(*self.command(
                manifest.relative_to(self.repo).as_posix(), "--runner", "docker", "--json"
            ))

        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["version"], "unavailable")

    def test_human_test_output_uses_title_case_labels(self) -> None:
        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, output, error = self.run_cli(*self.command(
                manifest.relative_to(self.repo).as_posix(), "--runner", "local"
            ))

        self.assertEqual(code, 0, error)
        self.assertIn("Test run", output)
        self.assertIn("  Status", output)
        self.assertIn("Collection", output)
        self.assertNotIn("STATUS", output)

    def test_manifest_swap_after_anchor_returns_usage_without_executing_external_command(self) -> None:
        path = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        marker = Path(self.temporary.name) / "external-manifest-executed"
        outside = Path(self.temporary.name) / "outside.yaml"
        outside.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "outside",
                "steps": [{"type": "command", "command": f"touch {marker}"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")

        def swap_after_anchor(_root: Path, _manifest: Path) -> None:
            path.unlink()
            path.symlink_to(outside)

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_manifest._after_manifest_opened", side_effect=swap_after_anchor,
        ):
            code, _, error = self.run_cli(*self.command(path.relative_to(self.repo).as_posix(), "--runner", "local"))

        self.assertEqual(code, 2)
        self.assertIn("changed before read", error)
        self.assertFalse(marker.exists())

    def test_fixture_parent_swap_after_selection_blocks_without_executing_external_script(self) -> None:
        path = self.manifest(
            "tests/skills/team/review/test.yaml", kind="skill", target_id="team/review", post_check="true",
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["cases"][0]["steps"] = [{"type": "command", "command": "sh run.sh"}]
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        fixtures = path.parent / "fixtures"
        fixtures.mkdir()
        (fixtures / "run.sh").write_text("true\n", encoding="utf-8")
        marker = Path(self.temporary.name) / "external-fixture-executed"
        outside_review = Path(self.temporary.name) / "outside-review"
        (outside_review / "fixtures").mkdir(parents=True)
        (outside_review / "fixtures/run.sh").write_text(f"touch {marker}\n", encoding="utf-8")

        def swap_before_fixture_open(_source) -> None:
            review = path.parent
            review.rename(review.with_name("review-original"))
            review.symlink_to(outside_review, target_is_directory=True)

        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_runner._before_fixture_source_opened", side_effect=swap_before_fixture_open,
        ):
            code, _, _ = self.run_cli(*self.command(path.relative_to(self.repo).as_posix(), "--runner", "local"))

        self.assertEqual(code, 3)
        self.assertFalse(marker.exists())

    def test_repeated_core_runs_close_the_anchored_descriptors(self) -> None:
        core = self.repo / "tests/core"
        core.mkdir(parents=True)
        (core / "test_safe.py").write_text(
            "import unittest\nclass Safe(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        before = len(list(Path("/proc/self/fd").iterdir()))
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            for _ in range(12):
                code, _, error = self.run_cli(*self.command("tests/core/test_safe.py", "--runner", "local"))
                self.assertEqual(code, 0, error)
        after = len(list(Path("/proc/self/fd").iterdir()))
        self.assertLessEqual(after, before + 1)

    def test_manifest_render_keeps_steps_separate_from_the_post_check(self) -> None:
        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code, output, error = self.run_cli(*self.command(manifest.relative_to(self.repo).as_posix(), "--runner", "local", "--json"))

        self.assertEqual(code, 0, error)
        case = json.loads(output)["collections"][0]["cases"][0]
        self.assertEqual([action["id"] for action in case["actions"]], ["step"])
        self.assertEqual(case["post_check"]["id"], "post")

    def test_non_check_setup_uses_configurator_and_persists_only_ready_replacement(self) -> None:
        from hwskill import test_cli
        from hwskill.test_setup import SetupCheck, SetupReport

        args = Namespace(
            test_target="setup", test_id=None, runner="local", host=None, base=None,
            check=False, json=True, repo_root=str(self.repo), model="replacement", reasoning="high",
        )
        report = SetupReport("READY", (SetupCheck("model-availability", "READY", "available"),))
        writes = []
        writer = lambda value: writes.append(value)
        stdout = StringIO()
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_cli.configure_test_setup", return_value=report
        ) as configured:
            code = test_cli.run_test_command(
                args, self.repo, stdout, StringIO(),
                setup_writer=writer, setup_prompt=lambda _message: "replace",
            )

        self.assertEqual(code, 0)
        configured.assert_called_once()
        self.assertEqual(configured.call_args.kwargs["replacement"].hosts["codex"].model, "replacement")
        self.assertIs(configured.call_args.kwargs["write"], writer)
        self.assertEqual(writes, [])
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["runner"], "local")
        self.assertEqual(data["host"], "codex")
        self.assertEqual(data["model"], "replacement")
        self.assertEqual(data["reasoning"], "high")

    def test_setup_bootstraps_missing_configuration_only_after_ready_inspection(self) -> None:
        from hwskill import test_cli
        from hwskill.test_setup import SetupCheck, SetupReport

        args = Namespace(
            test_target="setup", test_id=None, runner="local", host="codex", base=None,
            check=False, json=True, repo_root=str(self.repo), model="bootstrap-model", reasoning="high",
        )
        ready = SetupReport("READY", (SetupCheck("model-availability", "READY", "available"),))
        stdout = StringIO()
        with patch(
            "hwskill.test_cli.load_test_configuration",
            side_effect=TestConfigurationError("cannot read test configuration: No such file or directory"),
        ), patch("hwskill.test_cli.configure_test_setup", return_value=ready) as configured:
            code = test_cli.run_test_command(args, self.repo, stdout, StringIO())

        self.assertEqual(code, 0)
        initial = configured.call_args.args[0]
        self.assertEqual(initial.runner, "local")
        self.assertEqual(initial.default_host, "codex")
        self.assertEqual(initial.hosts["codex"], HostModel("bootstrap-model", "high"))
        self.assertEqual(configured.call_args.kwargs["replacement"], initial)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["model"], "bootstrap-model")
        self.assertEqual(data["reasoning"], "high")

    def test_interactive_setup_bootstrap_prompts_each_field_once_and_uses_host(self) -> None:
        from hwskill import test_cli
        from hwskill.test_setup import SetupCheck, SetupReport

        args = Namespace(
            test_target="setup", test_id=None, runner=None, host=None, base=None,
            check=False, json=True, repo_root=str(self.repo), model=None, reasoning=None,
        )
        responses = iter(("local", "claude-code", "bootstrap-model", "high"))
        prompts = []
        ready = SetupReport("READY", (SetupCheck("model-availability", "READY", "available"),))
        stdout = StringIO()
        with patch(
            "hwskill.test_cli.load_test_configuration",
            side_effect=TestConfigurationError("cannot read test configuration: No such file or directory"),
        ), patch("hwskill.test_cli.configure_test_setup", return_value=ready) as configured:
            code = test_cli.run_test_command(
                args, self.repo, stdout, StringIO(),
                setup_prompt=lambda message: prompts.append(message) or next(responses),
            )

        self.assertEqual(code, 0)
        self.assertEqual(prompts, [
            "Test runner [docker]: ", "Test host [codex]: ", "Test model: ", "Test reasoning: ",
        ])
        initial = configured.call_args.args[0]
        self.assertEqual(initial.runner, "local")
        self.assertEqual(initial.default_host, "claude-code")
        self.assertEqual(initial.hosts["claude-code"], HostModel("bootstrap-model", "high"))
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["host"], "claude-code")

    def test_setup_check_with_missing_configuration_is_usage_without_prompting(self) -> None:
        from hwskill import test_cli

        args = Namespace(
            test_target="setup", test_id=None, runner=None, host=None, base=None,
            check=True, json=False, repo_root=str(self.repo), model=None, reasoning=None,
        )
        prompt = Mock()
        error = StringIO()
        with patch(
            "hwskill.test_cli.load_test_configuration",
            side_effect=TestConfigurationError("cannot read test configuration: No such file or directory"),
        ):
            code = test_cli.run_test_command(args, self.repo, StringIO(), error, setup_prompt=prompt)

        self.assertEqual(code, 2)
        self.assertIn("cannot read test configuration", error.getvalue())
        prompt.assert_not_called()

    def test_setup_check_rejects_explicit_replacement_options(self) -> None:
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_cli.configure_test_setup"
        ) as configured:
            code, _, error = self.run_cli(*self.command(
                "setup", "--check", "--model", "replacement", "--reasoning", "high"
            ))

        self.assertEqual(code, 2)
        self.assertIn("--check cannot be combined", error)
        configured.assert_not_called()

    def test_mismatched_credential_material_is_a_usage_error_without_traceback(self) -> None:
        from hwskill import test_cli

        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        args = Namespace(
            test_target=manifest.relative_to(self.repo).as_posix(), test_id=None, runner="local", host=None,
            base=None, check=False, json=False, model=None, reasoning=None,
        )
        error = StringIO()
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code = test_cli.run_test_command(
                args, self.repo, StringIO(), error,
                credential_material=test_cli.CredentialMaterial("claude-code"),
            )

        self.assertEqual(code, 2)
        self.assertIn("credential material host", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_local_execution_reports_injected_actual_host_version(self) -> None:
        from hwskill import test_cli

        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        args = Namespace(
            test_target=manifest.relative_to(self.repo).as_posix(), test_id=None, runner="local", host=None,
            base=None, check=False, json=True, model=None, reasoning=None,
        )

        class Boundary:
            def run(self, _collections, environment, artifact_root):
                return test_cli.TestRunResult(
                    "PASS", artifact_root, (), "incorrect-runner", "incorrect-host",
                    "incorrect-model", "not-actual",
                )

        stdout = StringIO()
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code = test_cli.run_test_command(
                args, self.repo, stdout, StringIO(), execution_boundary=Boundary(),
                host_version_probe=lambda _host: "0.147.0",
            )

        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["runner"], "local")
        self.assertEqual(data["host"], "codex")
        self.assertEqual(data["model"], "test-model")
        self.assertEqual(data["version"], "0.147.0")

    def test_local_execution_blocks_when_actual_host_version_cannot_be_probed(self) -> None:
        from hwskill import test_cli

        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        args = Namespace(
            test_target=manifest.relative_to(self.repo).as_posix(), test_id=None, runner="local", host=None,
            base=None, check=False, json=True, model=None, reasoning=None,
        )

        class Boundary:
            def run(self, *_args):
                raise AssertionError("execution must not start without an actual compatible host version")

        stdout = StringIO()
        with patch("hwskill.test_cli.load_test_configuration", return_value=self.config):
            code = test_cli.run_test_command(
                args, self.repo, stdout, StringIO(), execution_boundary=Boundary(),
                host_version_probe=lambda _host: "credential-sentinel",
            )

        self.assertEqual(code, 3)
        data = json.loads(stdout.getvalue())
        self.assertIn("local host CLI version is unavailable or unsupported", data["blocked_reason"])
        self.assertNotIn("credential-sentinel", json.dumps(data))

    def test_credential_store_path_uses_runtime_or_injected_home(self) -> None:
        from hwskill.test_cli import _environment_credential_material

        home = Path(self.temporary.name) / "alternate-home"
        store = home / ".codex/auth.json"
        store.parent.mkdir(parents=True)
        store.write_text("{}", encoding="utf-8")

        material = _environment_credential_material("codex", {}, home=home)

        self.assertEqual(material.source, "host-store")

    def test_filtered_minimax_credential_file_is_accepted_only_for_compatible_docker_hosts(self) -> None:
        from hwskill.test_cli import _environment_credential_material

        source = Path(self.temporary.name) / "minimax.json"
        source.write_text(json.dumps({
            "minimax-cn-coding-plan": {"type": "api", "key": "minimax-sentinel"},
        }), encoding="utf-8")
        source.chmod(0o600)
        environment = {"HWSKILL_MINIMAX_AUTH_FILE": str(source)}

        home = Path(self.temporary.name) / "empty-home"
        claude = _environment_credential_material("claude-code", environment, home=home)
        opencode = _environment_credential_material("opencode", environment, home=home)

        self.assertEqual(claude.source, "minimax-store")
        self.assertEqual(claude.credential_files[0].source, source)
        self.assertEqual(claude.credential_files[0].destination, "/credentials/minimax-auth.json")
        self.assertEqual(opencode.credential_files[0].destination, "/credentials/opencode/auth.json")
        self.assertFalse(_environment_credential_material("codex", environment, home=home).credential_files)

        alias = Path(self.temporary.name) / "minimax-alias.json"
        alias.symlink_to(source)
        with self.assertRaisesRegex(Exception, "non-symlink"):
            _environment_credential_material("claude-code", {"HWSKILL_MINIMAX_AUTH_FILE": str(alias)}, home=home)

    def test_filtered_minimax_credential_rejects_unfiltered_extra_provider_data(self) -> None:
        """A wrapper adapter must not mount unrelated credentials into the Agent container."""
        from hwskill.test_cli import _environment_credential_material

        source = Path(self.temporary.name) / "not-filtered.json"
        source.write_text(json.dumps({
            "minimax-cn-coding-plan": {"type": "api", "key": "minimax-sentinel"},
            "unrelated-provider": {"type": "api", "key": "must-not-mount"},
        }), encoding="utf-8")
        source.chmod(0o600)

        with self.assertRaisesRegex(Exception, "malformed"):
            _environment_credential_material(
                "claude-code", {"HWSKILL_MINIMAX_AUTH_FILE": str(source)}, home=self.repo,
            )

    def test_nondefault_minimax_path_is_wired_into_the_docker_runner_without_secret_output(self) -> None:
        """The legacy wrapper marker becomes an approved fixed-destination Docker credential mount."""
        from hwskill import test_cli

        manifest = self.manifest("tests/skills/team/review/test.yaml", kind="skill", target_id="team/review")
        source = Path(self.temporary.name) / "non-default-minimax.json"
        sentinel = "minimax-sentinel"
        source.write_text(json.dumps({
            "minimax-cn-coding-plan": {"type": "api", "key": sentinel},
        }), encoding="utf-8")
        source.chmod(0o600)
        docker_config = TestConfiguration(
            runner="docker", default_host="claude-code",
            hosts={"claude-code": HostModel("test-model", "minimal")},
        )
        captured: list[object] = []

        class Runner:
            def __init__(self, _root, _configuration, *, credential_files=()):
                captured.extend(credential_files)

            def run(self, _collections, environment, artifact_root):
                return test_cli.TestRunResult(
                    "PASS", artifact_root, (), environment.runner, environment.host,
                    environment.model, "2.1.141",
                )

        args = Namespace(
            test_target=manifest.relative_to(self.repo).as_posix(), test_id=None,
            runner="docker", host="claude-code", base=None, check=False, json=True,
            model=None, reasoning=None,
        )
        stdout = StringIO()
        with patch.dict(os.environ, {"HWSKILL_MINIMAX_AUTH_FILE": str(source)}, clear=True), patch(
            "hwskill.test_cli.load_test_configuration", return_value=docker_config,
        ), patch("hwskill.test_cli.DockerTestRunner", Runner):
            code = test_cli.run_test_command(args, self.repo, stdout, StringIO())

        self.assertEqual(code, 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].source, source)
        self.assertEqual(captured[0].destination, "/credentials/minimax-auth.json")
        self.assertNotIn(sentinel, stdout.getvalue())

    def test_local_agent_uses_only_selected_environment_credentials_and_redacts_all_artifacts(self) -> None:
        from hwskill import test_cli

        sentinel = "credential-sentinel"
        path = self.repo / "tests/skills/team/review/test.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "agent-case",
                "steps": [{"type": "agent", "id": "agent", "prompt": "reply"}],
                "post_check": {"type": "command", "id": "post", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")

        class Process:
            def __init__(self) -> None:
                self.stdout = StringIO('{"type":"result","result":"' + sentinel + '"}\n')
                self.stderr = StringIO(sentinel + "\n")
                self.returncode = 0
                self.pid = os.getpid()

            def wait(self, timeout=None):
                return self.returncode

            def communicate(self, timeout=None):
                return "", ""

        captured = []

        def popen(_command, **kwargs):
            captured.append(kwargs["env"])
            return Process()

        completed = type("Completed", (), {"returncode": 0})()
        from hwskill.test_agent import AgentExecutor
        executor = AgentExecutor(credential_available=lambda _host: True, setup_runner=lambda *_args, **_kwargs: completed)
        args = Namespace(
            test_target=path.relative_to(self.repo).as_posix(), test_id=None, runner="local", host=None,
            base=None, check=False, json=True,
        )
        stdout = StringIO()
        with patch.dict(os.environ, {"CODEX_API_KEY": sentinel, "UNRELATED_SECRET": "must-not-pass"}, clear=True), patch(
            "hwskill.test_cli.load_test_configuration", return_value=self.config
        ), patch("hwskill.test_cli._local_agent_executor", return_value=executor), patch(
            "hwskill.test_agent.subprocess.Popen", side_effect=popen
        ):
            code = test_cli.run_test_command(args, self.repo, stdout, StringIO())

        self.assertEqual(code, 0, stdout.getvalue())
        credentialed = [environment for environment in captured if "CODEX_API_KEY" in environment]
        self.assertEqual(credentialed[0]["CODEX_API_KEY"], sentinel)
        self.assertTrue(all("UNRELATED_SECRET" not in environment for environment in captured))
        case_dir = Path(json.loads(stdout.getvalue())["collections"][0]["cases"][0]["artifact_dir"])
        for artifact in case_dir.rglob("*"):
            if not artifact.is_file():
                continue
            self.assertNotIn(sentinel, artifact.read_text(encoding="utf-8"))

    def test_local_agent_without_environment_material_blocks_before_model_spawn(self) -> None:
        path = self.repo / "tests/skills/team/review/test.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({
            "schema_version": 1, "target": {"kind": "skill", "id": "team/review"},
            "cases": [{"id": "agent-case", "steps": [{"type": "agent", "prompt": "reply"}],
                       "post_check": {"type": "command", "command": "true"}}],
        }, sort_keys=False), encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch("hwskill.test_cli.load_test_configuration", return_value=self.config), patch(
            "hwskill.test_agent.subprocess.Popen"
        ) as popen:
            code, _, _ = self.run_cli(*self.command(path.relative_to(self.repo).as_posix(), "--runner", "local"))

        self.assertEqual(code, 3)
        self.assertFalse(any(call.args[0][0] == "codex" for call in popen.call_args_list))


if __name__ == "__main__":
    unittest.main()
