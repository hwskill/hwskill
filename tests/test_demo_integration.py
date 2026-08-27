from pathlib import Path
import subprocess
import sys
import unittest


DEMO = Path(__file__).parents[1] / "examples/codex-demo"


class DemoIntegrationTest(unittest.TestCase):
    def test_demo_starts_with_one_boundary_failure(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=DEMO,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test_discount_threshold_is_inclusive", result.stderr)
        self.assertEqual(result.stderr.count(" ... FAIL"), 1)

    def test_demo_has_no_native_skills(self):
        self.assertFalse((DEMO / ".agents/skills").exists())


if __name__ == "__main__":
    unittest.main()
