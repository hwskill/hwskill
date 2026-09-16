from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/verify_release.sh"
EXPECTED_STEPS = {
    "source-snapshot",
    "test-input",
    "python-tests",
    "directory-validate",
    "directory-build",
    "site-build",
    "site-index",
    "local-http-read",
    "publishing-recovery",
    "sharing-prepare-ack",
}


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _fake_tool(path: Path, log: Path, *, fail_pattern: str = "") -> None:
    path.write_text(
        """#!/bin/sh
set -eu
printf '%s|HOME=%s|PYTHONPATH=%s|PWD=%s\\n' "$*" "${HOME:-}" "${PYTHONPATH:-}" "$PWD" >> "$VERIFY_TOOL_LOG"
case "$*" in
  *"$VERIFY_FAIL_PATTERN"*) [ -z "$VERIFY_FAIL_PATTERN" ] || exit 19 ;;
esac
case "$*" in
  *"--prefix site run build"*)
    mkdir -p site/dist/data
    : > site/dist/index.html
    : > site/dist/data/catalog.json
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    log.write_text("", encoding="utf-8")


@unittest.skipIf(os.environ.get("HWSKILL_RELEASE_VERIFICATION_ACTIVE") == "1", "outer release verification owns this integration test")
class VerifyReleaseScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "sentinel").write_text("unchanged", encoding="utf-8")
        (self.repository / "site/node_modules").mkdir(parents=True)
        subprocess.run(["/usr/bin/git", "init", "-q", str(self.repository)], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(self.repository), "add", "."], check=True)
        subprocess.run(
            [
                "/usr/bin/git", "-C", str(self.repository),
                "-c", "user.name=Test", "-c", "user.email=test@example.test",
                "commit", "-q", "-m", "fixture",
            ],
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "-C", str(self.repository), "tag", "hwskill-legacy-v0.1.0"],
            check=True,
        )
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "tools.log"
        _fake_tool(self.bin / "python3", self.log)
        (self.bin / "npm").symlink_to(self.bin / "python3")
        (self.bin / "node").symlink_to(self.bin / "python3")

    def run_script(self, *, fail_pattern: str = "", scale: bool = False) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
        report = self.root / "report.jsonl"
        before = _tree_digest(self.repository)
        arguments = [str(SCRIPT), "--repo-root", str(self.repository), "--report", str(report)]
        if scale:
            arguments.append("--scale")
        completed = subprocess.run(
            arguments,
            cwd=self.root,
            env={
                **os.environ,
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "PYTHON": str(self.bin / "python3"),
                "VERIFY_TOOL_LOG": str(self.log),
                "VERIFY_FAIL_PATTERN": fail_pattern,
            },
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(_tree_digest(self.repository), before)
        records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        return completed, records

    def test_runs_required_steps_in_isolated_copy_and_writes_structured_report(self) -> None:
        completed, records = self.run_script()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        steps = {record["step"] for record in records if record.get("kind") == "step"}
        self.assertEqual(steps, EXPECTED_STEPS)
        self.assertEqual(records[-1]["kind"], "summary")
        self.assertEqual(records[-1]["result"], "pass")
        for record in records[:-1]:
            self.assertRegex(str(record["log_sha256"]), r"^sha256:[0-9a-f]{64}$")
            self.assertIn("log_tail_base64", record)
        invocations = self.log.read_text(encoding="utf-8")
        self.assertNotIn(f"HOME={os.environ.get('HOME', '')}|", invocations)
        self.assertNotIn(f"PWD={self.repository}", invocations)

    def test_step_failure_is_recorded_continues_and_returns_nonzero(self) -> None:
        completed, records = self.run_script(fail_pattern="tests.publishing.test_recovery")

        self.assertNotEqual(completed.returncode, 0)
        by_step = {record["step"]: record for record in records if record.get("kind") == "step"}
        self.assertEqual(by_step["publishing-recovery"]["result"], "fail")
        self.assertIn("sharing-prepare-ack", by_step)
        self.assertEqual(records[-1]["result"], "fail")

    def test_missing_supported_node_is_recorded_as_blocked_not_pass(self) -> None:
        (self.bin / "node").unlink()
        completed, records = self.run_script()

        self.assertEqual(completed.returncode, 2)
        by_step = {record["step"]: record for record in records if record.get("kind") == "step"}
        self.assertEqual(by_step["site-build"]["result"], "blocked")
        self.assertEqual(by_step["site-index"]["result"], "blocked")
        self.assertEqual(by_step["local-http-read"]["result"], "blocked")
        self.assertEqual(records[-1]["result"], "blocked")
        self.assertEqual(records[-1]["failed_steps"], 0)

    def test_scale_mode_records_both_requested_sizes_without_touching_source(self) -> None:
        completed, records = self.run_script(scale=True)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        steps = {record["step"] for record in records if record.get("kind") == "step"}
        self.assertTrue({"scale-1000", "scale-10000"}.issubset(steps))
        metrics = {record["size"] for record in records if record.get("kind") == "scale"}
        self.assertEqual(metrics, {1000, 10000})

    def test_refuses_to_overwrite_an_existing_report(self) -> None:
        report = self.root / "report.jsonl"
        report.write_text("keep\n", encoding="utf-8")
        completed = subprocess.run(
            [str(SCRIPT), "--repo-root", str(self.repository), "--report", str(report)],
            env={**os.environ, "PATH": f"{self.bin}:/usr/bin:/bin"},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(report.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
