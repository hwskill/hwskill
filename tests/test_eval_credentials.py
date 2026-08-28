import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).parents[1]
FILTER = ROOT / "scripts/filter_minimax_auth.sh"


class EvalCredentialsTest(unittest.TestCase):
    def test_filter_keeps_only_minimax_provider_and_locks_permissions(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "auth.json"
            target = Path(directory) / "filtered.json"
            source.write_text(json.dumps({
                "minimax-cn-coding-plan": {"type": "api", "key": "minimax-secret"},
                "unrelated": {"type": "api", "key": "must-not-be-copied"},
            }), encoding="utf-8")

            completed = subprocess.run(
                [str(FILTER), str(source), str(target)], text=True,
                capture_output=True, check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(list(json.loads(target.read_text(encoding="utf-8"))), ["minimax-cn-coding-plan"])
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)

    def test_filter_reports_missing_or_malformed_minimax_credential(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "auth.json"
            target = Path(directory) / "filtered.json"
            source.write_text('{"unrelated": {"key": "secret"}}\n', encoding="utf-8")

            completed = subprocess.run(
                [str(FILTER), str(source), str(target)], text=True,
                capture_output=True, check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("MiniMax credential missing or malformed", completed.stderr)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
