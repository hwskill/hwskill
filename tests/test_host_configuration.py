import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Sequence
import unittest
from unittest.mock import patch

from hwskill.scopes import project_scope, user_scope


ROOT = Path(__file__).parents[1]


def require_configuration(*names):
    from hwskill import configuration

    try:
        return tuple(getattr(configuration, name) for name in names)
    except AttributeError as exc:
        raise AssertionError(f"missing configuration interface: {exc.name}") from exc


class HostConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        self.project.mkdir()
        self.legacy_skill = self.project / ".agents/skills/legacy/SKILL.md"
        self.legacy_skill.parent.mkdir(parents=True)
        self.legacy_skill.write_text("---\nname: legacy\n---\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_claude_setup_preserves_settings_and_unsetup_removes_only_owned_entries(self):
        setup_claude_code, unsetup_claude_code = require_configuration(
            "setup_claude_code", "unsetup_claude_code"
        )

        settings = self.project / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps({"permissions": {"allow": ["Read"]}, "hooks": {"Stop": []}}),
            encoding="utf-8",
        )
        mcp = self.project / ".mcp.json"
        mcp.write_text(json.dumps({"mcpServers": {"legacy": {"command": "old"}}}), encoding="utf-8")

        first = setup_claude_code(self.project, ROOT)
        second = setup_claude_code(self.project, ROOT)

        configured = json.loads(settings.read_text(encoding="utf-8"))
        configured_mcp = json.loads(mcp.read_text(encoding="utf-8"))
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(configured["permissions"]["allow"], ["Read"])
        self.assertEqual(configured["hooks"]["Stop"], [])
        self.assertEqual(len(configured["hooks"]["SessionStart"]), 1)
        self.assertEqual(configured_mcp["mcpServers"]["legacy"]["command"], "old")
        self.assertEqual(configured_mcp["mcpServers"]["hwskill"]["command"], "hwskill")
        self.assertTrue(self.legacy_skill.exists())

        result = unsetup_claude_code(self.project)
        self.assertTrue(result.changed)
        cleaned = json.loads(settings.read_text(encoding="utf-8"))
        cleaned_mcp = json.loads(mcp.read_text(encoding="utf-8"))
        self.assertNotIn("SessionStart", cleaned["hooks"])
        self.assertEqual(cleaned["hooks"]["Stop"], [])
        self.assertNotIn("hwskill", cleaned_mcp["mcpServers"])
        self.assertIn("legacy", cleaned_mcp["mcpServers"])
        self.assertTrue(self.legacy_skill.exists())

    def test_user_codex_setup_writes_codex_home_without_pinning_project(self):
        setup_codex, codex_setup_is_current, unsetup_codex = require_configuration(
            "setup_codex", "codex_setup_is_current", "unsetup_codex"
        )
        codex_home = self.project / "user-codex"
        with patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(codex_home),
                "XDG_CONFIG_HOME": str(self.project / "user-config"),
                "XDG_STATE_HOME": str(self.project / "user-state"),
            },
        ):
            target = user_scope()
            result = setup_codex(target, ROOT)
            text = result.config_path.read_text(encoding="utf-8")

            self.assertEqual(result.config_path, codex_home / "config.toml")
            self.assertNotIn("--project", text)
            self.assertIn("--scope user", text)
            self.assertTrue(codex_setup_is_current(target))
            self.assertTrue(unsetup_codex(target).changed)

    def test_project_codex_setup_keeps_explicit_project_runtime(self):
        setup_codex, codex_setup_is_current = require_configuration(
            "setup_codex", "codex_setup_is_current"
        )
        target = project_scope(self.project)

        setup_codex(target, ROOT)
        text = (self.project / ".codex/config.toml").read_text(encoding="utf-8")

        self.assertIn(str(self.project.resolve()), text)
        self.assertIn("--scope project", text)
        self.assertTrue(codex_setup_is_current(target))

    def test_user_claude_setup_uses_settings_file_and_official_mcp_cli(self):
        setup_claude_code, claude_setup_is_current, unsetup_claude_code = (
            require_configuration(
                "setup_claude_code", "claude_setup_is_current", "unsetup_claude_code"
            )
        )

        class FakeRunner:
            def __init__(self):
                self.calls: list[tuple[str, ...]] = []
                self.mcp = None

            def __call__(
                self, argv: Sequence[str]
            ) -> subprocess.CompletedProcess[str]:
                call = tuple(argv)
                self.calls.append(call)
                if call[:4] == ("claude", "mcp", "get", "hwskill"):
                    if self.mcp is None:
                        return subprocess.CompletedProcess(call, 1, "", "not found")
                    output = (
                        "hwskill:\n"
                        "  Scope: User config (available in all your projects)\n"
                        "  Status: ✓ Connected\n"
                        f"  Type: {self.mcp['type']}\n"
                        f"  Command: {self.mcp['command']}\n"
                        f"  Args: {' '.join(self.mcp['args'])}\n"
                    )
                    return subprocess.CompletedProcess(call, 0, output, "")
                if call[:4] == ("claude", "mcp", "add-json", "hwskill"):
                    self.mcp = json.loads(call[4])
                if call[:4] == ("claude", "mcp", "remove", "hwskill"):
                    self.mcp = None
                return subprocess.CompletedProcess(call, 0, "", "")

        runner = FakeRunner()
        with patch.dict(
            os.environ,
            {
                "CLAUDE_CONFIG_DIR": str(self.project / "claude-home"),
                "XDG_CONFIG_HOME": str(self.project / "user-config"),
                "XDG_STATE_HOME": str(self.project / "user-state"),
            },
        ):
            target = user_scope()
            result = setup_claude_code(target, ROOT, command_runner=runner)

            self.assertEqual(
                result.config_path,
                Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json",
            )
            add = next(
                call
                for call in runner.calls
                if call[:4] == ("claude", "mcp", "add-json", "hwskill")
            )
            self.assertIn("--scope", add)
            self.assertIn("user", add)
            self.assertNotIn("--project", json.dumps(add))
            self.assertTrue(claude_setup_is_current(target, command_runner=runner))
            get_calls = [
                call for call in runner.calls
                if call[:4] == ("claude", "mcp", "get", "hwskill")
            ]
            self.assertTrue(get_calls)
            self.assertTrue(all(
                call == ("claude", "mcp", "get", "hwskill")
                for call in get_calls
            ))
            self.assertTrue(unsetup_claude_code(target, command_runner=runner).changed)
            self.assertIn(
                ("claude", "mcp", "remove", "hwskill", "--scope", "user"),
                runner.calls,
            )

    def test_user_claude_refuses_shadowed_or_modified_mcp_entry(self):
        setup_claude_code, claude_setup_is_current, unsetup_claude_code = (
            require_configuration(
                "setup_claude_code", "claude_setup_is_current", "unsetup_claude_code"
            )
        )

        class FakeRunner:
            def __init__(self):
                self.mcp = None
                self.scope = "User config (available in all your projects)"

            def __call__(
                self, argv: Sequence[str]
            ) -> subprocess.CompletedProcess[str]:
                call = tuple(argv)
                if call[:4] == ("claude", "mcp", "get", "hwskill"):
                    if self.mcp is None:
                        return subprocess.CompletedProcess(call, 1, "", "not found")
                    output = (
                        "hwskill:\n"
                        f"  Scope: {self.scope}\n"
                        "  Status: ✓ Connected\n"
                        f"  Type: {self.mcp['type']}\n"
                        f"  Command: {self.mcp['command']}\n"
                        f"  Args: {' '.join(self.mcp['args'])}\n"
                    )
                    return subprocess.CompletedProcess(call, 0, output, "")
                if call[:4] == ("claude", "mcp", "add-json", "hwskill"):
                    self.mcp = json.loads(call[4])
                if call[:4] == ("claude", "mcp", "remove", "hwskill"):
                    self.mcp = None
                return subprocess.CompletedProcess(call, 0, "", "")

        runner = FakeRunner()
        with patch.dict(
            os.environ,
            {
                "CLAUDE_CONFIG_DIR": str(self.project / "claude-home"),
                "XDG_CONFIG_HOME": str(self.project / "user-config"),
                "XDG_STATE_HOME": str(self.project / "user-state"),
            },
        ):
            target = user_scope()
            setup_claude_code(target, ROOT, command_runner=runner)

            runner.scope = "Project config (private to this project)"
            self.assertFalse(claude_setup_is_current(target, command_runner=runner))

            runner.scope = "User config (available in all your projects)"
            runner.mcp["command"] = "foreign-command"
            self.assertFalse(claude_setup_is_current(target, command_runner=runner))
            settings_path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
            state_path = Path(os.environ["XDG_STATE_HOME"]) / "hwskill/setup-claude-code.json"
            settings_before = settings_path.read_text(encoding="utf-8")
            state_before = state_path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified outside hwskill"):
                setup_claude_code(
                    target,
                    ROOT,
                    self.project / "different-audit.jsonl",
                    command_runner=runner,
                )
            self.assertEqual(settings_path.read_text(encoding="utf-8"), settings_before)
            self.assertEqual(state_path.read_text(encoding="utf-8"), state_before)
            with self.assertRaisesRegex(ValueError, "modified outside hwskill"):
                unsetup_claude_code(target, command_runner=runner)
            self.assertIsNotNone(runner.mcp)

    def test_user_opencode_setup_uses_xdg_global_config_and_dynamic_plugin(self):
        setup_opencode, opencode_setup_is_current = require_configuration(
            "setup_opencode", "opencode_setup_is_current"
        )
        with patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(self.project / "user-config"),
                "XDG_STATE_HOME": str(self.project / "user-state"),
            },
        ):
            target = user_scope()
            result = setup_opencode(target, ROOT)
            root = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode"
            plugin = (root / "plugins/hwskill.js").read_text(encoding="utf-8")

            self.assertEqual(result.config_path, root / "opencode.json")
            self.assertIn("process.cwd()", plugin)
            self.assertIn("--scope", plugin)
            self.assertNotIn(str(self.project.resolve()), plugin)
            self.assertTrue(opencode_setup_is_current(target))

    def test_claude_setup_refuses_unowned_hwskill_mcp(self):
        setup_claude_code, = require_configuration("setup_claude_code")

        (self.project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"hwskill": {"command": "someone-else"}}}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "not owned"):
            setup_claude_code(self.project, ROOT)
        self.assertFalse((self.project / ".claude/settings.json").exists())
        self.assertFalse(
            (self.project / ".hwskills/state/setup-claude-code.json").exists()
        )

    def test_setup_preserves_existing_top_level_json_key_order(self):
        setup_claude_code, = require_configuration("setup_claude_code")
        settings = self.project / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"zeta": 1, "alpha": 2}\n', encoding="utf-8")

        setup_claude_code(self.project, ROOT)

        configured = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(list(configured)[:2], ["zeta", "alpha"])

    def test_claude_unsetup_refuses_externally_modified_hook(self):
        setup_claude_code, unsetup_claude_code = require_configuration(
            "setup_claude_code", "unsetup_claude_code"
        )

        setup_claude_code(self.project, ROOT)
        settings = self.project / ".claude/settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["hooks"]["SessionStart"][0]["matcher"] = "startup"
        settings.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "modified outside hwskill"):
            unsetup_claude_code(self.project)

    def test_opencode_setup_preserves_config_and_writes_runtime_catalog_plugin(self):
        setup_opencode, = require_configuration("setup_opencode")

        config = self.project / "opencode.json"
        config.write_text(json.dumps({"theme": "system", "mcp": {"legacy": {"enabled": True}}}), encoding="utf-8")
        audit = self.project / "logs/audit.jsonl"

        first = setup_opencode(self.project, ROOT, audit)
        second = setup_opencode(self.project, ROOT, audit)

        data = json.loads(config.read_text(encoding="utf-8"))
        plugin = self.project / ".opencode/plugins/hwskill.js"
        plugin_text = plugin.read_text(encoding="utf-8")
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(data["theme"], "system")
        self.assertIn("legacy", data["mcp"])
        self.assertEqual(data["mcp"]["hwskill"]["type"], "local")
        self.assertIn("experimental.chat.system.transform", plugin_text)
        self.assertIn("adapter", plugin_text)
        self.assertIn("opencode", plugin_text)
        self.assertIn(str(audit.resolve()), plugin_text)
        self.assertNotIn("scripts:", plugin_text)
        self.assertFalse((self.project / ".opencode/skills").exists())
        self.assertTrue(self.legacy_skill.exists())

    def test_opencode_unsetup_refuses_modified_plugin_then_removes_owned_entries(self):
        setup_opencode, unsetup_opencode = require_configuration(
            "setup_opencode", "unsetup_opencode"
        )

        setup_opencode(self.project, ROOT)
        plugin = self.project / ".opencode/plugins/hwskill.js"
        original = plugin.read_text(encoding="utf-8")
        plugin.write_text(original + "// external\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "modified outside hwskill"):
            unsetup_opencode(self.project)

        plugin.write_text(original, encoding="utf-8")
        result = unsetup_opencode(self.project)
        self.assertTrue(result.changed)
        self.assertFalse(plugin.exists())
        cleaned = json.loads(
            (self.project / "opencode.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("hwskill", cleaned.get("mcp", {}))

    def test_opencode_setup_refuses_unowned_plugin(self):
        setup_opencode, = require_configuration("setup_opencode")

        plugin = self.project / ".opencode/plugins/hwskill.js"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("export default {}\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "not owned"):
            setup_opencode(self.project, ROOT)


if __name__ == "__main__":
    unittest.main()
