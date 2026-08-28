import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hwskill.configuration import setup_codex
from hwskill.profiles import bind_profile


ROOT = Path(__file__).parents[1]


class InfoTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repository = self.base / "repository"
        (self.repository / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/hwskill", self.repository / "scripts/hwskill")
        venv_python = self.repository / ".venv/bin/python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        venv_python.chmod(0o755)
        (self.repository / ".venv/.hwskill-installed").touch()
        for name in ("profiles", "registry", "skills-src"):
            (self.repository / name).symlink_to(ROOT / name, target_is_directory=True)
        subprocess.run(["git", "init", "-q", str(self.repository)], check=True)
        subprocess.run(["git", "-C", str(self.repository), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(self.repository), "-c", "user.name=Test",
            "-c", "user.email=test@example.com", "commit", "-qm", "fixture",
        ], check=True)
        self.command = self.base / "bin/hwskill"
        self.command.parent.mkdir()
        self.command.symlink_to(self.repository / "scripts/hwskill")
        self.project = self.base / "demo"
        self.project.mkdir()
        bind_profile(self.project, self.repository, "codex-demo")
        setup_codex(self.project, self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def collect(self):
        try:
            from hwskill.info import collect_info
        except (ImportError, ModuleNotFoundError):
            self.fail("hwskill info collector is not implemented")
        with patch.dict(
            os.environ, {"HWSKILL_COMMAND_PATH": str(self.command)}, clear=False
        ):
            return collect_info(
                self.repository, self.project, repository_source="--repo-root"
            )

    def test_collects_installation_and_git_locations(self):
        info = self.collect()
        installation = info["installation"]

        self.assertEqual(installation["version"], "0.1.0")
        self.assertEqual(installation["repository"], str(self.repository.resolve()))
        self.assertEqual(installation["repository_source"], "--repo-root")
        self.assertEqual(
            installation["launcher"],
            str((self.repository / "scripts/hwskill").resolve()),
        )
        self.assertIsInstance(installation["command"], str)
        self.assertIsInstance(installation["python"], str)
        self.assertEqual(
            installation["venv"], str((self.repository / ".venv").resolve())
        )
        self.assertIsInstance(installation["venv_ready"], bool)
        self.assertIn(installation["status"], {"PASS", "WARN"})
        self.assertEqual(
            set(installation["git"]), {"branch", "commit", "dirty"}
        )
        self.assertIsInstance(installation["git"]["dirty"], bool)

    def test_installation_requires_command_to_target_this_repository(self):
        try:
            from hwskill.info import collect_info
        except (ImportError, ModuleNotFoundError):
            self.fail("hwskill info collector is not implemented")
        with patch.dict(
            os.environ, {"HWSKILL_COMMAND_PATH": "/bin/true"}, clear=False
        ):
            info = collect_info(
                self.repository, self.project, repository_source="--repo-root"
            )

        self.assertEqual(info["installation"]["status"], "WARN")

    def test_installation_requires_an_executable_venv_python(self):
        (self.repository / ".venv/bin/python").chmod(0o644)

        info = self.collect()

        self.assertEqual(info["installation"]["status"], "WARN")

    def test_installation_requires_an_executable_launcher(self):
        (self.repository / "scripts/hwskill").chmod(0o644)

        info = self.collect()

        self.assertEqual(info["installation"]["status"], "WARN")

    def test_git_status_does_not_refresh_the_index(self):
        tracked = self.repository / "scripts/hwskill"
        current = tracked.stat()
        os.utime(tracked, (current.st_atime + 10, current.st_mtime + 10))
        index = self.repository / ".git/index"
        before = index.stat().st_mtime_ns

        self.collect()

        self.assertEqual(index.stat().st_mtime_ns, before)

    def test_unavailable_git_state_is_unknown_and_warned(self):
        try:
            from hwskill.info import collect_info, format_info_summary
        except (ImportError, ModuleNotFoundError):
            self.fail("hwskill info collector is not implemented")
        shutil.rmtree(self.repository / ".git")

        info = self.collect()

        self.assertIsNone(info["installation"]["git"]["dirty"])
        self.assertIn("git:         unavailable", format_info_summary(info))

    def test_collects_profiles_and_all_host_integration_checks(self):
        info = self.collect()
        integration = info["integration"]

        self.assertEqual(integration["project"], str(self.project.resolve()))
        self.assertEqual(integration["profiles"], ["codex-demo"])
        self.assertEqual(
            set(integration["hosts"]), {"codex", "claude-code", "opencode"}
        )
        self.assertIn(
            "codex-config",
            {item["name"] for item in integration["hosts"]["codex"]["checks"]},
        )
        self.assertEqual(
            next(
                item["status"]
                for item in integration["hosts"]["codex"]["checks"]
                if item["name"] == "codex-config"
            ),
            "PASS",
        )
        for host in integration["hosts"].values():
            self.assertIn(host["status"], {"PASS", "WARN", "ERROR"})
        self.assertEqual(
            integration["hosts"]["codex"].get("integration_status"), "installed"
        )
        self.assertEqual(
            integration["hosts"]["claude-code"].get("integration_status"),
            "not installed",
        )
        self.assertEqual(
            integration["hosts"]["opencode"].get("integration_status"),
            "not installed",
        )

        json.dumps(info)

    def test_partially_configured_host_is_incomplete(self):
        config = self.project / ".codex/config.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "[[hooks.SessionStart]]", "[[hooks.Disabled]]"
            ),
            encoding="utf-8",
        )

        codex = self.collect()["integration"]["hosts"]["codex"]

        self.assertEqual(codex.get("integration_status"), "incomplete")

    def test_registry_is_validated_once_for_all_hosts(self):
        from hwskill import doctor

        with patch(
            "hwskill.doctor.validate_registry", wraps=doctor.validate_registry
        ) as validate_registry:
            self.collect()

        self.assertEqual(validate_registry.call_count, 1)

    def test_malformed_profile_is_reported_as_an_error(self):
        from hwskill.info import format_info_summary

        (self.project / ".hwskills/profile.yaml").write_text(
            "profiles: [\n", encoding="utf-8"
        )

        integration = self.collect()["integration"]

        self.assertEqual(integration["profiles"], [])
        self.assertEqual(integration.get("profile_status"), "ERROR")
        self.assertIn("while parsing", integration.get("profile_error", ""))
        self.assertIn("profiles:    error", format_info_summary(self.collect()))

    def test_default_summary_is_human_readable(self):
        try:
            from hwskill.info import format_info_summary
        except (ImportError, ModuleNotFoundError):
            self.fail("hwskill info summary formatter is not implemented")
        info = self.collect()
        info["installation"]["git"] = {
            "branch": "main", "commit": "abc1234", "dirty": False,
        }

        output = format_info_summary(info)

        self.assertEqual(
            output,
            f"hwskill v0.1.0 installed.\n"
            f"\n"
            f"repository:  {self.repository.resolve()}\n"
            f"executable:  {self.command}\n"
            f"venv:        {self.repository.resolve()}/.venv\n"
            f"python:      {info['installation']['python']}\n"
            f"git:         main@abc1234 clean\n"
            f"\n"
            f"Integrations:\n"
            f"project:     {self.project.resolve()}\n"
            f"profiles:    codex-demo\n"
            f"codex        -- installed\n"
            f"claude-code  -- not installed\n"
            f"opencode     -- not installed\n",
        )


if __name__ == "__main__":
    unittest.main()
