from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from hwskill.cli import main


ROOT = Path(__file__).parents[1]


class IntegrityCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        for name in ("skills-src", "sources", "profiles", "registry"):
            shutil.copytree(ROOT / name, self.repo / name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_reports_every_issue_in_stable_order_without_a_traceback(self) -> None:
        """Dropping independent governance and Catalog data must expose both failures."""
        orphan = self.repo / "skills-src/l1/team/orphan/SKILL.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("---\nname: orphan\ndescription: missing governance\n---\n", encoding="utf-8")
        (self.repo / "registry/catalog.json").write_text("{}\n", encoding="utf-8")

        code, output, stderr = self.run_cli("integrity-check", "--repo-root", str(self.repo), "--json")

        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["counts"], {"issues": 2, "profiles": 3, "skills": 17, "sources": 1})
        self.assertEqual([item["code"] for item in payload["issues"]], ["catalog-stale", "orphan-skill"])
        self.assertEqual(payload["issues"], sorted(payload["issues"], key=lambda item: (item["code"], item["path"], item["message"])))
        self.assertEqual(stderr, "")
        self.assertNotIn("Traceback", output)

    def test_human_output_starts_with_summary_and_groups_paths(self) -> None:
        """A maintainer can see inventory counts before the path-local diagnostics."""
        orphan = self.repo / "skills-src/l1/team/orphan/SKILL.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("---\nname: orphan\ndescription: missing governance\n---\n", encoding="utf-8")
        (self.repo / "registry/catalog.json").write_text("{}\n", encoding="utf-8")

        code, output, stderr = self.run_cli("integrity-check", "--repo-root", str(self.repo))

        self.assertEqual(code, 1)
        self.assertTrue(output.startswith("Integrity check\n"))
        self.assertIn("Counts\n  Skills", output)
        self.assertLess(output.index("registry\n"), output.index("skills-src\n"))
        self.assertIn("catalog-stale", output)
        self.assertIn("orphan-skill", output)
        self.assertEqual(stderr, "")

    def test_clean_repository_returns_zero_and_registry_validate_stays_available(self) -> None:
        """The full gate and focused Registry validator remain separate commands."""
        code, output, stderr = self.run_cli("integrity-check", "--repo-root", str(self.repo), "--json")

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "clean")
        self.assertEqual(stderr, "")
        registry_code, registry_output, registry_stderr = self.run_cli("registry", "validate", "--repo-root", str(self.repo))
        self.assertEqual(registry_code, 0)
        self.assertTrue(registry_output.startswith("ID\tREVISION\tDIGEST\n"))
        self.assertEqual(registry_stderr, "")


if __name__ == "__main__":
    unittest.main()
