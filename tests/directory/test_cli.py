from __future__ import annotations

import json
import io
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
import unittest


FIXTURES = Path(__file__).parent / "fixtures"


class DirectoryCliTests(unittest.TestCase):
    def repository(self, fixture: str) -> Path:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / fixture, root)
        return root

    def test_validate_json_returns_zero_and_complete_report(self) -> None:
        from hwskill.directory.cli import main

        root = self.repository("valid")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(["validate", "--repo-root", str(root), "--json"]), 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["issues"], [])
        self.assertIn("input_digest", report)

    def test_validate_invalid_input_returns_one_with_issue_fields(self) -> None:
        from hwskill.directory.cli import main

        root = self.repository("unknown-field")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(["validate", "--repo-root", str(root), "--json"]), 1)
        issue = json.loads(stdout.getvalue())["issues"][0]
        self.assertEqual(set(issue), {"file", "field", "code", "severity", "message", "suggested_action"})

    def test_environment_error_returns_three(self) -> None:
        from hwskill.directory.cli import main

        self.assertEqual(main(["validate", "--repo-root", "/definitely/not/a/repository", "--json"]), 3)

    def test_build_unsafe_output_is_input_error_with_structured_issue(self) -> None:
        from hwskill.directory.cli import main

        root = self.repository("valid")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(["build", "--repo-root", str(root), "--out", str(root), "--json"]), 1)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["issues"][0]["code"], "unsafe-output-path")


if __name__ == "__main__":
    unittest.main()
