from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import unittest
from unittest.mock import patch

import hwskill.pending_verification as pending

from hwskill.pending_verification import (
    VerificationResult,
    clear_pending_verification,
    load_pending_verification,
    prepare_pending_verification,
    write_pending_verification,
)
from hwskill.test_impact import TestSelection


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


class PendingVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "test"], check=True)
        (self.repo / "registry").mkdir()
        self._write_current_digest(_DIGEST_A)
        self.selection = TestSelection(
            skill_ids=("team/review",), profile_ids=("review-workflow",),
            collection_paths=(Path("tests/skills/team/review/test.yaml"), Path("tests/profiles/review-workflow/test.yaml")),
            changed_paths=("skills-src/l1/team/review/SKILL.md",),
        )
        self.digests = {"team/review": _DIGEST_A}

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
        self.assertEqual(record.digests, (("team/review", _DIGEST_A),))
        self.assertEqual(record.selection, self.selection)

    def test_only_exact_digests_and_all_required_passes_clear(self) -> None:
        write_pending_verification(self.repo, self.selection, self.digests)
        incomplete = VerificationResult(self.selection, self.digests, (("skill:team/review", "PASS"),))
        self.assertFalse(clear_pending_verification(self.repo, incomplete))
        stale = VerificationResult(self.selection, {"team/review": _DIGEST_B}, (("skill:team/review", "PASS"), ("profile:review-workflow", "PASS")))
        self.assertFalse(clear_pending_verification(self.repo, stale))
        passed = VerificationResult(self.selection, self.digests, (("skill:team/review", "PASS"), ("profile:review-workflow", "PASS")))
        self._write_current_digest(_DIGEST_B)
        self.assertFalse(clear_pending_verification(self.repo, passed))
        self._write_current_digest(_DIGEST_A)
        self.assertTrue(clear_pending_verification(self.repo, passed))
        self.assertIsNone(load_pending_verification(self.repo))

    def test_missing_or_corrupt_state_fails_closed(self) -> None:
        passed = VerificationResult(self.selection, self.digests, (("skill:team/review", "PASS"), ("profile:review-workflow", "PASS")))
        self.assertFalse(clear_pending_verification(self.repo, passed))
        path = write_pending_verification(self.repo, self.selection, self.digests)
        path.write_text("not json", encoding="utf-8")
        self.assertFalse(clear_pending_verification(self.repo, passed))

    def test_core_pending_state_requires_an_exact_core_pass_to_clear(self) -> None:
        selection = TestSelection(core=True)
        path = write_pending_verification(self.repo, selection, {})
        for statuses in ((), (("core", "FAIL"),)):
            with self.subTest(statuses=statuses):
                self.assertFalse(clear_pending_verification(
                    self.repo, VerificationResult(selection, {}, statuses),
                ))
                self.assertTrue(path.exists())
        self.assertTrue(clear_pending_verification(
            self.repo, VerificationResult(selection, {}, (("core", "PASS"),)),
        ))
        self.assertFalse(path.exists())

    def test_parseable_schema_invalid_and_symlink_file_fail_closed(self) -> None:
        path = write_pending_verification(self.repo, self.selection, self.digests)
        path.write_text('{"schema_version":99}', encoding="utf-8")
        self.assertIsNone(load_pending_verification(self.repo))
        path.unlink()
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("outside", encoding="utf-8")
        path.symlink_to(outside)
        self.assertIsNone(load_pending_verification(self.repo))
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_pending_schema_persists_only_expected_data(self) -> None:
        path = write_pending_verification(self.repo, self.selection, self.digests)
        data = path.read_text(encoding="utf-8")
        self.assertNotIn("prompt", data.lower())
        self.assertNotIn("output", data.lower())
        self.assertNotIn("secret", data.lower())
        self.assertEqual(set(__import__("json").loads(data)), {"schema_version", "changed_paths", "digests", "selection"})

    def test_publish_stage_failures_restore_existing_record(self) -> None:
        original = write_pending_verification(self.repo, self.selection, self.digests).read_bytes()
        original_replace, original_chmod, original_fsync = pending.os.replace, pending.os.chmod, pending._fsync_fd
        failures = (
            ("replace", "os.replace", original_replace),
            ("chmod", "os.chmod", original_chmod),
            ("fsync", "_fsync_fd", original_fsync),
        )
        for label, attribute, original_function in failures:
            with self.subTest(label=label):
                calls = {"count": 0}
                def fail_once(*args, **kwargs):
                    calls["count"] += 1
                    if calls["count"] == 1:
                        raise OSError(label)
                    return original_function(*args, **kwargs)
                target = "hwskill.pending_verification." + attribute
                with patch(target, side_effect=fail_once):
                    with self.assertRaises(pending.PendingVerificationError):
                        write_pending_verification(self.repo, self.selection, {"team/review": _DIGEST_B})
                path = self.repo / ".git/hwskill/pending-verification.json"
                self.assertEqual(path.read_bytes(), original)

    def test_writer_rejects_secret_bearing_public_inputs_without_persisting(self) -> None:
        sentinel = "PROMPT-OUTPUT-CREDENTIAL-SECRET"
        initial = TestSelection(changed_paths=(f"sources/{sentinel}.yaml",))
        with self.assertRaises(pending.PendingVerificationError):
            write_pending_verification(self.repo, initial, {})
        self.assertFalse((self.repo / ".git/hwskill").exists())

        path = write_pending_verification(self.repo, self.selection, self.digests)
        original = path.read_bytes()
        invalid_inputs = (
            (TestSelection(skill_ids=(sentinel,)), self.digests),
            (TestSelection(profile_ids=(sentinel,)), self.digests),
            (TestSelection(reasons=(sentinel,)), self.digests),
            (TestSelection(digests=(("team/review", sentinel),)), self.digests),
            (self.selection, {"team/review": sentinel}),
        )
        for selection, digests in invalid_inputs:
            with self.subTest(selection=selection, digests=digests):
                with self.assertRaises(pending.PendingVerificationError):
                    write_pending_verification(self.repo, selection, digests)
                self.assertEqual(path.read_bytes(), original)
                self.assertNotIn(sentinel.encode("utf-8"), path.read_bytes())

    def test_existing_hwskill_symlink_is_rejected_without_external_write(self) -> None:
        gitdir = self.repo / ".git"
        target = Path(self.temporary.name) / "outside"
        target.mkdir()
        (gitdir / "hwskill").symlink_to(target, target_is_directory=True)
        with self.assertRaises(pending.PendingVerificationError):
            write_pending_verification(self.repo, self.selection, self.digests)
        self.assertFalse(any(target.iterdir()))

    def test_open_fd_survives_directory_swap_without_external_write(self) -> None:
        prepared = prepare_pending_verification(self.repo, self.selection, self.digests)
        gitdir = self.repo / ".git"
        original = gitdir / "hwskill"
        held = gitdir / "held-hwskill"
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        original.rename(held)
        original.symlink_to(outside, target_is_directory=True)
        prepared.publish()
        self.assertTrue((held / "pending-verification.json").is_file())
        self.assertFalse(any(outside.iterdir()))

    def test_linked_worktree_uses_its_resolved_gitdir(self) -> None:
        (self.repo / "seed").write_text("seed", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "seed"], check=True)
        linked = Path(self.temporary.name) / "linked"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "linked", str(linked)], check=True)
        (linked / "registry").mkdir(exist_ok=True)
        (linked / "registry/catalog.json").write_text((self.repo / "registry/catalog.json").read_text(encoding="utf-8"), encoding="utf-8")
        path = write_pending_verification(linked, self.selection, self.digests)
        self.assertFalse(path.is_relative_to(linked))
        self.assertEqual(load_pending_verification(linked).selection, self.selection)
