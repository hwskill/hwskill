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

    def test_local_installer_binds_current_checkout_without_requiring_venv_activation(self):
        checkout = self.local_checkout()
        bin_dir = self.base / "bin"
        shell_config = self.base / "bashrc"
        environment = os.environ | {
            "HOME": str(self.base / "home"),
            "HWSKILL_BIN_DIR": str(bin_dir),
            "HWSKILL_SHELL_CONFIG": str(shell_config),
            "PYTHON": str(self.fake_python()),
        }

        for _ in range(2):
            completed = subprocess.run(
                [str(checkout / "install.sh")],
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

        command = bin_dir / "hwskill"
        self.assertTrue(command.is_symlink())
        self.assertEqual(command.resolve(), checkout / "scripts/hwskill")
        self.assertTrue((checkout / ".venv/bin/python").is_file())
        configured = shell_config.read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
