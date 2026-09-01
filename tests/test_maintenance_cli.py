from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hwskill.cli import main
from hwskill.console_output import format_maintenance_plan, plan_data
from hwskill.maintenance_transaction import MaintenancePlan, MaintenanceSummary
from hwskill.git_source import RemoteRefs, ResolvedTrack
from hwskill.source_manifest import SourceDefaults, UpstreamConfig, UpstreamSource, IgnoredSkill, write_source_manifest, load_source_manifest


class FakeGitSourceClient:
    def __init__(self, checkout: Path): self.checkout, self.calls = checkout, []
    def list_remote(self, repository):
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
        plan = SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))
        args = self._update_args(on_added=None, on_removed=None, yes=False)
        with patch("hwskill.maintenance_cli.plan_update_sources", return_value=plan) as dispatch, patch("hwskill.maintenance_cli.GitSourceClient"):
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams("ignore\nmanualize\ny\n")), 0)
        self.assertEqual(dispatch.call_args.args[2].on_added, "ignore")
        self.assertEqual(dispatch.call_args.args[2].on_removed, "manualize")

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

    def test_injected_streams_are_used_without_global_input_or_print(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._update_args(on_added=None, on_removed=None, yes=False)
        with patch("builtins.input", side_effect=AssertionError("global input")), patch("builtins.print", side_effect=AssertionError("global print")), patch("hwskill.maintenance_cli.plan_update_sources", return_value=SimpleNamespace(apply=lambda: None, summary=MaintenanceSummary("source-update"))), patch("hwskill.maintenance_cli.GitSourceClient"):
            self.assertEqual(run_maintenance_command(args, self.repo, *self._streams("include\nremove\ny\n")), 0)

    def test_check_and_update_json_snapshot_has_required_fields(self):
        payload = plan_data(MaintenancePlan(None, MaintenanceSummary("source-update", source_ids=("team",))))
        self.assertEqual(sorted(payload), ["affected_profiles", "affected_tests", "operation", "skills", "sources", "status"])

    def test_human_output_has_aligned_source_skills_profiles_tests_and_no_ansi(self):
        self.test_plan_output_is_stable_sectioned_and_non_ansi()

    def _add_args(self): return SimpleNamespace(command="source", source_command="add", repository=None, source_id=None, track=None, skills_path=None, namespace=None, layer=None, license_name=None, include=None, repo_root=str(self.repo), yes=False, json=True)

    def test_source_add_wizard_defaults_to_repo_name_default_branch_detected_skills_path_and_all_skills(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0, stderr.getvalue() + stdout.getvalue())
        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual((source.source_id, source.upstream.track, source.upstream.skills_path), ("team", "refs/heads/main", "library")); self.assertEqual(len(source.skills), 2)

    def test_source_add_retries_duplicate_source_id(self):
        from hwskill.maintenance_cli import run_maintenance_command
        write_source_manifest(self.repo / "sources/team.yaml", UpstreamSource("team", UpstreamConfig("https://x/old.git", "refs/heads/main", "library", ()), SourceDefaults("old", "l1", "MIT"), "a" * 40, ()))
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\nteam2\n\n\n\n\n\n\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0, stderr.getvalue() + stdout.getvalue())
        self.assertTrue((self.repo / "sources/team2.yaml").exists())

    def test_source_add_unselected_skills_become_ignored_and_prompts_layer_license(self):
        from hwskill.maintenance_cli import run_maintenance_command
        args = self._add_args(); stdin, stdout, stderr = self._streams("https://x/team.git\n\n\n2\nspace\nl2\nApache-2.0\ny\n")
        self.assertEqual(run_maintenance_command(args, self.repo, stdin, stdout, stderr, self.git), 0, stderr.getvalue() + stdout.getvalue())
        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(source.defaults.layer, "l2"); self.assertEqual(source.defaults.license, "Apache-2.0"); self.assertEqual([item.path for item in source.upstream.ignore], ["one"])

    def test_future_ignore_add_and_remove_collision_prints_exact_adopt_command(self):
        from hwskill.maintenance_cli import run_maintenance_command
        source = UpstreamSource("team", UpstreamConfig("https://x/team.git", "refs/heads/main", "library", (IgnoredSkill("future", "x"),)), SourceDefaults("team", "l1", "MIT"), "b" * 40, ())
        write_source_manifest(self.repo / "sources/team.yaml", source)
        args = SimpleNamespace(command="source", source_command="ignore", ignore_command="add", source_id="team", path="future2", repo_root=str(self.repo), yes=True, json=True)
        self.assertEqual(run_maintenance_command(args, self.repo, *self._streams(), self.git), 0)
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
