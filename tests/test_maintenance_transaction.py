from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hwskill.maintenance_transaction import (
    RepositoryTransaction,
    TransactionConflictError,
)


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
        calls = 0

        def fail_second_replace(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected replacement failure")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

        tx = RepositoryTransaction(self.repo, replace=fail_second_replace)
        tx.write_text(Path("sources/a.yaml"), "new source\n")
        tx.write_text(Path("registry/catalog.json"), "new catalog\n")

        with self.assertRaisesRegex(OSError, "injected replacement failure"):
            tx.apply()

        self.assertEqual((self.repo / "sources/a.yaml").read_text(encoding="utf-8"), "old source\n")
        self.assertEqual((self.repo / "registry/catalog.json").read_text(encoding="utf-8"), "old catalog\n")

    def test_rollback_restores_deleted_and_created_directories(self) -> None:
        calls = 0

        def fail_second_operation() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected replacement failure")

        def fail_second_replace(source: Path, destination: Path) -> None:
            fail_second_operation()
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                for child in source.rglob("*"):
                    if child.is_file():
                        target = destination / child.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(child.read_bytes())
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())

        def fail_second_remove(destination: Path) -> None:
            fail_second_operation()
            if destination.is_dir():
                import shutil
                shutil.rmtree(destination)
            else:
                destination.unlink()

        tx = RepositoryTransaction(
            self.repo,
            replace=fail_second_replace,
            remove=fail_second_remove,
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


if __name__ == "__main__":
    unittest.main()
