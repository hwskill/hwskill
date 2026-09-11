from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class DirectoryWheelTests(unittest.TestCase):
    def test_installed_wheel_reads_packaged_schema(self) -> None:
        """The validator must not depend on a checkout-relative schemas directory."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            target = root / "installed"
            wheels.mkdir()
            build_environment = dict(os.environ)
            build_environment.pop("PYTHONPATH", None)
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", ".", "--wheel-dir", str(wheels)],
                check=True, capture_output=True, text=True, env=build_environment,
            )
            wheel = next(wheels.glob("hwskill-*.whl"))
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
                check=True, capture_output=True, text=True, env=build_environment,
            )
            environment = {**os.environ, "PYTHONPATH": f"{target}:/usr/lib/python3/dist-packages"}
            completed = subprocess.run(
                [sys.executable, "-c", "from hwskill.directory.schema import validator_for; validator_for('entry'); print('ok')"],
                check=True, capture_output=True, text=True, env=environment,
            )
            self.assertEqual(completed.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
