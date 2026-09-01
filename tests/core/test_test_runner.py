from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from hwskill.test_manifest import CommandAction, TestCase, TestCollection, TestTarget


class TestLocalCaseRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.fixtures = self.repo / "tests/skills/team/review/fixtures"
        self.fixtures.mkdir(parents=True)
        (self.fixtures / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.artifacts = self.root / "artifacts"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, **overrides: object):
        from hwskill.test_runner import TestEnvironment

        values: dict[str, object] = {
            "repo_root": self.repo,
            "runner": "local",
            "host": "codex",
            "model": "test-model",
            "reasoning": "minimal",
        }
        values.update(overrides)
        return TestEnvironment(**values)

    def case(self, *, prepare=None, steps=None, post_check=None, workdir=None) -> TestCase:
        return TestCase(
            case_id="case-one",
            description=None,
            workdir=workdir,
            prepare=prepare,
            steps=tuple(steps or [CommandAction("write", "printf written > output.txt")]),
            post_check=post_check or CommandAction("post-check", "test -f output.txt"),
        )

    def collection(self, *cases: TestCase) -> TestCollection:
        return TestCollection(
            manifest_path=self.repo / "tests/skills/team/review/test.yaml",
            target=TestTarget("skill", "team/review"),
            cases=tuple(cases),
            fixtures_dir=self.fixtures,
        )

    def test_prepare_steps_and_post_check_share_workspace_and_context(self) -> None:
        from hwskill.test_runner import run_case

        result = run_case(self.case(
            prepare=CommandAction("prepare", "printf prepared > prepared.txt"),
            steps=[CommandAction("write", "test -f seed.txt && test -f prepared.txt && printf written > output.txt")],
        ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")
        self.assertEqual([action.action_id for action in result.actions], ["prepare", "write", "post-check"])
        context = json.loads((result.artifact_dir / "context.json").read_text(encoding="utf-8"))
        self.assertEqual(list(context["actions"]), ["prepare", "write", "post-check"])
        self.assertNotIn("stdout", json.dumps(context))
        self.assertFalse(Path(context["workspace"]).exists())
        self.assertTrue((result.artifact_dir / "workspace.diff").is_file())

    def test_prepare_failure_blocks_steps_and_post_check(self) -> None:
        from hwskill.test_runner import run_case

        result = run_case(self.case(
            prepare=CommandAction("prepare", "exit 9"),
            steps=[CommandAction("step", "touch should-not-exist")],
            post_check=CommandAction("post-check", "touch post-check-ran"),
        ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual([item.action_id for item in result.actions], ["prepare"])
        self.assertFalse((result.artifact_dir / "workspace" / "should-not-exist").exists())
        self.assertFalse((result.artifact_dir / "workspace" / "post-check-ran").exists())

    def test_failed_step_still_reaches_post_check_and_uses_its_exact_verdict(self) -> None:
        from hwskill.test_runner import run_case

        failing = run_case(self.case(
            steps=[CommandAction("step", "exit 4")],
            post_check=CommandAction("post-check", "exit 1"),
        ), self.environment(), self.artifacts / "fail")
        blocked = run_case(self.case(
            steps=[CommandAction("step", "exit 4")],
            post_check=CommandAction("post-check", "exit 2"),
        ), self.environment(), self.artifacts / "blocked")

        self.assertEqual(failing.status, "FAIL")
        self.assertEqual([item.status for item in failing.actions], ["failed", "failed"])
        self.assertEqual(blocked.status, "BLOCKED")
        self.assertEqual([item.status for item in blocked.actions], ["failed", "failed"])

    def test_post_check_receives_absolute_context_artifact_and_workspace_paths(self) -> None:
        from hwskill.test_runner import run_case

        command = (
            'python3 -c "import os,pathlib; '
            'assert all(pathlib.Path(os.environ[name]).is_absolute() for name in '
            "['HWSKILL_TEST_CONTEXT','HWSKILL_TEST_ARTIFACTS','HWSKILL_TEST_WORKSPACE','HWSKILL_TEST_REPO_ROOT','HWSKILL_TEST_PYTHON','HWSKILL_TEST_PYTHONPATH'])\""
        )
        result = run_case(self.case(post_check=CommandAction("post-check", command)), self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")

    def test_every_command_action_receives_runner_owned_repository_and_python_paths(self) -> None:
        from hwskill.test_runner import run_case

        command = (
            'python3 -c "import os,pathlib; '
            "assert all(pathlib.Path(os.environ[name]).is_absolute() for name in "
            "['HWSKILL_TEST_REPO_ROOT','HWSKILL_TEST_PYTHON','HWSKILL_TEST_PYTHONPATH'])\""
        )
        result = run_case(
            self.case(steps=[CommandAction("step", command)], post_check=CommandAction("post-check", "true")),
            self.environment(), self.artifacts,
        )

        self.assertEqual(result.status, "PASS")

    def test_action_workdir_symlink_escape_is_blocked_before_command_runs(self) -> None:
        from hwskill.test_runner import run_case

        outside = self.root / "outside"
        outside.mkdir()
        result = run_case(self.case(
            prepare=CommandAction("prepare", f"ln -s {outside} escape"),
            steps=[CommandAction("step", "touch escaped", workdir="escape")],
            post_check=CommandAction("post-check", "exit 0"),
        ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.actions[1].status, "blocked")
        self.assertFalse((outside / "escaped").exists())

    def test_workdir_swap_after_open_never_launches_the_command_outside_workspace(self) -> None:
        from hwskill.test_runner import run_case

        (self.fixtures / "project").mkdir()
        outside = self.root / "outside"
        outside.mkdir()

        def swap_after_open(workspace: Path, configured: str | None) -> None:
            if configured != "project":
                return
            target = workspace / "project"
            target.rename(workspace / "project-original")
            target.symlink_to(outside, target_is_directory=True)

        with patch("hwskill.test_runner._after_workdir_opened", side_effect=swap_after_open):
            result = run_case(self.case(
                steps=[CommandAction("step", "touch anchored.txt", workdir="project")],
                post_check=CommandAction("post-check", "exit 0"),
            ), self.environment(), self.artifacts)

        self.assertIn(result.status, {"PASS", "BLOCKED"})
        self.assertNotEqual(result.actions[0].status, "failed")
        self.assertFalse((outside / "anchored.txt").exists())

    def test_fixture_root_swap_after_open_copies_the_anchored_fixture_tree(self) -> None:
        from hwskill.test_runner import run_case

        (self.fixtures / "root.txt").write_text("trusted", encoding="utf-8")
        outside = self.root / "outside-fixtures"
        outside.mkdir()
        (outside / "root.txt").write_text("external", encoding="utf-8")

        def swap_after_open(source: Path, relative: Path) -> None:
            if relative == Path("."):
                source.rename(source.with_name("fixtures-original"))
                source.symlink_to(outside, target_is_directory=True)

        with patch("hwskill.test_runner._after_fixture_directory_opened", side_effect=swap_after_open):
            result = run_case(self.case(
                steps=[CommandAction("step", 'test "$(cat root.txt)" = trusted && printf written > output.txt')],
            ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")

    def test_nested_fixture_swap_after_open_copies_the_anchored_directory(self) -> None:
        from hwskill.test_runner import run_case

        nested = self.fixtures / "nested"
        nested.mkdir()
        (nested / "value.txt").write_text("trusted", encoding="utf-8")
        outside = self.root / "outside-nested"
        outside.mkdir()
        (outside / "value.txt").write_text("external", encoding="utf-8")

        def swap_after_open(source: Path, relative: Path) -> None:
            if relative == Path("nested"):
                target = source / "nested"
                target.rename(source / "nested-original")
                target.symlink_to(outside, target_is_directory=True)

        with patch("hwskill.test_runner._after_fixture_directory_opened", side_effect=swap_after_open):
            result = run_case(self.case(
                steps=[CommandAction("step", 'test "$(cat nested/value.txt)" = trusted && printf written > output.txt')],
            ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")

    def test_loaded_collection_snapshots_nested_fixtures_and_preserves_executable_mode(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": 'test -x run.sh && test "$(cat nested/value.txt)" = trusted && ./run.sh'}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        nested = self.fixtures / "nested"
        nested.mkdir()
        (nested / "value.txt").write_text("trusted\n", encoding="utf-8")
        script = self.fixtures / "run.sh"
        script.write_text("true\n", encoding="utf-8")
        script.chmod(0o755)

        collection = load_test_collection(manifest, self.repo)
        self.assertIsNotNone(collection.fixture_source)
        result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")

    def test_loaded_collection_rejects_leaf_fixture_symlink_replacement_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "sh run.sh"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        script = self.fixtures / "run.sh"
        script.write_text("true\n", encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)
        self.assertIsNotNone(collection.fixture_source)
        marker = self.root / "external-leaf-executed"
        outside = self.root / "outside.sh"
        outside.write_text(f"touch {marker}\n", encoding="utf-8")
        script.unlink()
        script.symlink_to(outside)

        result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())
        self.assertFalse(marker.exists())

    def test_loaded_collection_rejects_in_place_fixture_content_rewrite_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "sh run.sh"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        script = self.fixtures / "run.sh"
        script.write_text("true\n", encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)
        marker = self.root / "rewritten-fixture-executed"
        script.write_text(f"touch {marker}\n", encoding="utf-8")

        result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())
        self.assertFalse(marker.exists())

    def test_loaded_collection_rejects_fixture_mode_change_during_copy(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{"id": "case-one", "steps": [{"type": "command", "command": "sh run.sh"}],
                       "post_check": {"type": "command", "command": "true"}}],
        }, sort_keys=False), encoding="utf-8")
        script = self.fixtures / "run.sh"
        script.write_text("true\n", encoding="utf-8")
        script.chmod(0o755)
        collection = load_test_collection(manifest, self.repo)

        def chmod_after_open(relative: tuple[str, ...]) -> None:
            if relative == ("run.sh",):
                script.chmod(0o644)

        with patch("hwskill.test_runner._after_snapshot_fixture_file_opened", side_effect=chmod_after_open):
            result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())

    def test_loaded_collection_rejects_root_fixture_added_during_copy_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "touch action-ran"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        (self.fixtures / "safe.txt").write_text("trusted\n", encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)

        def add_after_open(relative: tuple[str, ...]) -> None:
            if relative == ("safe.txt",):
                (self.fixtures / "added.txt").write_text("late\n", encoding="utf-8")

        with patch("hwskill.test_runner._after_snapshot_fixture_file_opened", side_effect=add_after_open):
            result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())

    def test_loaded_collection_rejects_nested_fixture_added_during_copy_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "touch action-ran"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        nested = self.fixtures / "nested"
        nested.mkdir()
        (nested / "safe.txt").write_text("trusted\n", encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)

        def add_after_open(relative: tuple[str, ...]) -> None:
            if relative == ("nested", "safe.txt"):
                (nested / "added.txt").write_text("late\n", encoding="utf-8")

        with patch("hwskill.test_runner._after_snapshot_fixture_file_opened", side_effect=add_after_open):
            result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())

    def test_loaded_collection_rejects_fixture_deleted_during_copy_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "touch action-ran"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        fixture = self.fixtures / "safe.txt"
        fixture.write_text("trusted\n", encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)

        def remove_after_open(relative: tuple[str, ...]) -> None:
            if relative == ("safe.txt",):
                fixture.unlink()

        with patch("hwskill.test_runner._after_snapshot_fixture_file_opened", side_effect=remove_after_open):
            result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())

    def test_loaded_collection_rejects_parent_mode_change_during_copy_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "touch action-ran"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        (self.fixtures / "safe.txt").write_text("trusted\n", encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)
        original_mode = self.fixtures.stat().st_mode & 0o777

        def chmod_after_open(relative: tuple[str, ...]) -> None:
            if relative == ("safe.txt",):
                self.fixtures.chmod(0o700)

        with patch("hwskill.test_runner._after_snapshot_fixture_file_opened", side_effect=chmod_after_open):
            result = run_collection(collection, self.environment(), self.artifacts)

        self.fixtures.chmod(original_mode)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())

    def test_loaded_collection_rejects_fixtures_created_after_absent_snapshot_before_actions(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "sh run.sh"}],
                "post_check": {"type": "command", "command": "true"},
            }],
        }, sort_keys=False), encoding="utf-8")
        shutil.rmtree(self.fixtures)
        collection = load_test_collection(manifest, self.repo)
        self.assertIsNotNone(collection.fixture_source)
        marker = self.root / "late-fixture-executed"
        self.fixtures.mkdir()
        (self.fixtures / "run.sh").write_text(f"touch {marker}\n", encoding="utf-8")

        result = run_collection(collection, self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].actions, ())
        self.assertFalse(marker.exists())

    def test_loaded_collection_with_absent_fixtures_runs_in_an_empty_workspace(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{
                "id": "case-one",
                "steps": [{"type": "command", "command": "test ! -e run.sh && touch output.txt"}],
                "post_check": {"type": "command", "command": "test -f output.txt"},
            }],
        }, sort_keys=False), encoding="utf-8")
        shutil.rmtree(self.fixtures)
        collection = load_test_collection(manifest, self.repo)

        result = run_collection(collection, self.environment(), self.artifacts)

        self.assertIsNotNone(collection.fixture_source)
        self.assertEqual(result.status, "PASS")

    def test_loaded_collection_fixture_snapshot_closes_descriptors_across_runs(self) -> None:
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import run_collection

        manifest = self.repo / "tests/skills/team/review/test.yaml"
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [{"id": "case-one", "steps": [{"type": "command", "command": "true"}],
                       "post_check": {"type": "command", "command": "true"}}],
        }, sort_keys=False), encoding="utf-8")
        collection = load_test_collection(manifest, self.repo)
        self.assertIsNotNone(collection.fixture_source)
        before = len(list(Path("/proc/self/fd").iterdir()))
        for index in range(8):
            result = run_collection(collection, self.environment(), self.artifacts / str(index))
            self.assertEqual(result.status, "PASS")
        after = len(list(Path("/proc/self/fd").iterdir()))
        self.assertLessEqual(after, before + 1)

    def test_case_workdir_applies_when_action_has_no_override(self) -> None:
        from hwskill.test_runner import run_case

        (self.fixtures / "case-dir").mkdir()
        (self.fixtures / "action-dir").mkdir()
        result = run_case(self.case(
            workdir="case-dir",
            steps=[
                CommandAction("case", "printf case > case.txt"),
                CommandAction("action", "printf action > action.txt", workdir="action-dir"),
            ],
            post_check=CommandAction("post-check", "test -f case.txt && test ! -f action.txt"),
        ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")
        diff = (result.artifact_dir / "workspace.diff").read_text(encoding="utf-8")
        self.assertEqual(diff, "A action-dir/action.txt\nA case-dir/case.txt\n")

    def test_timeout_kills_the_action_process_group(self) -> None:
        from hwskill.test_runner import run_case

        result = run_case(self.case(
            steps=[CommandAction("step", "sleep 5")],
            post_check=CommandAction("post-check", "exit 0"),
        ), self.environment(timeout_seconds=0.1), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.actions[0].status, "blocked")
        self.assertEqual(result.actions[1].status, "completed")
        stderr = (result.actions[0].artifact_dir / "stderr.log").read_text(encoding="utf-8")
        self.assertIn("timed out", stderr)

    def test_minimal_environment_forwards_declared_variables_and_redacts_persisted_secrets(self) -> None:
        from hwskill.test_runner import run_case

        secret = "super-secret-value"
        result = run_case(self.case(
            steps=[CommandAction("step", 'printf "%s:%s" "$PATH" "$DECLARED"; printf "' + secret + '" >&2')],
            post_check=CommandAction("post-check", "exit 0"),
        ), self.environment(
            environment_variables=(("DECLARED", "available"),),
            secret_values=(secret,),
        ), self.artifacts)

        persisted = "\n".join(
            path.read_text(encoding="utf-8")
            for path in result.artifact_dir.rglob("*") if path.is_file()
        )
        self.assertEqual(result.status, "PASS")
        self.assertIn(":available", persisted)
        self.assertNotIn(secret, persisted)
        self.assertIn("[REDACTED]", persisted)
        self.assertNotIn(os.environ.get("HOME", ""), persisted)

    def test_invalid_command_environment_returns_blocked_action_evidence_and_cleans_workspace(self) -> None:
        from hwskill.test_runner import run_case

        result = run_case(self.case(), self.environment(
            environment_variables=(("INVALID=KEY", "value"),),
        ), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual([action.status for action in result.actions], ["blocked", "blocked"])
        step_result = json.loads((result.actions[0].artifact_dir / "result.json").read_text(encoding="utf-8"))
        self.assertIn("invalid declared test environment variable", step_result["reason"])
        context = json.loads((result.artifact_dir / "context.json").read_text(encoding="utf-8"))
        self.assertFalse(Path(context["workspace"]).exists())

    def test_collection_keeps_invalid_environment_action_evidence_and_cleans_workspace(self) -> None:
        from hwskill.test_runner import run_collection

        result = run_collection(self.collection(self.case()), self.environment(
            environment_variables=(("INVALID=KEY", "value"),),
        ), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual([action.status for action in result.cases[0].actions], ["blocked", "blocked"])
        context = json.loads((result.cases[0].artifact_dir / "context.json").read_text(encoding="utf-8"))
        self.assertFalse(Path(context["workspace"]).exists())

    def test_agent_without_a_required_executor_blocks_the_case(self) -> None:
        from hwskill.test_manifest import AgentAction
        from hwskill.test_runner import run_case

        result = run_case(self.case(
            steps=[AgentAction("agent", "do the work")],
            post_check=CommandAction("post-check", "exit 0"),
        ), self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.actions[0].status, "blocked")

    def test_run_collection_is_case_isolated_and_converts_case_exceptions_to_blocked(self) -> None:
        from hwskill.test_runner import run_collection

        first = self.case(
            steps=[CommandAction("write", "printf one > unique.txt")],
            post_check=CommandAction("post-check", "test -f unique.txt"),
        )
        second = TestCase(
            case_id="case-two",
            description=None,
            workdir=None,
            prepare=None,
            steps=(CommandAction("write", "test ! -e unique.txt"),),
            post_check=CommandAction("post-check", "exit 0"),
        )
        result = run_collection(self.collection(first, second), self.environment(), self.artifacts)

        self.assertEqual(result.status, "PASS")
        self.assertEqual([case.case_id for case in result.cases], ["case-one", "case-two"])
        self.assertEqual([case.status for case in result.cases], ["PASS", "PASS"])

    def test_run_collection_converts_runner_exception_to_a_blocked_case(self) -> None:
        from hwskill.test_runner import run_collection

        self.artifacts.mkdir()
        (self.artifacts / "case-one").write_text("not an artifact directory", encoding="utf-8")
        result = run_collection(self.collection(self.case()), self.environment(), self.artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.cases[0].status, "BLOCKED")
        self.assertEqual(result.cases[0].artifact_dir.name, "case-one-blocked")


if __name__ == "__main__":
    unittest.main()
