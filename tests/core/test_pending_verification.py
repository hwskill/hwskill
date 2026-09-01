from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import unittest

from hwskill.pending_verification import (
    VerificationResult,
    clear_pending_verification,
    load_pending_verification,
    write_pending_verification,
)
from hwskill.test_impact import TestSelection


class PendingVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "test"], check=True)
        (self.repo / "registry").mkdir()
        self._write_current_digest("sha256:abc")
        self.selection = TestSelection(
            skill_ids=("team/review",), profile_ids=("review-workflow",),
            collection_paths=(Path("tests/skills/team/review/test.yaml"), Path("tests/profiles/review-workflow/test.yaml")),
            changed_paths=("skills-src/l1/team/review/SKILL.md",),
        )
        self.digests = {"team/review": "sha256:abc"}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_current_digest(self, digest: str) -> None:
        (self.repo / "registry/catalog.json").write_text(
            '{"skills":[{"id":"team/review","path":"skills-src/l1/team/review","content_digest":"' + digest + '"}]}',
            encoding="utf-8",
        )

    def test_writes_gitdir_local_mode_600_stable_state(self) -> None:
        path = write_pending_verification(self.repo, self.selection, self.digests)
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(self.repo / ".git"))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        record = load_pending_verification(self.repo)
        self.assertEqual(record.digests, (("team/review", "sha256:abc"),))
        self.assertEqual(record.selection, self.selection)

    def test_only_exact_digests_and_all_required_passes_clear(self) -> None:
        write_pending_verification(self.repo, self.selection, self.digests)
        incomplete = VerificationResult(self.selection, self.digests, (("skill:team/review", "PASS"),))
        self.assertFalse(clear_pending_verification(self.repo, incomplete))
        stale = VerificationResult(self.selection, {"team/review": "sha256:def"}, (("skill:team/review", "PASS"), ("profile:review-workflow", "PASS")))
        self.assertFalse(clear_pending_verification(self.repo, stale))
        passed = VerificationResult(self.selection, self.digests, (("skill:team/review", "PASS"), ("profile:review-workflow", "PASS")))
        self._write_current_digest("sha256:def")
        self.assertFalse(clear_pending_verification(self.repo, passed))
        self._write_current_digest("sha256:abc")
        self.assertTrue(clear_pending_verification(self.repo, passed))
        self.assertIsNone(load_pending_verification(self.repo))

    def test_missing_or_corrupt_state_fails_closed(self) -> None:
        passed = VerificationResult(self.selection, self.digests, (("skill:team/review", "PASS"), ("profile:review-workflow", "PASS")))
        self.assertFalse(clear_pending_verification(self.repo, passed))
        path = write_pending_verification(self.repo, self.selection, self.digests)
        path.write_text("not json", encoding="utf-8")
        self.assertFalse(clear_pending_verification(self.repo, passed))
