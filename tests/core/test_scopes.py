import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hwskill.hosts import HOST_SPECS, canonical_host
from hwskill.scopes import lock_path, profile_path, project_scope, setup_state_path, user_scope


class ScopeTargetTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_user_scope_honors_xdg_roots(self):
        with patch.dict(
            os.environ,
            {
                "HOME": "/home/demo",
                "XDG_CONFIG_HOME": "/cfg",
                "XDG_STATE_HOME": "/state",
            },
            clear=True,
        ):
            target = user_scope()

        self.assertEqual(profile_path(target), Path("/cfg/hwskill/profile.yaml"))
        self.assertEqual(lock_path(target), Path("/cfg/hwskill/lock.yaml"))
        self.assertEqual(
            setup_state_path(target, "claude_code"),
            Path("/state/hwskill/setup-claude-code.json"),
        )

    def test_user_scope_uses_home_defaults_without_xdg_overrides(self):
        with patch.dict(os.environ, {"HOME": "/home/demo"}, clear=True):
            target = user_scope()

        self.assertEqual(target.config_root, Path("/home/demo/.config/hwskill"))
        self.assertEqual(target.state_root, Path("/home/demo/.local/state/hwskill"))

    def test_bare_project_uses_git_root(self):
        project = self.base / "repo"
        (project / ".git").mkdir(parents=True)
        nested = project / "a/b"
        nested.mkdir(parents=True)

        target = project_scope(None, nested)

        self.assertEqual(target.project_root, project.resolve())
        self.assertEqual(target.config_root, project.resolve() / ".hwskills")
        self.assertEqual(target.state_root, project.resolve() / ".hwskills/state")

    def test_explicit_project_path_is_resolved(self):
        project = self.base / "repo"
        project.mkdir()

        target = project_scope(project)

        self.assertEqual(target.project_root, project.resolve())

    def test_host_alias_normalizes_without_changing_executable(self):
        self.assertEqual(canonical_host("claude_code"), "claude-code")
        self.assertEqual(HOST_SPECS["claude-code"].executable, "claude")
        self.assertEqual(HOST_SPECS["claude-code"].verified_version, "2.1.141")
        self.assertEqual(HOST_SPECS["codex"].verified_version, "0.147.0")
        self.assertEqual(HOST_SPECS["opencode"].verified_version, "1.14.48")

    def test_unknown_host_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported host"):
            canonical_host("unknown")


if __name__ == "__main__":
    unittest.main()
