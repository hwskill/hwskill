from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hwskill.cli import main
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

    def test_profile_list_unbind_and_doctor_json(self):
        self.run_cli(
            "profile", "bind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        code, output, _ = self.run_cli(
            "profile", "list", "--project", str(self.project), "--json",
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
        code, _, _ = self.run_cli(
            "profile", "unbind", "codex-demo", "--project", str(self.project),
            "--repo-root", str(ROOT), "--yes",
        )
        self.assertEqual(resolve_profile_ids(self.project), [])


if __name__ == "__main__":
    unittest.main()
