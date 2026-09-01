from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import unittest

from hwskill.cli import main


ROOT = Path(__file__).parents[2]


class CliHelpTest(unittest.TestCase):
    def render_help(self, *args: str) -> str:
        stdout = StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as exit_context:
            main([*args, "--help"])
        self.assertEqual(exit_context.exception.code, 0)
        return stdout.getvalue()

    def test_command_groups_use_required_marker_and_describe_each_choice(self):
        cases = {
            (): {
                "info": "Show installation and integration status",
                "integrity-check": "Validate repository source integrity",
                "test": "Run declarative core, Skill, and Profile tests",
                "registry": "Manage the skill registry",
                "source": "Manage upstream skill sources",
                "profile": "Manage user and project profiles",
                "skill": "Search and load effective skills",
                "setup": "Configure a host integration",
                "unsetup": "Remove a host integration",
                "doctor": "Check a host integration",
                "adapter": "Run host adapter commands",
                "serve-mcp": "Start the hwskill MCP server",
            },
            ("registry",): {
                "validate": "Validate registry contents",
                "build": "Build the registry catalog",
            },
            ("profile",): {
                "bind": "Bind a profile to a project",
                "unbind": "Unbind a profile from a project",
                "list": "List available profile definitions",
                "set": "Set profiles for a user or project",
                "show": "Show explicit and effective profiles",
                "unset": "Remove an explicit profile setting",
                "resolve": "Resolve the effective skill catalog",
            },
            ("skill",): {
                "list": "List all registered skills",
                "dump": "Export selected skills",
                "dump-profile": "Export skills from profiles",
                "search": "Search the effective skill catalog",
                "load": "Load a skill from the effective catalog",
                "create": "Create a manual Skill",
                "manualize": "Convert an upstream Skill to manual",
            },
            ("source",): {
                "add": "Add an upstream source",
                "check": "Check upstream revisions and local source drift",
                "update": "Update upstream snapshots (interactive per-Skill decisions)",
                "adopt": "Adopt an upstream Skill",
            },
            ("adapter",): {
                "codex": "Run Codex adapter commands",
                "claude-code": "Run Claude Code adapter commands",
                "claude_code": "Alias for claude-code adapter commands",
                "opencode": "Run OpenCode adapter commands",
            },
            ("adapter", "codex"): {
                "session-start": "Render Codex session-start context",
            },
            ("adapter", "claude-code"): {
                "session-start": "Render Claude Code session-start context",
            },
            ("adapter", "claude_code"): {
                "session-start": "Render Claude Code session-start context",
            },
            ("adapter", "opencode"): {
                "catalog": "Render the OpenCode skill catalog",
            },
        }

        for command_path, expected_commands in cases.items():
            with self.subTest(command_path=command_path):
                output = self.render_help(*command_path)
                self.assertIn("positional arguments:\n  <COMMAND>", output)
                self.assertNotIn("commands:\n  COMMAND", output)
                for command, description in expected_commands.items():
                    self.assertRegex(
                        output,
                        rf"(?m)^    {re.escape(command)}\s+{re.escape(description)}$",
                    )

    def test_host_positionals_are_named_and_list_all_allowed_values(self):
        choices = "One of: codex, claude-code, claude_code, opencode"
        for command in ("setup", "unsetup", "doctor"):
            with self.subTest(command=command):
                output = self.render_help(command)
                self.assertIn("<HOST>", output)
                self.assertIn(choices, output)

    def test_free_form_positionals_use_required_markers_and_examples(self):
        cases = {
            ("profile", "bind"): ("<PROFILE_ID>", "e.g. codex-demo"),
            ("profile", "unbind"): ("<PROFILE_ID>", "e.g. codex-demo"),
            ("profile", "set"): (
                "<PROFILE_NAME,PROFILE_NAME,...>",
                "e.g. personal-baseline,superpowers",
            ),
            ("skill", "search"): ("<QUERY>", 'e.g. "debug failing test"'),
            ("skill", "load"): (
                "<SKILL_ID>",
                "e.g. local/chinese-thinking",
            ),
            ("skill", "dump"): (
                "<SKILL_ID,SKILL_NAME,...>",
                "e.g. chinese-thinking,brainstorming",
            ),
        }

        for command_path, (metavar, example) in cases.items():
            with self.subTest(command_path=command_path):
                output = self.render_help(*command_path)
                self.assertIn(metavar, output)
                self.assertIn(example, re.sub(r"\s+", " ", output))

    def test_scoped_profile_help_uses_optional_project_value(self):
        for command in ("set", "show", "unset"):
            with self.subTest(command=command):
                output = self.render_help("profile", command)
                self.assertIn("--user", output)
                self.assertIn("--project [PROJECT]", output)

    def test_readme_distinguishes_registry_integrity_and_behavior_checks(self):
        """Maintainers need separate commands for focused, offline, and behavioral gates."""
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("hwskill registry validate", text)
        self.assertIn("hwskill registry build --repo-root . --check", text)
        self.assertIn("hwskill integrity-check", text)
        self.assertIn("hwskill test affected", text)
        self.assertIn("setsid", text)
        self.assertIn("标准 Docker", text)
        specification = (ROOT / "docs/superpowers/specs/2026-09-01-skill-source-maintenance-and-testing-design.md").read_text(encoding="utf-8")
        self.assertIn("HWSKILL_TEST_EVIDENCE_WORKSPACE", specification)
        self.assertIn("setsid", specification)


if __name__ == "__main__":
    unittest.main()
