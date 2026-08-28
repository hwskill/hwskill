import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


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

    def test_claude_setup_refuses_unowned_hwskill_mcp(self):
        setup_claude_code, = require_configuration("setup_claude_code")

        (self.project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"hwskill": {"command": "someone-else"}}}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "not owned"):
            setup_claude_code(self.project, ROOT)

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
