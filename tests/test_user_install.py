import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).parents[1]


class UserInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fake_python(self):
        path = self.base / "fake-python"
        path.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if [ \"${1-}\" = -m ] && [ \"${2-}\" = venv ]; then\n"
            "  mkdir -p \"$3/bin\"\n"
            "  cp \"$0\" \"$3/bin/python\"\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1-}\" = -m ] && [ \"${2-}\" = pip ]; then exit 0; fi\n"
            "printf '%s\\n' \"$*\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def local_checkout(self):
        checkout = self.base / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        package = checkout / "src/hwskill"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            'print("source fallback")\n', encoding="utf-8"
        )
        for relative in ("install.sh", "scripts/hwskill"):
            source = ROOT / relative
            if not source.is_file():
                self.fail(f"local installer artifact is missing: {relative}")
            target = checkout / relative
            shutil.copy2(source, target)
        (checkout / "pyproject.toml").write_text(
            "[project]\nname = \"hwskill\"\nversion = \"0.1.0\"\n",
            encoding="utf-8",
        )
        return checkout

    def test_launcher_ignores_an_incomplete_private_venv(self):
        checkout = self.local_checkout()
        incomplete = checkout / ".venv/bin/python"
        incomplete.parent.mkdir(parents=True)
        incomplete.write_text(
            "#!/bin/sh\nprintf 'incomplete venv\\n'\n", encoding="utf-8"
        )
        incomplete.chmod(0o755)

        completed = subprocess.run(
            [str(checkout / "scripts/hwskill"), "--version"],
            env=os.environ | {"HWSKILL_HOME": ""},
            text=True, capture_output=True, check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "source fallback")

    def test_local_installer_binds_current_checkout_without_requiring_venv_activation(self):
        checkout = self.local_checkout()
        bin_dir = self.base / "bin"
        shell_config = self.base / "bashrc"
        shell_config.write_text("export EXISTING=1\n", encoding="utf-8")
        environment = os.environ | {
            "HOME": str(self.base / "home"),
            "HWSKILL_BIN_DIR": str(bin_dir),
            "HWSKILL_SHELL_CONFIG": str(shell_config),
            "PYTHON": str(self.fake_python()),
        }

        configured_versions = []
        for _ in range(2):
            completed = subprocess.run(
                [str(checkout / "install.sh")],
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            configured_versions.append(shell_config.read_text(encoding="utf-8"))

        command = bin_dir / "hwskill"
        self.assertTrue(command.is_symlink())
        self.assertEqual(command.resolve(), checkout / "scripts/hwskill")
        self.assertTrue((checkout / ".venv/bin/python").is_file())
        configured = shell_config.read_text(encoding="utf-8")
        self.assertEqual(configured_versions[0], configured_versions[1])
        self.assertEqual(configured.count("# >>> hwskill >>>"), 1)
        self.assertIn(f"export HWSKILL_HOME='{checkout}'", configured)
        self.assertIn(str(bin_dir), configured)

        invoked = subprocess.run(
            [str(command), "--version"], env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(invoked.returncode, 0, invoked.stderr)
        self.assertEqual(invoked.stdout.strip(), "-m hwskill --version")

    def test_curl_bootstrap_clones_then_runs_checkout_installer(self):
        source = self.base / "remote"
        shutil.copytree(self.local_checkout(), source)
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(source), "-c", "user.name=Test",
            "-c", "user.email=test@example.com", "commit", "-qm", "fixture",
        ], check=True)
        install_home = self.base / "installed"
        bin_dir = self.base / "bootstrap-bin"
        shell_config = self.base / "bootstrap-rc"
        installer = ROOT / "install.sh"
        if not installer.is_file():
            self.fail("curl bootstrap installer is missing")

        completed = subprocess.run(
            ["sh"], input=installer.read_text(encoding="utf-8"),
            cwd=self.base,
            env=os.environ | {
                "HWSKILL_HOME": str(install_home),
                "HWSKILL_REPOSITORY_URL": str(source),
                "HWSKILL_BIN_DIR": str(bin_dir),
                "HWSKILL_SHELL_CONFIG": str(shell_config),
                "PYTHON": str(self.fake_python()),
            },
            text=True, capture_output=True, check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((install_home / ".git").is_dir())
        self.assertEqual((bin_dir / "hwskill").resolve(), install_home / "scripts/hwskill")
        self.assertIn(
            f"export HWSKILL_HOME='{install_home}'",
            shell_config.read_text(encoding="utf-8"),
        )

    def test_installer_refuses_malformed_managed_shell_block_without_data_loss(self):
        checkout = self.local_checkout()
        shell_config = self.base / "bashrc"
        original = "export BEFORE=1\n# >>> hwskill >>>\nexport AFTER=1\n"
        shell_config.write_text(original, encoding="utf-8")

        completed = subprocess.run(
            [str(checkout / "install.sh")],
            env=os.environ | {
                "HOME": str(self.base / "home"),
                "HWSKILL_BIN_DIR": str(self.base / "bin"),
                "HWSKILL_SHELL_CONFIG": str(shell_config),
                "PYTHON": str(self.fake_python()),
            },
            text=True, capture_output=True, check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("managed markers", completed.stderr)
        self.assertEqual(shell_config.read_text(encoding="utf-8"), original)

    def test_installer_preserves_symlinked_shell_configuration(self):
        checkout = self.local_checkout()
        dotfiles = self.base / "dotfiles/bashrc"
        dotfiles.parent.mkdir()
        dotfiles.write_text("export EXISTING=1\n", encoding="utf-8")
        shell_config = self.base / "home/.bashrc"
        shell_config.parent.mkdir()
        shell_config.symlink_to(dotfiles)

        completed = subprocess.run(
            [str(checkout / "install.sh")],
            env=os.environ | {
                "HOME": str(self.base / "home"),
                "HWSKILL_BIN_DIR": str(self.base / "bin"),
                "HWSKILL_SHELL_CONFIG": str(shell_config),
                "PYTHON": str(self.fake_python()),
            },
            text=True, capture_output=True, check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(shell_config.is_symlink())
        configured = dotfiles.read_text(encoding="utf-8")
        self.assertIn("export EXISTING=1", configured)
        self.assertEqual(configured.count("# >>> hwskill >>>"), 1)


if __name__ == "__main__":
    unittest.main()
