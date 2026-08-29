from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hwskill.cli import default_audit_path, main
from hwskill.configuration import END, START
from hwskill.profiles import resolve_profile_ids


ROOT = Path(__file__).parents[1]


class CliRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        self.project.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_bind_resolve_search_and_load_formats(self):
        code, _, _ = self.run_cli(
            "profile", "bind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.assertEqual(code, 0)
        code, output, _ = self.run_cli(
            "skill", "search", "debug", "--project", str(self.project),
            "--repo-root", str(ROOT),
        )
        self.assertEqual(code, 0)
        self.assertIn("ID", output)
        code, output, _ = self.run_cli(
            "skill", "search", "debug", "--project", str(self.project),
            "--repo-root", str(ROOT), "--json",
        )
        self.assertIsInstance(json.loads(output)["results"], list)
        code, output, _ = self.run_cli(
            "skill", "load", "superpowers/systematic-debugging",
            "--project", str(self.project), "--repo-root", str(ROOT),
        )
        self.assertEqual(code, 0)
        self.assertIn("x-hwskill-runtime", output)

    def test_setup_preserves_unmanaged_skills_and_config(self):
        unmanaged = self.project / ".agents/skills/legacy/SKILL.md"
        unmanaged.parent.mkdir(parents=True)
        unmanaged.write_text("---\nname: legacy\ndescription: old\n---\n", encoding="utf-8")
        config = self.project / ".codex/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('model = "existing-model"\n', encoding="utf-8")
        code, _, _ = self.run_cli(
            "setup", "codex", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.assertEqual(code, 0)
        self.assertTrue(unmanaged.exists())
        self.assertIn('model = "existing-model"', config.read_text(encoding="utf-8"))
        self.assertFalse((self.project / ".agents/skills/hwskill").exists())

        code, _, _ = self.run_cli(
            "unsetup", "codex", "--project", str(self.project), "--yes",
        )
        self.assertEqual(code, 0)
        updated = config.read_text(encoding="utf-8")
        self.assertIn('model = "existing-model"', updated)
        self.assertNotIn(START, updated)
        self.assertNotIn(END, updated)
        self.assertTrue(unmanaged.exists())

    def test_setup_can_pin_audit_path_in_managed_commands(self):
        audit = self.project / "logs/audit.jsonl"
        code, _, _ = self.run_cli(
            "setup", "codex", "--project", str(self.project),
            "--repo-root", str(ROOT), "--audit-path", str(audit), "--yes",
        )
        self.assertEqual(code, 0)
        config = (self.project / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertEqual(config.count(str(audit)), 2)

    def test_unsetup_refuses_externally_modified_managed_block(self):
        self.run_cli(
            "setup", "codex", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        config = self.project / ".codex/config.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("required = true", "required = false"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "modified outside hwskill"):
            self.run_cli("unsetup", "codex", "--project", str(self.project), "--yes")
        self.assertIn(START, config.read_text(encoding="utf-8"))

    def test_profile_show_unbind_and_doctor_json(self):
        self.run_cli(
            "profile", "bind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        code, output, _ = self.run_cli(
            "profile", "show", "--project", str(self.project),
            "--repo-root", str(ROOT), "--json",
        )
        self.assertEqual(json.loads(output)["profiles"], ["codex-demo"])
        self.run_cli(
            "setup", "codex", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        code, output, _ = self.run_cli(
            "doctor", "codex", "--project", str(self.project),
            "--repo-root", str(ROOT), "--json",
        )
        checks = json.loads(output)["checks"]
        self.assertTrue(all(item["status"] in {"PASS", "WARN"} for item in checks))
        self.assertTrue({"mcp-config", "hook-config", "codex-cli", "audit-directory"}.issubset(
            {item["name"] for item in checks}
        ))
        code, _, _ = self.run_cli(
            "profile", "unbind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.assertEqual(resolve_profile_ids(self.project), [])

    def test_read_only_command_discovers_git_root_without_project(self):
        (self.project / ".git").mkdir()
        nested = self.project / "nested"
        nested.mkdir()
        with patch("hwskill.cli.Path.cwd", return_value=nested):
            code, output, error = self.run_cli(
                "profile", "show", "--project", "--repo-root", str(ROOT), "--json"
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["profiles"], [])
        self.assertIn(str(self.project), error)

    def test_profile_set_requires_exactly_one_scope(self):
        with self.assertRaises(SystemExit):
            main(["profile", "set", "personal-baseline"])
        with self.assertRaises(SystemExit):
            main([
                "profile", "set", "personal-baseline", "--user", "--project"
            ])

    def test_profile_set_empty_disables_user_fallback(self):
        with patch.dict(
            os.environ,
            {
                "HOME": str(self.project / "home"),
                "XDG_CONFIG_HOME": str(self.project / "config"),
                "XDG_STATE_HOME": str(self.project / "state"),
            },
            clear=True,
        ):
            self.run_cli(
                "profile", "set", "personal-baseline", "--user",
                "--repo-root", str(ROOT), "--yes",
            )
            self.run_cli(
                "profile", "set", "--empty", "--project", str(self.project),
                "--repo-root", str(ROOT), "--yes",
            )
            _, output, _ = self.run_cli(
                "profile", "show", "--project", str(self.project),
                "--repo-root", str(ROOT),
            )

        self.assertIn("EFFECTIVE_SCOPE\tproject", output)
        self.assertIn("PROFILES\tnone", output)

    def test_profile_list_lists_registry_definitions(self):
        _, output, _ = self.run_cli(
            "profile", "list", "--repo-root", str(ROOT)
        )

        self.assertIn("personal-baseline", output)
        self.assertIn("superpowers", output)

    def test_profile_show_project_reports_user_fallback(self):
        with patch.dict(
            os.environ,
            {
                "HOME": str(self.project / "home"),
                "XDG_CONFIG_HOME": str(self.project / "config"),
                "XDG_STATE_HOME": str(self.project / "state"),
            },
            clear=True,
        ):
            self.run_cli(
                "profile", "set", "personal-baseline,superpowers", "--user",
                "--repo-root", str(ROOT), "--yes",
            )
            _, output, _ = self.run_cli(
                "profile", "show", "--project", str(self.project),
                "--repo-root", str(ROOT),
            )

        self.assertIn("EFFECTIVE_SCOPE\tuser", output)
        self.assertIn("PROFILES\tpersonal-baseline,superpowers", output)

    def test_skill_list_and_batch_dump_accept_ids_and_unique_names(self):
        _, listed, _ = self.run_cli(
            "skill", "list", "--repo-root", str(ROOT), "--json"
        )
        records = json.loads(listed)["skills"]
        self.assertTrue(
            {"id", "name", "layer", "revision"}.issubset(records[0])
        )

        destination = self.project / "exported"
        _, dumped, _ = self.run_cli(
            "skill", "dump",
            "local/chinese-thinking,systematic-debugging",
            str(destination), "--repo-root", str(ROOT), "--json",
        )
        results = json.loads(dumped)["results"]
        self.assertEqual(
            [item["skill_id"] for item in results],
            ["local/chinese-thinking", "superpowers/systematic-debugging"],
        )
        self.assertTrue((destination / "chinese-thinking/SKILL.md").is_file())
        self.assertTrue((destination / "systematic-debugging/SKILL.md").is_file())

    def test_skill_dump_profile_accepts_plural_profile_csv(self):
        destination = self.project / "profile-export"

        _, output, _ = self.run_cli(
            "skill", "dump-profile", "personal-baseline,codex-demo",
            str(destination), "--repo-root", str(ROOT),
        )

        self.assertIn("SKILL\tTARGET\tSTATUS", output)
        self.assertTrue((destination / "chinese-thinking/SKILL.md").is_file())
        self.assertTrue((destination / "systematic-debugging/SKILL.md").is_file())

    def test_setup_doctor_unsetup_require_scope_and_accept_bare_project(self):
        for command in ("setup", "doctor", "unsetup"):
            with self.subTest(command=command):
                with self.assertRaises(SystemExit):
                    main([command, "codex"])

        (self.project / ".git").mkdir()
        with patch("hwskill.cli.Path.cwd", return_value=self.project):
            code, _, _ = self.run_cli(
                "setup", "codex", "--project", "--repo-root", str(ROOT), "--yes"
            )
        self.assertEqual(code, 0)

    def test_info_silently_discovers_git_root_without_project(self):
        (self.project / ".git").mkdir()
        nested = self.project / "nested"
        nested.mkdir()

        with patch("hwskill.cli.Path.cwd", return_value=nested):
            code, output, error = self.run_cli(
                "info", "--repo-root", str(ROOT)
            )

        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn(f"project:     {self.project}", output)

    def test_noninteractive_write_requires_explicit_project_and_yes(self):
        with patch("hwskill.cli.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(SystemExit, "explicit --project and --yes"):
                self.run_cli("profile", "bind", "codex-demo", "--yes")

    def test_audit_path_can_be_provided_by_environment(self):
        custom = self.project / "audit.jsonl"
        with patch.dict("hwskill.cli.os.environ", {"HWSKILL_AUDIT_PATH": str(custom)}):
            self.assertEqual(default_audit_path(), custom)

    def test_repo_root_defaults_to_hwskill_home_outside_registry_checkout(self):
        previous = Path.cwd()
        try:
            os.chdir(self.project)
            with patch.dict("hwskill.cli.os.environ", {"HWSKILL_HOME": str(ROOT)}):
                try:
                    code, _, _ = self.run_cli(
                        "profile", "bind", "codex-demo",
                        "--project", str(self.project), "--yes",
                    )
                except Exception as exc:
                    self.fail(f"CLI ignored HWSKILL_HOME: {exc}")
        finally:
            os.chdir(previous)

        self.assertEqual(code, 0)
        self.assertEqual(resolve_profile_ids(self.project), ["codex-demo"])

    def test_claude_code_alias_setup_adapter_and_unsetup(self):
        self.run_cli(
            "profile", "bind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        code, output, _ = self.run_cli(
            "setup", "claude_code", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.assertEqual(code, 0)
        self.assertIn(".claude/settings.json", output)
        stdin = StringIO(json.dumps({"cwd": str(self.project), "session_id": "c1"}))
        stdout = StringIO()
        with patch("hwskill.cli.sys.stdin", stdin), redirect_stdout(stdout):
            code = main([
                "adapter", "claude-code", "session-start",
                "--repo-root", str(ROOT),
                "--audit-path", str(self.project / "audit.jsonl"),
            ])
        self.assertEqual(code, 0)
        self.assertIn("catalog_digest:", json.loads(stdout.getvalue())["hookSpecificOutput"]["additionalContext"])
        code, _, _ = self.run_cli(
            "unsetup", "claude-code", "--project", str(self.project), "--yes",
        )
        self.assertEqual(code, 0)

    def test_opencode_setup_catalog_adapter_and_unsetup(self):
        self.run_cli(
            "profile", "bind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        code, _, _ = self.run_cli(
            "setup", "opencode", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.assertEqual(code, 0)
        code, output, _ = self.run_cli(
            "adapter", "opencode", "catalog", "--project", str(self.project),
            "--repo-root", str(ROOT),
            "--audit-path", str(self.project / "audit.jsonl"),
        )
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("hwskill Effective Skill Catalog"))
        code, _, _ = self.run_cli(
            "unsetup", "opencode", "--project", str(self.project), "--yes",
        )
        self.assertEqual(code, 0)

    def test_doctor_routes_selected_host(self):
        self.run_cli(
            "profile", "bind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.run_cli(
            "setup", "claude-code", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        code, output, _ = self.run_cli(
            "doctor", "claude-code", "--project", str(self.project),
            "--repo-root", str(ROOT), "--json",
        )
        self.assertEqual(code, 0)
        names = {item["name"] for item in json.loads(output)["checks"]}
        self.assertIn("claude-hook-config", names)
        self.assertNotIn("codex-config", names)

    def test_user_doctor_checks_the_user_scoped_codex_setup(self):
        with patch.dict(
            os.environ,
            {
                "HOME": str(self.project / "home"),
                "XDG_CONFIG_HOME": str(self.project / "config"),
                "XDG_STATE_HOME": str(self.project / "state"),
                "CODEX_HOME": str(self.project / "codex-home"),
            },
            clear=True,
        ):
            self.run_cli(
                "profile", "set", "personal-baseline", "--user",
                "--repo-root", str(ROOT), "--yes",
            )
            self.run_cli(
                "setup", "codex", "--user", "--repo-root", str(ROOT), "--yes",
            )
            code, output, _ = self.run_cli(
                "doctor", "codex", "--user", "--repo-root", str(ROOT), "--json",
            )

        self.assertEqual(code, 0)
        checks = {item["name"]: item for item in json.loads(output)["checks"]}
        self.assertEqual(checks["mcp-config"]["status"], "PASS")
        self.assertEqual(checks["hook-config"]["status"], "PASS")
        self.assertEqual(checks["profile"]["status"], "PASS")
        self.assertIn("3 effective skills", checks["profile"]["detail"])

    def test_info_json_reports_cli_repository_source(self):
        try:
            code, output, _ = self.run_cli(
                "info", "--project", str(self.project),
                "--repo-root", str(ROOT), "--json",
            )
        except SystemExit as exc:
            self.fail(f"info command is not implemented: {exc}")

        self.assertEqual(code, 0)
        info = json.loads(output)
        self.assertEqual(info["installation"]["repository"], str(ROOT.resolve()))
        self.assertEqual(info["installation"]["repository_source"], "--repo-root")
        self.assertEqual(info["integration"]["project"], str(self.project.resolve()))

    def test_info_defaults_to_human_readable_summary(self):
        try:
            code, output, _ = self.run_cli(
                "info", "--project", str(self.project),
                "--repo-root", str(ROOT),
            )
        except SystemExit as exc:
            self.fail(f"info command is not implemented: {exc}")

        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("hwskill v0.1.0 "))
        self.assertIn("\nInstallation:\n", output)
        self.assertIn("\ninstall path:  ", output)
        self.assertIn("\ngit repo:      ", output)
        self.assertIn("\nIntegrations:\n", output)
        self.assertIn("codex        -- ", output)


if __name__ == "__main__":
    unittest.main()
