from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hwskill.cli import main


class MaintenanceCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()

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
        with patch("hwskill.maintenance_cli.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(SystemExit, "--on-added.*--on-removed"):
                main(["source", "update", "--all", "--repo-root", str(self.repo), "--yes"])

    def test_registry_import_is_not_registered(self):
        with self.assertRaises(SystemExit) as raised:
            main(["registry", "import", "--source", "x"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
