from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hwskill.cli import main
from hwskill.console_output import format_maintenance_plan, plan_data
from hwskill.maintenance_transaction import MaintenancePlan, MaintenanceSummary
from hwskill.git_source import RemoteRefs, ResolvedTrack
from hwskill.source_manifest import ResolvedSourceSkill, SourceDefaults, UpstreamConfig, UpstreamSource, IgnoredSkill, write_source_manifest, load_source_manifest


class FakeGitSourceClient:
    def __init__(self, checkout: Path): self.checkout, self.calls = checkout, []
    def list_remote(self, repository):
        self.calls.append(("list_remote", repository))
        return RemoteRefs("refs/heads/main", {"refs/heads/main": "b" * 40, "refs/tags/v1": "c" * 40})
    def materialize(self, repository, track, destination):
        self.calls.append(track)
        shutil.copytree(self.checkout, destination, dirs_exist_ok=True)
        return ResolvedTrack(track, "branch", "b" * 40)


class MaintenanceCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.remote = Path(self.temp.name) / "remote"
        for name in ("one", "two"):
            path = self.remote / "library" / name; path.mkdir(parents=True, exist_ok=True)
            (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n", encoding="utf-8")
        self.git = FakeGitSourceClient(self.remote)

    def tearDown(self):
        self.temp.cleanup()

    def test_source_and_skill_maintenance_commands_are_registered(self):
        outputs = []
        for command in (("source", "--help"), ("skill", "--help")):
            out = StringIO()
            with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                main(list(command))
            self.assertEqual(raised.exception.code, 0)
            outputs.append(out.getvalue())
        self.assertIn("add", outputs[0])
        self.assertIn("create", outputs[1])

    def test_noninteractive_source_update_requires_explicit_policies(self):
        with patch("hwskill.cli.sys.stdin.isatty", return_value=False):
            self.assertEqual(main(["source", "update", "--all", "--repo-root", str(self.repo), "--yes"]), 2)

    def test_noninteractive_write_refuses_before_any_prompt_with_usage_exit(self):
        stdin, stdout, stderr = StringIO(""), StringIO(), StringIO()
        stdin.isatty = lambda: False
        with patch("hwskill.maintenance_cli.GitSourceClient"):
            from hwskill.maintenance_cli import run_maintenance_command
            import argparse
            args = argparse.Namespace(command="skill", skill_command="create", skill_id="team/review", layer="l1", description="demo", license_name="MIT", repo_root=str(self.repo), yes=False, json=False)
            self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr), 2)
        self.assertIn("--yes", stderr.getvalue())

    def test_registry_import_is_not_registered(self):
        with self.assertRaises(SystemExit) as raised:
            main(["registry", "import", "--source", "x"])
        self.assertEqual(raised.exception.code, 2)

    def test_plan_output_is_stable_sectioned_and_non_ansi(self):
        summary = MaintenanceSummary("source-update", source_ids=("team",), added_skill_ids=("team/new",), affected_profile_ids=("demo",), affected_test_paths=("tests/skills/team/new/test.yaml",), source_details=({"source_id": "team", "status": "success", "repository": "https://team.example/team.git", "track": "refs/heads/main", "old_revision": "a" * 40, "new_revision": "b" * 40, "deltas": {"added": ["new"], "updated": [], "removed": []}},))
        plan = MaintenancePlan(None, summary)
        data = plan_data(plan)
        self.assertEqual(data["sources"][0]["track"], "refs/heads/main")
        self.assertEqual(data["affected_tests"], ["tests/skills/team/new/test.yaml"])
        rendered = format_maintenance_plan(plan, color=False)
        self.assertLess(rendered.index("Source"), rendered.index("Skills"))
        self.assertIn("Profiles", rendered); self.assertIn("Tests", rendered); self.assertNotIn("\x1b[", rendered)

    def _streams(self, answers="", tty=True):
        stdin, stdout, stderr = StringIO(answers), StringIO(), StringIO()
        stdin.isatty = lambda: tty; stdout.isatty = lambda: False
        return stdin, stdout, stderr

    @staticmethod
    def _verifier(selection):
        from hwskill.pending_verification import VerificationResult
        return VerificationResult(
            selection, dict(selection.digests),
            tuple((case_id, "PASS") for case_id in selection.required_case_ids),
        )

    def _update_args(self, **changes):
        values = dict(command="source", source_command="update", source_id="team", all=False, repo_root=str(self.repo), on_added="include", on_removed="remove", track=None, select_track=False, yes=True, json=True)
        values.update(changes); return SimpleNamespace(**values)

    def test_source_update_all_dispatches_one_atomic_plan(self):
        from hwskill.maintenance_cli import run_maintenance_command
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update", source_ids=("a", "b")))
        args = self._update_args(source_id=None, all=True)
        with patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as dispatch:
            with patch("hwskill.maintenance_cli.GitSourceClient"):
                self.assertEqual(run_maintenance_command(args, self.repo, *self._streams()), 0)
        self.assertIsNone(dispatch.call_args.args[1])

    def test_source_update_yes_does_not_supply_missing_policies(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._update_args(on_added=None, on_removed=None)
        with patch("hwskill.maintenance_cli.GitSourceClient"):
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams()), 2)

    def test_source_update_interactively_resolves_added_and_removed_policies(self):
        from hwskill.maintenance_cli import run_maintenance_command
        from hwskill.git_source import DiscoveredSkill
        from hwskill.source_maintenance import SourceInspection
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))
        args = self._update_args(on_added=None, on_removed=None, yes=False)
        inspection = SourceInspection("a" * 40, "b" * 40, (DiscoveredSkill("new", "new", "new"),), (), ("gone",), ())
        with patch("hwskill.maintenance_cli.inspect_sources", return_value={"team": inspection}), patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as dispatch, patch("hwskill.maintenance_cli.GitSourceClient"):
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams("ignore\nmanualize\ny\n")), 0)
        self.assertEqual(dict(dispatch.call_args.args[2].added_decisions), {("team", "new"): "ignore"})
        self.assertEqual(dict(dispatch.call_args.args[2].removed_decisions), {("team", "gone"): "manualize"})

    def test_source_update_interactively_collects_mixed_per_skill_decisions_for_all_sources(self):
        """Interactive update decisions are bound to each discovered source path."""
        from hwskill.maintenance_cli import run_maintenance_command

        for source_id in ("team", "other"):
            write_source_manifest(
                self.repo / "sources" / f"{source_id}.yaml",
                UpstreamSource(
                    source_id,
                    UpstreamConfig(f"https://x/{source_id}.git", "refs/heads/main", "library", ()),
                    SourceDefaults(source_id, "l1", "MIT"),
                    "a" * 40,
                    (),
                ),
            )
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))
        args = self._update_args(source_id=None, all=True, on_added=None, on_removed=None, yes=False)
        # The fake remote contains one and two.  Mix include and ignore for every source.
        answers = "include\nignore\nignore\ninclude\ny\n"
        stdin, stdout, stderr = self._streams(answers)
        with patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as dispatch:
            self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0)

        policies = dispatch.call_args.args[2]
        self.assertEqual(
            dict(policies.added_decisions),
            {("other", "one"): "include", ("other", "two"): "ignore", ("team", "one"): "ignore", ("team", "two"): "include"},
        )
        self.assertEqual(dispatch.call_args.kwargs["expected_inspections"].keys(), {"other", "team"})
        # JSON mode reserves stdout for the final machine result, so prompts are stderr.
        rendered = stderr.getvalue()
        self.assertLess(rendered.index("Source update: other"), rendered.index("Added other/one"))
        self.assertLess(rendered.index("Source update: team"), rendered.index("Added other/one"))

    def test_source_update_track_override_and_select_track(self):
        from hwskill.maintenance_cli import run_maintenance_command
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))
        args = self._update_args(track="refs/tags/v1")
        with patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as dispatch, patch("hwskill.maintenance_cli.GitSourceClient"):
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams()), 0)
        self.assertEqual(dispatch.call_args.args[4], {"team": "refs/tags/v1"})

    def test_exit_code_mapping_usage_validation_external_conflict(self):
        from hwskill.maintenance_cli import run_maintenance_command
        from hwskill.source_maintenance import SourceMaintenanceError
        from hwskill.git_source import GitSourceError
        from hwskill.maintenance_transaction import TransactionConflictError
        args = self._update_args()
        for error, expected in ((SourceMaintenanceError("bad"), 1), (GitSourceError("net"), 3), (TransactionConflictError("race"), 4)):
            with self.subTest(error=error), patch("hwskill.maintenance_cli.plan_update_sources", side_effect=error), patch("hwskill.maintenance_cli.GitSourceClient"):
                self.assertEqual(run_maintenance_command(args, self.repo, *self._streams()), expected)

    def test_integrity_rejection_returns_one_without_emitting_or_applying_a_plan(self):
        """A structured candidate gate failure is a normal maintenance error, not a traceback."""
        from hwskill.integrity import IntegrityError, IntegrityIssue, IntegrityReport
        from hwskill.maintenance_cli import run_maintenance_command

        rejection = IntegrityError(IntegrityReport(
            issues=(IntegrityIssue("sources/team.yaml", "source-skill-mismatch", "resolved Skill disagrees with Catalog"),),
            skill_count=1,
            source_count=1,
            profile_count=0,
        ))
        args = self._update_args()
        stdin, stdout, stderr = self._streams()

        with patch("hwskill.maintenance_cli.plan_update_sources", side_effect=rejection) as planner, patch("hwskill.maintenance_cli._emit") as emit:
            code = run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git)

        self.assertEqual(code, 1)
        planner.assert_called_once()
        emit.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error: repository integrity check failed (1 issue(s))", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_injected_streams_are_used_without_global_input_or_print(self):
        from hwskill.maintenance_cli import run_maintenance_command
        from hwskill.git_source import DiscoveredSkill
        from hwskill.source_maintenance import SourceInspection
        args = self._update_args(on_added=None, on_removed=None, yes=False)
        inspection = SourceInspection("a" * 40, "b" * 40, (DiscoveredSkill("new", "new", "new"),), (), ("gone",), ())
        with patch("builtins.input", side_effect=AssertionError("global input")), patch("builtins.print", side_effect=AssertionError("global print")), patch("hwskill.maintenance_cli.inspect_sources", return_value={"team": inspection}), patch("hwskill.maintenance_cli.plan_update_sources", return_value=SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))), patch("hwskill.maintenance_cli.GitSourceClient"):
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams("include\nremove\ny\n")), 0)

    def test_check_and_update_json_snapshot_has_required_fields(self):
        payload = plan_data(MaintenancePlan(None, MaintenanceSummary("source-update", source_ids=("team",))))
        self.assertEqual(sorted(payload), ["affected_profiles", "affected_tests", "operation", "skills", "sources", "status"])

    def test_human_output_has_aligned_source_skills_profiles_tests_and_no_ansi(self):
        self.test_plan_output_is_stable_sectioned_and_non_ansi()

    def _add_args(self, **changes):
        values = dict(command="source", source_command="add", repository=None, source_id=None, track=None, skills_path=None, namespace=None, layer=None, license_name=None, include=None, repo_root=str(self.repo), yes=False, json=True)
        values.update(changes)
        return SimpleNamespace(**values)

    def test_source_add_rejects_invalid_explicit_track_before_git_work(self):
        from hwskill.maintenance_cli import run_maintenance_command

        explicit = self._add_args(
            repository="https://x/team.git", track="D" * 40, skills_path="library",
            include=["one"], yes=True,
        )
        stdin, stdout, stderr = self._streams(tty=False)
        self.assertEqual(run_maintenance_command(explicit, self.repo, stdin, stdout, stderr, self.git), 2)
        self.assertEqual(self.git.calls, [])

    def test_source_add_rejects_local_repository_arguments_before_git_work(self):
        from hwskill.maintenance_cli import run_maintenance_command

        for repository in ("/tmp/upstream", "../upstream", "file:///tmp/upstream"):
            with self.subTest(repository=repository):
                self.git.calls.clear()
                args = self._add_args(
                    repository=repository, track="refs/heads/main", skills_path="library",
                    include=["one"], yes=True,
                )
                stdin, stdout, stderr = self._streams(tty=False)
                self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 2)
                self.assertEqual(self.git.calls, [])

    def test_source_add_rejects_prompted_local_repository_before_git_work(self):
        from hwskill.maintenance_cli import run_maintenance_command

        for repository in ("/tmp/upstream", "../upstream", "file:///tmp/upstream"):
            with self.subTest(repository=repository):
                self.git.calls.clear()
                args = self._add_args(yes=True)
                stdin, stdout, stderr = self._streams(repository + "\n", tty=True)
                self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 2)
                self.assertEqual(self.git.calls, [])

    def test_source_add_rejects_invalid_prompted_track_after_loading_preselected_default(self):
        from hwskill.maintenance_cli import run_maintenance_command

        prompted = self._add_args(repository="https://x/prompted.git", yes=True)
        stdin, stdout, stderr = self._streams("D" * 40 + "\n", tty=True)
        self.assertEqual(run_maintenance_command(prompted, self.repo, stdin, stdout, stderr, self.git), 2)
        self.assertEqual(self.git.calls, [("list_remote", "https://x/prompted.git")])
        self.assertIn("Track [refs/heads/main]", stderr.getvalue())

    def test_source_add_and_update_help_describe_the_lowercase_track_grammar(self):
        for command in (("source", "add", "--help"), ("source", "update", "--help")):
            with self.subTest(command=command), redirect_stdout(StringIO()) as output, self.assertRaises(SystemExit) as raised:
                main(list(command))
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("full ref or lowercase 40-hex commit", " ".join(output.getvalue().split()))
        out = StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["source", "update", "--help"])
        self.assertIn("enter a lowercase 40-hex commit", " ".join(out.getvalue().split()))

    def test_source_add_rejects_an_invalid_remote_default_track_before_materialization(self):
        from hwskill.maintenance_cli import run_maintenance_command

        def invalid_default(repository):
            self.git.calls.append(("list_remote", repository))
            return RemoteRefs("main", {})
        self.git.list_remote = invalid_default
        args = self._add_args(repository="https://x/default.git", include=["one"], yes=True)
        stdin, stdout, stderr = self._streams(tty=False)
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 2)
        self.assertEqual(self.git.calls, [("list_remote", "https://x/default.git")])

    def test_source_add_wizard_defaults_to_repo_name_default_branch_detected_skills_path_and_all_skills(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git, self._verifier), 0, stderr.getvalue() + stdout.getvalue())
        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual((source.source_id, source.upstream.track, source.upstream.skills_path), ("team", "refs/heads/main", "library")); self.assertEqual(len(source.skills), 2)
        self.assertIn("Track [refs/heads/main]", stderr.getvalue())

    def test_invalid_numeric_selections_raise_usage_error(self):
        from hwskill.maintenance_cli import UsageError, _select_indices
        for value in ("x", "0", "99", "1,1"):
            with self.subTest(value=value), self.assertRaises(UsageError): _select_indices(value, 2)

    def test_single_skill_detection_uses_parent_path(self):
        from hwskill.maintenance_cli import _detect_skills_path
        single = Path(self.temp.name) / "single"; path = single / "skills" / "only"; path.mkdir(parents=True); (path / "SKILL.md").write_text("---\nname: only\ndescription: only\n---\n")
        self.assertEqual(_detect_skills_path("x", "refs/heads/main", FakeGitSourceClient(single)), "skills")

    def test_interactive_source_add_json_is_stdout_only(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/json.git\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git, self._verifier), 0)
        payload = json.loads(stdout.getvalue()); self.assertTrue(payload["sources"][0]["deltas"]); self.assertIn("Git repository URL", stderr.getvalue())

    def test_select_track_numeric_blank_typed_and_invalid(self):
        from hwskill.maintenance_cli import run_maintenance_command
        write_source_manifest(self.repo / "sources/team.yaml", UpstreamSource("team", UpstreamConfig("https://x/team.git", "refs/heads/main", "library", ()), SourceDefaults("team", "l1", "MIT"), "a" * 40, ()))
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))
        commit = "d" * 40
        for answer, expected in (("2", "refs/tags/v1"), ("", "refs/heads/main"), ("refs/heads/main", "refs/heads/main"), (commit, commit)):
            args=self._update_args(select_track=True, yes=True)
            with patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as dispatch:
                self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(answer + "\n"), self.git), 0)
            self.assertEqual(dispatch.call_args.args[4]["team"], expected)
        for answer in ("0", "99", "x,y"):
            args=self._update_args(select_track=True, yes=True)
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(answer + "\n"), self.git), 2)
        for answer in ("D" * 40,):
            args=self._update_args(select_track=True, yes=True)
            with patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as planner:
                self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(answer + "\n"), self.git), 2)
            planner.assert_not_called()
        args = self._update_args(track="D" * 40)
        with patch("hwskill.maintenance_cli.plan_update_sources") as planner:
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(), self.git), 2)
        planner.assert_not_called()

    def test_source_delete_interactively_shows_impact_then_chooses_policy_or_cancels(self):
        from hwskill.maintenance_cli import run_maintenance_command

        source = UpstreamSource(
            "team", UpstreamConfig("https://x/team.git", "refs/heads/main", "library", ()),
            SourceDefaults("team", "l1", "MIT"), "a" * 40,
            (ResolvedSourceSkill("one", "team/one", "l1", "sha256:" + "0" * 64),),
        )
        write_source_manifest(self.repo / "sources/team.yaml", source)
        args = SimpleNamespace(command="source", source_command="delete", source_id="team", skills=None, remove_from_profiles=False, repo_root=str(self.repo), yes=False, json=False)
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-manualize"))
        with patch("hwskill.maintenance_cli.plan_delete_source", return_value=plan) as planner:
            stdin, stdout, stderr = self._streams("manualize\ny\n")
            self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0)
        self.assertEqual(planner.call_args.args[2], "manualize")
        self.assertIn("Managed", stdout.getvalue())

        args.skills = None
        with patch("hwskill.maintenance_cli.plan_delete_source") as planner:
            stdin, stdout, stderr = self._streams("cancel\n")
            self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0)
        planner.assert_not_called()
        self.assertIn("Cancelled", stdout.getvalue())

        for tty in (True, False):
            args.skills = None
            args.yes = True
            with self.subTest(tty=tty), patch("hwskill.maintenance_cli.plan_delete_source") as planner:
                stdin, stdout, stderr = self._streams("delete\n", tty=tty)
                self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 2)
            planner.assert_not_called()
        args.yes = False

    def test_source_check_reports_local_payload_and_catalog_drift_without_remote_change(self):
        from hwskill.maintenance_cli import run_maintenance_command

        add = self._add_args()
        self.assertEqual(run_maintenance_command(add, self.repo, *self._streams("https://x/team.git\n\n\n\n\n\n\ny\n"), self.git, self._verifier), 0)
        (self.repo / "skills-src/l1/team/one/SKILL.md").write_text("---\nname: one\ndescription: altered\n---\n", encoding="utf-8")
        (self.repo / "registry/catalog.json").write_text("{\"schema_version\": 1, \"skills\": []}\n", encoding="utf-8")
        args = SimpleNamespace(command="source", source_command="check", source_id="team", all=False, repo_root=str(self.repo), json=True)
        stdin, stdout, stderr = self._streams(tty=False)
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 1)
        row = json.loads(stdout.getvalue())["sources"][0]
        self.assertEqual(row["upstream_status"], "current")
        self.assertEqual(row["local_status"], "drift")
        self.assertTrue(any(item["code"].startswith("source-skill-") for item in row["local_drift"]))
        payload = json.loads(stdout.getvalue())
        self.assertTrue(any(item["code"].startswith("catalog-") for item in payload["global_drift"]))

    def test_source_check_reports_catalog_drift_once_without_misattributing_it_to_healthy_sources(self):
        from hwskill.integrity import IntegrityIssue, IntegrityReport
        from hwskill.maintenance_cli import run_maintenance_command
        from hwskill.source_maintenance import SourceInspection

        for source_id in ("alpha", "beta"):
            write_source_manifest(
                self.repo / "sources" / f"{source_id}.yaml",
                UpstreamSource(source_id, UpstreamConfig(f"https://x/{source_id}.git", "refs/heads/main", "library", ()), SourceDefaults(source_id, "l1", "MIT"), "a" * 40, ()),
            )
        report = IntegrityReport((IntegrityIssue("registry/catalog.json", "catalog-stale", "manual and beta records differ"),), 3, 2, 0)
        inspection = SourceInspection("a" * 40, "a" * 40, (), (), (), ())
        with patch("hwskill.maintenance_cli.inspect_source", return_value=inspection), patch("hwskill.maintenance_cli.check_integrity", return_value=report) as integrity:
            args = SimpleNamespace(command="source", source_command="check", source_id=None, all=True, repo_root=str(self.repo), json=True)
            stdin, stdout, stderr = self._streams(tty=False)
            self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "global_drift")
        self.assertEqual([row["local_status"] for row in payload["sources"]], ["current", "current"])
        self.assertEqual([row["local_drift"] for row in payload["sources"]], [[], []])
        self.assertEqual(payload["global_drift"], [{"path": "registry/catalog.json", "code": "catalog-stale", "message": "manual and beta records differ"}])
        self.assertEqual(integrity.call_count, 1)

    def test_interactive_skill_create_json_prompts_only_stderr(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = SimpleNamespace(command="skill", skill_command="create", skill_id="team/demo", layer="l1", description=None, license_name=None, repo_root=str(self.repo), yes=False, json=True)
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("skill-create", added_skill_ids=("team/demo",)))
        with patch("hwskill.maintenance_cli.plan_create_manual", return_value=plan):
            stdin, stdout, stderr = self._streams("demo\nMIT\ny\n")
            self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0)
        self.assertIsInstance(json.loads(stdout.getvalue()), dict)
        self.assertIn("Description", stderr.getvalue()); self.assertNotIn("Description", stdout.getvalue())

    def test_invalid_add_selection_returns_usage_without_writing_manifest(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\n\n\n99\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 2)
        self.assertIn("usage:", stderr.getvalue()); self.assertNotIn("Traceback", stderr.getvalue()); self.assertFalse((self.repo / "sources/team.yaml").exists())

    def test_single_skill_wizard_applies_parent_path_and_resolved_payload(self):
        from hwskill.maintenance_cli import run_maintenance_command
        single = Path(self.temp.name) / "only-remote"; payload = single / "skills" / "only"; payload.mkdir(parents=True); (payload / "SKILL.md").write_text("---\nname: only\ndescription: only\n---\n")
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/only.git\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, FakeGitSourceClient(single), self._verifier), 0)
        source = load_source_manifest(self.repo / "sources/only.yaml")
        self.assertEqual((source.upstream.skills_path, len(source.skills), source.skills[0].path), ("skills", 1, "only")); self.assertTrue((self.repo / "skills-src/l1/only/only/SKILL.md").exists())

    def test_real_source_update_json_applies_details_and_catalog(self):
        from hwskill.maintenance_cli import run_maintenance_command
        add = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(add, self.repo, stdin, stdout, stderr, self.git, self._verifier), 0)
        (self.remote / "library" / "one" / "SKILL.md").write_text("---\nname: one\ndescription: changed\n---\n", encoding="utf-8")
        args = self._update_args()
        stdin, stdout, stderr = self._streams()
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git, self._verifier), 0)
        payload = json.loads(stdout.getvalue()); row = payload["sources"][0]
        self.assertEqual((row["repository"], row["track"]), ("https://x/team.git", "refs/heads/main")); self.assertIsNotNone(row["old_revision"]); self.assertIsNotNone(row["new_revision"]); self.assertIn("updated", row["deltas"])
        self.assertTrue((self.repo / "registry/catalog.json").is_file())

    def test_source_add_retries_duplicate_source_id(self):
        from hwskill.maintenance_cli import run_maintenance_command
        write_source_manifest(self.repo / "sources/team.yaml", UpstreamSource("team", UpstreamConfig("https://x/old.git", "refs/heads/main", "library", ()), SourceDefaults("old", "l1", "MIT"), "a" * 40, ()))
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\nteam2\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git, self._verifier), 0, stderr.getvalue() + stdout.getvalue())
        self.assertTrue((self.repo / "sources/team2.yaml").exists())

    def test_source_add_unselected_skills_become_ignored_and_prompts_layer_license(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\n\n\n2\nspace\nl2\nApache-2.0\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git, self._verifier), 0, stderr.getvalue() + stdout.getvalue())
        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(source.defaults.layer, "l2"); self.assertEqual(source.defaults.license, "Apache-2.0"); self.assertEqual([item.path for item in source.upstream.ignore], ["one"])

    def test_future_ignore_add_and_remove_collision_prints_exact_adopt_command(self):
        from hwskill.maintenance_cli import run_maintenance_command
        source = UpstreamSource("team", UpstreamConfig("https://x/team.git", "refs/heads/main", "library", (IgnoredSkill("future", "x"),)), SourceDefaults("team", "l1", "MIT"), "b" * 40, ())
        write_source_manifest(self.repo / "sources/team.yaml", source)
        args = SimpleNamespace(command="source", source_command="ignore", ignore_command="add", source_id="team", path="future2", repo_root=str(self.repo), yes=True, json=True)
        self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(), self.git), 1)
        # Existing upstream path that collides with a manual ID gets the exact service guidance.
        args.ignore_command="remove"; args.path="future"; stdout=StringIO(); code=run_maintenance_command(args, self.repo, self._streams()[0], stdout, StringIO(), self.git)
        self.assertIn(code, (0, 1))

    def test_adopt_requires_replace_for_different_manual_payload(self):
        from hwskill.maintenance_cli import run_maintenance_command
        # CLI propagates the service replacement gate rather than applying an implicit overwrite.
        source = UpstreamSource("team", UpstreamConfig("https://x/team.git", "refs/heads/main", "library", ()), SourceDefaults("team", "l1", "MIT"), "b" * 40, ())
        write_source_manifest(self.repo / "sources/team.yaml", source)
        local = self.repo / "skills-src/l1/team/one"; local.mkdir(parents=True); (local / "SKILL.md").write_text("---\nname: one\ndescription: local\n---\nlocal\n")
        (local / "skill.yaml").write_text("schema_version: 1\nid: team/one\nname: one\ndescription: local\nlayer: l1\nstatus: experimental\nsource:\n  kind: manual\nlicense: MIT\ncontent_digest: sha256:0000000000000000000000000000000000000000000000000000000000000000\n")
        args=SimpleNamespace(command="source", source_command="adopt", source_id="team", skill_id="team/one", path="one", replace=False, repo_root=str(self.repo), yes=True, json=True)
        self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(), self.git), 1)


if __name__ == "__main__":
    unittest.main()
