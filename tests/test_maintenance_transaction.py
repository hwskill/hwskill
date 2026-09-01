from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import shutil
import unittest

from hwskill.maintenance_transaction import (
    MaintenanceSummary,
    RepositoryTransaction,
    TransactionConflictError,
    TransactionError,
    validated_plan,
)


ROOT = Path(__file__).parents[1]


class RepositoryTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self._write("sources/a.yaml", "old source\n")
        self._write("sources/old-directory/manifest.yaml", "old directory\n")
        self._write("registry/catalog.json", "old catalog\n")
        self._write("skills-src/l1/local/example/SKILL.md", "old skill\n")
        self._write("profiles/example.yaml", "old profile\n")
        self._write("tests/skills/example/test.yaml", "old test\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, relative: str, content: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_candidate_contains_only_managed_roots(self) -> None:
        self._write("notes/unmanaged.txt", "keep out\n")
        self._write(".git/config", "not a real git dir for this test\n")

        with RepositoryTransaction(self.repo) as tx:
            self.assertEqual(
                sorted(path.name for path in tx.candidate_root.iterdir()),
                ["profiles", "registry", "skills-src", "sources", "tests"],
            )
            self.assertFalse((tx.candidate_root / "notes").exists())
            self.assertFalse((tx.candidate_root / ".git").exists())

    def test_apply_rolls_back_every_target_when_second_replace_fails(self) -> None:
        def fail_second_operation(index: int, operation: str) -> None:
            self.assertIsInstance(index, int)
            self.assertEqual(operation, "replace")
            if index == 2:
                raise OSError("injected replacement failure")

        tx = RepositoryTransaction(self.repo, before_operation=fail_second_operation)
        tx.write_text(Path("sources/a.yaml"), "new source\n")
        tx.write_text(Path("registry/catalog.json"), "new catalog\n")

        with self.assertRaisesRegex(OSError, "injected replacement failure"):
            tx.apply()

        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")
        self.assertEqual((self.repo / "registry/catalog.json").read_text(encoding="utf-8"), "old catalog\n")

    def test_rollback_restores_deleted_and_created_directories(self) -> None:
        def fail_second_operation(index: int, operation: str) -> None:
            self.assertIn(operation, {"replace", "remove"})
            if index == 2:
                raise OSError("injected replacement failure")

        tx = RepositoryTransaction(
            self.repo,
            before_operation=fail_second_operation,
        )
        tx.delete(Path("sources/old-directory"))
        tx.write_text(Path("sources/new-directory/manifest.yaml"), "new directory\n")

        with self.assertRaisesRegex(OSError, "injected replacement failure"):
            tx.apply()

        self.assertEqual(
            (self.repo / "sources/old-directory/manifest.yaml").read_text(encoding="utf-8"),
            "old directory\n",
        )
        self.assertFalse((self.repo / "sources/new-directory").exists())

    def test_apply_rejects_target_preimage_drift_without_overwriting_it(self) -> None:
        tx = RepositoryTransaction(self.repo)
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")
        self._write("sources/a.yaml", "concurrent source\n")

        with self.assertRaisesRegex(TransactionConflictError, "preimage changed"):
            tx.apply()

        self.assertEqual(
            (self.repo / "sources/a.yaml").read_text(encoding="utf-8"),
            "concurrent source\n",
        )

    def test_apply_preserves_unrelated_dirty_files(self) -> None:
        dirty = self._write("notes/unrelated.txt", "user work\n")
        tx = RepositoryTransaction(self.repo)
        tx.write_text(Path("sources/a.yaml"), "new source\n")

        tx.apply()

        self.assertEqual(dirty.read_text(encoding="utf-8"), "user work\n")
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "new source\n")

    def test_multi_source_writes_apply_as_one_candidate_plan(self) -> None:
        self._write("sources/b.yaml", "old second source\n")
        tx = RepositoryTransaction(self.repo)
        tx.write_text(Path("sources/a.yaml"), "new first source\n")
        tx.write_text(Path("sources/b.yaml"), "new second source\n")

        self.assertEqual(
            tx.changed_paths(),
            (Path("sources/a.yaml"), Path("sources/b.yaml")),
        )
        tx.apply()

        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "new first source\n")
        self.assertEqual((self.repo / "sources/b.yaml").read_text(encoding="utf-8"), "new second source\n")

    def test_context_cleanup_is_idempotent_after_validation(self) -> None:
        validated: list[Path] = []
        tx: RepositoryTransaction
        with RepositoryTransaction(self.repo, validate=validated.append) as tx:
            candidate = tx.candidate_root
            tx.write_text(Path("profiles/example.yaml"), "new profile\n")
            tx.apply()
            self.assertEqual(validated, [candidate])

        self.assertFalse(candidate.exists())
        tx.discard()
        self.assertFalse(candidate.exists())

    def test_validate_rejects_an_invalid_candidate_and_discards_it_before_apply(self) -> None:
        """A failed gate must close the candidate before it can touch the real repository."""
        tx = RepositoryTransaction(self.repo)
        candidate = tx.candidate_root
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        with self.assertRaisesRegex(ValueError, "invalid candidate"):
            validated_plan(
                tx,
                MaintenanceSummary("test"),
                lambda root: (_ for _ in ()).throw(ValueError("invalid candidate")),
            )

        self.assertFalse(candidate.exists())
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")
        with self.assertRaisesRegex(TransactionError, "discarded"):
            tx.apply()

    def test_validate_runs_only_while_active_and_keeps_a_clean_candidate_applicable(self) -> None:
        """A successful validation is a planning gate, not a second apply lifecycle."""
        validated: list[Path] = []
        tx = RepositoryTransaction(self.repo)
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        tx.validate(validated.append)
        tx.apply()

        self.assertEqual(validated, [tx.candidate_root])
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "candidate source\n")
        with self.assertRaisesRegex(TransactionError, "already applied"):
            tx.validate(validated.append)

    def test_candidate_with_only_managed_roots_passes_the_full_integrity_gate(self) -> None:
        """Candidate validation must not require README or checked-in example bindings."""
        from hwskill.integrity import require_integrity

        with RepositoryTransaction(ROOT) as tx:
            tx.validate(require_integrity)

    def test_direct_discard_closes_transaction_without_mutating_worktree(self) -> None:
        tx = RepositoryTransaction(self.repo)
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        tx.discard()
        tx.discard()

        with self.assertRaisesRegex(TransactionError, "discarded"):
            tx.changed_paths()
        with self.assertRaisesRegex(TransactionError, "discarded"):
            tx.apply()
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")

    def test_context_exit_closes_transaction_without_mutating_worktree(self) -> None:
        with RepositoryTransaction(self.repo) as tx:
            tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        with self.assertRaisesRegex(TransactionError, "discarded"):
            tx.apply()
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")

    def test_repeated_apply_is_rejected_even_when_first_apply_has_no_changes(self) -> None:
        tx = RepositoryTransaction(self.repo)

        tx.apply()

        with self.assertRaisesRegex(TransactionError, "already applied"):
            tx.apply()
        with self.assertRaisesRegex(TransactionError, "already applied"):
            tx.changed_paths()
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")

    def test_replace_rejects_symlinked_managed_ancestor_without_touching_external_file(self) -> None:
        external = self.repo.parent / "external"
        external.mkdir()
        external_file = external / "a.yaml"
        external_file.write_text("old source\n", encoding="utf-8")
        tx = RepositoryTransaction(self.repo)
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")
        shutil.rmtree(self.repo / "sources")
        os.symlink(external, self.repo / "sources")

        with self.assertRaises(TransactionError):
            tx.apply()

        self.assertEqual(external_file.read_text(encoding="utf-8"), "old source\n")
        self.assertTrue((self.repo / "sources").is_symlink())

    def test_delete_rejects_symlinked_managed_ancestor_without_touching_external_file(self) -> None:
        external = self.repo.parent / "external"
        external.mkdir()
        external_file = external / "a.yaml"
        external_file.write_text("old source\n", encoding="utf-8")
        tx = RepositoryTransaction(self.repo)
        tx.delete(Path("sources/a.yaml"))
        shutil.rmtree(self.repo / "sources")
        os.symlink(external, self.repo / "sources")

        with self.assertRaises(TransactionError):
            tx.apply()

        self.assertEqual(external_file.read_text(encoding="utf-8"), "old source\n")
        self.assertTrue((self.repo / "sources").is_symlink())

    def test_rollback_does_not_follow_a_managed_ancestor_replaced_by_symlink(self) -> None:
        external = self.repo.parent / "external"
        external.mkdir()
        external_file = external / "a.yaml"
        external_file.write_text("old source\n", encoding="utf-8")
        original_inode = external_file.stat().st_ino
        calls = 0

        def swap_next_target_ancestor(index: int, operation: str) -> None:
            if index == 1:
                shutil.rmtree(self.repo / "sources")
                os.symlink(external, self.repo / "sources")

        tx = RepositoryTransaction(self.repo, before_operation=swap_next_target_ancestor)
        tx.write_text(Path("registry/catalog.json"), "candidate catalog\n")
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        with self.assertRaises(TransactionError):
            tx.apply()

        self.assertEqual(external_file.read_text(encoding="utf-8"), "old source\n")
        self.assertEqual(external_file.stat().st_ino, original_inode)
        self.assertEqual((self.repo / "registry/catalog.json").read_text(encoding="utf-8"), "old catalog\n")
        self.assertFalse((self.repo / "sources").is_symlink())
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")

    def test_replace_hook_cannot_redirect_the_same_target_to_a_symlinked_ancestor(self) -> None:
        external = self.repo.parent / "external"
        external.mkdir()
        sentinel = external / "a.yaml"
        sentinel.write_text("external sentinel\n", encoding="utf-8")

        def swap_same_target_ancestor(index: int, operation: str) -> None:
            self.assertEqual((index, operation), (1, "replace"))
            shutil.rmtree(self.repo / "sources")
            os.symlink(external, self.repo / "sources")

        tx = RepositoryTransaction(self.repo, before_operation=swap_same_target_ancestor)
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        original_cwd = Path.cwd()
        os.chdir(self.repo)
        try:
            with self.assertRaises(TransactionError):
                tx.apply()
        finally:
            os.chdir(original_cwd)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "external sentinel\n")
        self.assertFalse((self.repo / "sources").is_symlink())
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")

    def test_remove_hook_cannot_redirect_the_same_target_to_a_symlinked_ancestor(self) -> None:
        external = self.repo.parent / "external"
        external.mkdir()
        sentinel = external / "a.yaml"
        sentinel.write_text("external sentinel\n", encoding="utf-8")

        def swap_same_target_ancestor(index: int, operation: str) -> None:
            self.assertEqual((index, operation), (1, "remove"))
            shutil.rmtree(self.repo / "sources")
            os.symlink(external, self.repo / "sources")

        tx = RepositoryTransaction(self.repo, before_operation=swap_same_target_ancestor)
        tx.delete(Path("sources/a.yaml"))

        original_cwd = Path.cwd()
        os.chdir(self.repo)
        try:
            with self.assertRaises(TransactionError):
                tx.apply()
        finally:
            os.chdir(original_cwd)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "external sentinel\n")
        self.assertFalse((self.repo / "sources").is_symlink())
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")

    def test_root_recovery_unlinks_nested_symlink_and_restores_all_contents(self) -> None:
        sibling = self._write("sources/unchanged.yaml", "unchanged sibling\n")
        external = self.repo.parent / "external"
        external.mkdir()
        sentinel = external / "sentinel.txt"
        sentinel.write_text("external sentinel\n", encoding="utf-8")

        def inject_nested_symlink(index: int, operation: str) -> None:
            self.assertEqual((index, operation), (1, "replace"))
            os.symlink(external, self.repo / "sources/injected-link")
            (self.repo / "sources/a.yaml").write_text("tampered\n", encoding="utf-8")

        tx = RepositoryTransaction(self.repo, before_operation=inject_nested_symlink)
        tx.write_text(Path("sources/a.yaml"), "candidate source\n")

        with self.assertRaises(TransactionError):
            tx.apply()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "external sentinel\n")
        self.assertFalse((self.repo / "sources/injected-link").exists())
        self.assertFalse((self.repo / "sources/injected-link").is_symlink())
        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")
        self.assertEqual(sibling.read_text(encoding="utf-8"), "unchanged sibling\n")


if __name__ == "__main__":
    unittest.main()
