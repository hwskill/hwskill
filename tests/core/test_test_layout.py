from pathlib import Path
import unittest


class TestLayoutTest(unittest.TestCase):
    def test_framework_tests_live_only_under_core(self):
        root = Path(__file__).parents[2]
        self.assertFalse(list((root / "tests").glob("test_*.py")))
        self.assertTrue(list((root / "tests/core").glob("test_*.py")))
