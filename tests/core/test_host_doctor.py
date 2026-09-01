from pathlib import Path
import json
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hwskill.configuration import setup_claude_code, setup_codex, setup_opencode
from hwskill.doctor import run_doctor
from hwskill.profiles import bind_profile
from hwskill.scopes import project_scope


ROOT = Path(__file__).parents[2]


class HostDoctorTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        self.project.mkdir()
        bind_profile(self.project, ROOT, "codex-demo")

    def tearDown(self):
        self.temp.cleanup()

    def statuses(self, host):
        try:
            results = run_doctor(host, self.project, ROOT)
        except TypeError as exc:
            raise AssertionError("doctor does not accept a host") from exc
        return {item.name: item for item in results}

    def test_claude_doctor_checks_project_hook_mcp_and_cli(self):
        setup_claude_code(self.project, ROOT)
        with patch("hwskill.doctor.shutil.which", return_value="/usr/bin/claude"):
            checks = self.statuses("claude-code")

        self.assertEqual(checks["claude-hook-config"].status, "PASS")
        self.assertEqual(checks["claude-mcp-config"].status, "PASS")
        self.assertEqual(checks["claude-cli"].status, "PASS")
        self.assertNotIn("codex-config", checks)

    def test_codex_doctor_rejects_comments_and_an_unowned_session_hook(self):
        config = self.project / ".codex/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "# [mcp_servers.hwskill]\n"
            "[[hooks.SessionStart]]\n"
            "[[hooks.SessionStart.hooks]]\n"
            'type = "command"\n'
            'command = "echo unrelated"\n',
            encoding="utf-8",
        )

        checks = self.statuses("codex")

        self.assertEqual(checks["mcp-config"].status, "WARN")
        self.assertEqual(checks["hook-config"].status, "WARN")

    def test_codex_doctor_rejects_a_modified_owned_block(self):
        setup_codex(project_scope(self.project), ROOT)
        config = self.project / ".codex/config.toml"
        original = config.read_text(encoding="utf-8")
        mutations = (
            original.replace("required = true", "required = false"),
            original.replace('command = "hwskill"', 'command = "other"', 1),
        )

        for mutated in mutations:
            with self.subTest(mutated=mutated):
                config.write_text(mutated, encoding="utf-8")
                checks = self.statuses("codex")
                self.assertEqual(checks["mcp-config"].status, "WARN")
                self.assertEqual(checks["hook-config"].status, "WARN")

    def test_codex_doctor_rejects_non_object_setup_state(self):
        setup_codex(project_scope(self.project), ROOT)
        state = self.project / ".hwskills/state/setup-codex.json"

        for invalid in ([], None, "invalid"):
            with self.subTest(invalid=invalid):
                state.write_text(json.dumps(invalid), encoding="utf-8")
                try:
                    checks = self.statuses("codex")
                except AttributeError as exc:
                    self.fail(f"non-object setup state escaped validation: {exc}")
                self.assertEqual(checks["mcp-config"].status, "WARN")
                self.assertEqual(checks["hook-config"].status, "WARN")

    def test_claude_doctor_warns_when_owned_mcp_command_is_modified(self):
        setup_claude_code(self.project, ROOT)
        path = self.project / ".mcp.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mcpServers"]["hwskill"]["command"] = "not-hwskill"
        path.write_text(json.dumps(data), encoding="utf-8")

        with patch("hwskill.doctor.shutil.which", return_value=None):
            checks = self.statuses("claude-code")

        self.assertEqual(checks["claude-mcp-config"].status, "WARN")

    def test_opencode_1_14_48_is_the_verified_version(self):
        setup_opencode(self.project, ROOT)
        completed = subprocess.CompletedProcess(
            ["opencode", "--version"], 0, stdout="1.14.48\n", stderr=""
        )
        fake_subprocess = SimpleNamespace(run=lambda *args, **kwargs: completed)
        with patch("hwskill.doctor.shutil.which", return_value="/usr/bin/opencode"), patch(
            "hwskill.doctor.subprocess", fake_subprocess, create=True
        ):
            checks = self.statuses("opencode")

        self.assertEqual(checks["opencode-plugin-config"].status, "PASS")
        self.assertEqual(checks["opencode-mcp-config"].status, "PASS")
        self.assertEqual(checks["opencode-version"].status, "PASS")

    def test_other_opencode_version_is_reported_as_unverified(self):
        setup_opencode(self.project, ROOT)
        completed = subprocess.CompletedProcess(
            ["opencode", "--version"], 0, stdout="1.15.0\n", stderr=""
        )
        fake_subprocess = SimpleNamespace(run=lambda *args, **kwargs: completed)
        with patch("hwskill.doctor.shutil.which", return_value="/usr/bin/opencode"), patch(
            "hwskill.doctor.subprocess", fake_subprocess, create=True
        ):
            checks = self.statuses("opencode")

        self.assertEqual(checks["opencode-version"].status, "WARN")
        self.assertIn("verified: 1.14.48", checks["opencode-version"].detail)

    def test_opencode_doctor_warns_for_disabled_mcp_and_modified_plugin(self):
        setup_opencode(self.project, ROOT)
        config_path = self.project / "opencode.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["mcp"]["hwskill"]["enabled"] = False
        config_path.write_text(json.dumps(config), encoding="utf-8")
        plugin = self.project / ".opencode/plugins/hwskill.js"
        plugin.write_text("export default {}\n", encoding="utf-8")
        completed = subprocess.CompletedProcess(
            ["opencode", "--version"], 0, stdout="1.14.48\n", stderr=""
        )
        fake_subprocess = SimpleNamespace(run=lambda *args, **kwargs: completed)

        with patch("hwskill.doctor.shutil.which", return_value="/usr/bin/opencode"), patch(
            "hwskill.doctor.subprocess", fake_subprocess, create=True
        ):
            checks = self.statuses("opencode")

        self.assertEqual(checks["opencode-mcp-config"].status, "WARN")
        self.assertEqual(checks["opencode-plugin-config"].status, "WARN")

    def test_existing_unmanaged_skills_are_warned_but_not_errors(self):
        legacy = self.project / ".claude/skills/legacy/SKILL.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\nname: legacy\n---\n", encoding="utf-8")
        setup_claude_code(self.project, ROOT)
        with patch("hwskill.doctor.shutil.which", return_value=None):
            checks = self.statuses("claude-code")

        self.assertEqual(checks["unmanaged-native-skills"].status, "WARN")
        self.assertNotIn("ERROR", {item.status for item in checks.values()})


if __name__ == "__main__":
    unittest.main()
