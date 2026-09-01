from contextlib import redirect_stdout
from io import StringIO
import unittest

from hwskill.cli import main


class CliSmokeTest(unittest.TestCase):
    def test_version_is_printed(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertRegex(output.getvalue(), r"^hwskill 0\.1\.0\n$")


if __name__ == "__main__":
    unittest.main()
