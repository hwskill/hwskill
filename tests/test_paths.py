import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class RepoRootResolutionTest(unittest.TestCase):
    def resolve(self, explicit=None):
        try:
            from hwskill.paths import resolve_repo_root
        except ModuleNotFoundError:
            self.fail("repo-root resolver is not implemented")
        return resolve_repo_root(explicit)

    def test_explicit_parameter_overrides_environment_and_script_root(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.dict(os.environ, {
                "HWSKILL_HOME": str(base / "environment"),
                "HWSKILL_SCRIPT_ROOT": str(base / "script"),
            }, clear=False):
                result = self.resolve(str(base / "explicit"))

        self.assertEqual(result, (base / "explicit").resolve())

    def test_environment_overrides_script_root(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.dict(os.environ, {
                "HWSKILL_HOME": str(base / "environment"),
                "HWSKILL_SCRIPT_ROOT": str(base / "script"),
            }, clear=False):
                result = self.resolve()

        self.assertEqual(result, (base / "environment").resolve())

    def test_script_root_is_final_fallback(self):
        with TemporaryDirectory() as directory:
            script_root = Path(directory) / "checkout"
            with patch.dict(os.environ, {
                "HWSKILL_SCRIPT_ROOT": str(script_root),
            }, clear=True):
                result = self.resolve()

        self.assertEqual(result, script_root.resolve())

    def test_missing_repo_root_is_an_explicit_error_not_current_directory(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "--repo-root.*HWSKILL_HOME"):
                self.resolve()


if __name__ == "__main__":
    unittest.main()
