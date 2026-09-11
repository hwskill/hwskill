from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.sharing.test_service import StaticReader, advanced_empty_snapshot
from tests.sharing.support import valid_snapshot


class SharingCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare_args(self, *extra: str) -> list[str]:
        return [
            "prepare",
            "--state-db",
            str(self.root / "state.sqlite3"),
            "--consumer-id",
            "consumer-a",
            "--feed-url",
            "https://directory.test/feed",
            "--output",
            str(self.root / "batch.json"),
            *extra,
        ]

    def test_prepare_requires_explicit_first_run_mode_and_returns_structured_blocked(self) -> None:
        from hwskill.sharing.cli import main

        output = io.StringIO()
        with redirect_stdout(output):
            result = main(self.prepare_args(), reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()))

        self.assertEqual(result, 3)
        self.assertEqual(json.loads(output.getvalue())["code"], "initialization-required")
        self.assertFalse((self.root / "batch.json").exists())

    def test_prepare_writes_batch_without_advancing_until_separate_ack(self) -> None:
        from hwskill.sharing.cli import main
        from hwskill.sharing.store import SQLiteUpdateStore

        prepare_output = io.StringIO()
        with redirect_stdout(prepare_output):
            prepared = main(
                self.prepare_args("--replay-from", "0"),
                reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
            )
        payload = json.loads((self.root / "batch.json").read_text(encoding="utf-8"))
        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        self.assertEqual(prepared, 0)
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)

        ack_output = io.StringIO()
        with redirect_stdout(ack_output):
            acknowledged = main(
                [
                    "ack",
                    "--state-db",
                    str(self.root / "state.sqlite3"),
                    "--consumer-id",
                    "consumer-a",
                    "--batch-id",
                    payload["batch_id"],
                ]
            )

        self.assertEqual(acknowledged, 0)
        self.assertEqual(json.loads(ack_output.getvalue())["batch_id"], payload["batch_id"])
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 1)

    def test_atomic_output_failure_preserves_old_file_and_does_not_advance_cursor(self) -> None:
        from hwskill.sharing.cli import main
        from hwskill.sharing.store import SQLiteUpdateStore

        target = self.root / "batch.json"
        target.write_text('{"old":true}\n', encoding="utf-8")
        with patch("hwskill.sharing.output.os.replace", side_effect=OSError("disk failure")):
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    self.prepare_args("--replay-from", "0"),
                    reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
                )

        self.assertEqual(result, 1)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"old":true}\n')
        self.assertEqual(SQLiteUpdateStore(self.root / "state.sqlite3").load_consumer("consumer-a")["cursor_sequence"], 0)
        orphans = list(self.root.glob(".batch.json.*.tmp"))
        self.assertEqual(len(orphans), 1)
        self.assertEqual(stat.S_IMODE(orphans[0].stat().st_mode), 0o600)

        retry_output = io.StringIO()
        with redirect_stdout(retry_output):
            retry = main(
                self.prepare_args("--replay-from", "0"),
                reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
            )
        self.assertEqual(retry, 0)
        self.assertEqual(json.loads(retry_output.getvalue())["result"], "prepared")
        self.assertEqual(SQLiteUpdateStore(self.root / "state.sqlite3").load_consumer("consumer-a")["cursor_sequence"], 0)

    def test_json_serialization_failure_preserves_old_output_and_pending_cursor(self) -> None:
        from hwskill.sharing.output import write_json_atomic

        target = self.root / "batch.json"
        target.write_text('{"old":true}\n', encoding="utf-8")
        with self.assertRaises(TypeError):
            write_json_atomic(target, {"unsupported": object()})
        self.assertEqual(target.read_text(encoding="utf-8"), '{"old":true}\n')
        orphans = list(self.root.glob(".batch.json.*.tmp"))
        self.assertEqual(len(orphans), 1)
        self.assertEqual(stat.S_IMODE(orphans[0].stat().st_mode), 0o600)

    def test_export_refuses_to_overwrite_with_a_batch_superseded_after_prepare(self) -> None:
        from hwskill.sharing import cli as cli_module
        from hwskill.sharing.service import prepare_updates as real_prepare
        from hwskill.sharing.store import SQLiteUpdateStore

        prepared_ids: list[str] = []

        def supersede_before_export(reader, store, config):
            old = real_prepare(reader, store, config)
            new = real_prepare(
                StaticReader(advanced_empty_snapshot()),
                store,
                replace(config, initialization=None),
            )
            prepared_ids.extend((old.batch_id, new.batch_id))
            return old

        output = io.StringIO()
        with patch("hwskill.sharing.cli.prepare_updates", side_effect=supersede_before_export), redirect_stdout(output):
            result = cli_module.main(
                self.prepare_args("--replay-from", "0"),
                reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
            )

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        self.assertEqual(result, 1)
        self.assertFalse((self.root / "batch.json").exists())
        self.assertNotEqual(prepared_ids[0], prepared_ids[1])
        self.assertEqual(store.load_pending("consumer-a")["batch_id"], prepared_ids[1])
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)

    def test_output_cannot_replace_the_sqlite_database_or_its_lock(self) -> None:
        from hwskill.sharing.cli import main
        from hwskill.sharing.store import SQLiteUpdateStore

        for index, suffix in enumerate(("", ".lock", "-journal", "-wal", "-shm")):
            with self.subTest(suffix=suffix):
                database = self.root / f"state-{index}.sqlite3"
                output_path = Path(str(database) + suffix)
                output = io.StringIO()
                args = self.prepare_args("--replay-from", "0")
                args[2] = str(database)
                args[8] = str(output_path)
                with redirect_stdout(output):
                    result = main(args, reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()))

                self.assertEqual(result, 1)
                self.assertIsNone(SQLiteUpdateStore(database).load_consumer("consumer-a"))
                self.assertNotEqual(output_path.read_bytes()[:1] if output_path.exists() else b"", b"{")

    def test_atomic_output_fsyncs_file_and_parent_directory(self) -> None:
        from hwskill.sharing.output import write_json_atomic

        real_fsync = os.fsync
        modes: list[int] = []

        def recording_fsync(descriptor: int) -> None:
            modes.append(os.fstat(descriptor).st_mode)
            real_fsync(descriptor)

        with patch("hwskill.sharing.output.os.fsync", side_effect=recording_fsync):
            write_json_atomic(self.root / "output.json", {"schema_version": 1})

        self.assertTrue(any(stat.S_ISREG(mode) for mode in modes))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in modes))

    def test_failed_replace_does_not_unlink_a_later_file_at_the_temporary_name(self) -> None:
        from hwskill.sharing.output import write_json_atomic

        attacker = b"later inode\n"
        raced_name: Path | None = None
        real_replace = os.replace

        def replace_then_fail(source, _target, *, src_dir_fd, dst_dir_fd):
            nonlocal raced_name
            raced_name = self.root / source
            real_replace(
                source,
                "owned-temp-moved-aside",
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            raced_name.write_bytes(attacker)
            raise OSError("replace lost a race")

        with patch("hwskill.sharing.output.os.replace", side_effect=replace_then_fail):
            with self.assertRaisesRegex(OSError, "replace lost a race"):
                write_json_atomic(self.root / "output.json", {"schema_version": 1})

        self.assertIsNotNone(raced_name)
        self.assertTrue(raced_name.exists(), "cleanup deleted the later inode")
        self.assertEqual(raced_name.read_bytes(), attacker)

    def test_parent_fsync_failure_reports_uncertain_and_retry_is_idempotent(self) -> None:
        from hwskill.sharing.cli import main
        from hwskill.sharing.store import SQLiteUpdateStore

        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    self.prepare_args("--replay-from", "0"),
                    reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
                ),
                0,
            )
        (self.root / "batch.json").unlink()
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("directory fsync failed")
            real_fsync(descriptor)

        failed_output = io.StringIO()
        with patch("hwskill.sharing.output.os.fsync", side_effect=fail_directory_fsync), redirect_stdout(failed_output):
            result = main(
                self.prepare_args("--replay-from", "0"),
                reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
            )

        failed_payload = json.loads(failed_output.getvalue())
        written_batch = json.loads((self.root / "batch.json").read_text(encoding="utf-8"))
        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        self.assertEqual(result, 1)
        self.assertEqual(failed_payload["code"], "output-durability-uncertain")
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)
        self.assertEqual(store.load_pending("consumer-a")["batch_id"], written_batch["batch_id"])

        retry_output = io.StringIO()
        with redirect_stdout(retry_output):
            retry = main(
                self.prepare_args("--replay-from", "0"),
                reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
            )
        retried = json.loads(retry_output.getvalue())
        self.assertEqual(retry, 0)
        self.assertEqual(retried["batch_id"], written_batch["batch_id"])
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)

    def test_ack_sqlite_busy_returns_structured_blocked_without_advancing(self) -> None:
        from hwskill.sharing.cli import main
        from hwskill.sharing.store import SQLiteUpdateStore

        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    self.prepare_args("--replay-from", "0"),
                    reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
                ),
                0,
            )
        batch = json.loads((self.root / "batch.json").read_text(encoding="utf-8"))
        database = self.root / "state.sqlite3"
        blocker = sqlite3.connect(database, timeout=0, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                try:
                    result = main(
                        [
                            "ack",
                            "--state-db",
                            str(database),
                            "--consumer-id",
                            "consumer-a",
                            "--batch-id",
                            batch["batch_id"],
                        ]
                    )
                except BaseException as exc:
                    self.fail(f"CLI leaked an exception instead of structured JSON: {exc!r}")
        finally:
            blocker.rollback()
            blocker.close()

        self.assertEqual(result, 3)
        self.assertEqual(json.loads(output.getvalue())["code"], "environment-blocked")
        store = SQLiteUpdateStore(database)
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)
        self.assertEqual(store.load_pending("consumer-a")["batch_id"], batch["batch_id"])

    def test_output_rejects_temporary_name_swap_before_rename(self) -> None:
        from hwskill.sharing.output import write_json_atomic

        target = self.root / "output.json"
        target.write_text('{"old":true}\n', encoding="utf-8")
        real_fsync = os.fsync
        swapped_name: Path | None = None

        def swap_after_file_fsync(descriptor: int) -> None:
            nonlocal swapped_name
            real_fsync(descriptor)
            if stat.S_ISREG(os.fstat(descriptor).st_mode) and swapped_name is None:
                [temporary] = self.root.glob(".output.json.*.tmp")
                swapped_name = temporary
                os.replace(temporary, self.root / "owned-temp-moved-aside")
                temporary.write_text('{"attacker":true}\n', encoding="utf-8")

        with patch("hwskill.sharing.output.os.fsync", side_effect=swap_after_file_fsync):
            with self.assertRaises(OSError):
                write_json_atomic(target, {"schema_version": 1})

        self.assertIsNotNone(swapped_name)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"old":true}\n')
        self.assertEqual(swapped_name.read_text(encoding="utf-8"), '{"attacker":true}\n')

    def test_output_rejects_parent_name_swap_before_rename(self) -> None:
        from hwskill.sharing.output import write_json_atomic

        output_parent = self.root / "output-parent"
        output_parent.mkdir()
        moved_parent = self.root / "moved-output-parent"
        target = output_parent / "output.json"
        target.write_text('{"old":true}\n', encoding="utf-8")
        real_fsync = os.fsync
        swapped = False

        def swap_parent_after_file_fsync(descriptor: int) -> None:
            nonlocal swapped
            real_fsync(descriptor)
            if stat.S_ISREG(os.fstat(descriptor).st_mode) and not swapped:
                swapped = True
                [temporary] = output_parent.glob(".output.json.*.tmp")
                temporary_name = temporary.name
                os.replace(output_parent, moved_parent)
                output_parent.mkdir()
                (output_parent / temporary_name).write_text('{"attacker":true}\n', encoding="utf-8")

        with patch("hwskill.sharing.output.os.fsync", side_effect=swap_parent_after_file_fsync):
            with self.assertRaises(OSError):
                write_json_atomic(target, {"schema_version": 1})

        self.assertTrue(swapped)
        self.assertFalse(target.exists())
        self.assertEqual((moved_parent / "output.json").read_text(encoding="utf-8"), '{"old":true}\n')

    def test_corrupt_consumer_cursor_returns_structured_error_without_typeerror(self) -> None:
        from hwskill.sharing.cli import main

        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    self.prepare_args("--replay-from", "0"),
                    reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
                ),
                0,
            )
        database = self.root / "state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE consumers SET cursor_sequence = 'oops'")

        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                self.prepare_args(),
                reader_factory=lambda **_kwargs: StaticReader(valid_snapshot()),
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertEqual(payload["result"], "error")
        self.assertNotIn("TypeError", payload["message"])

    def test_output_reports_namespace_uncertain_when_parent_is_replaced_at_rename(self) -> None:
        from hwskill.sharing.output import OutputNamespaceUncertainError, write_json_atomic

        output_parent = self.root / "output-parent"
        output_parent.mkdir()
        moved_parent = self.root / "moved-output-parent"
        target = output_parent / "output.json"
        real_replace = os.replace
        raced = False

        def replace_after_parent_swap(source, destination, *, src_dir_fd, dst_dir_fd):
            nonlocal raced
            raced = True
            real_replace(output_parent, moved_parent)
            output_parent.mkdir()
            real_replace(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        with patch("hwskill.sharing.output.os.replace", side_effect=replace_after_parent_swap):
            with self.assertRaises(OutputNamespaceUncertainError):
                write_json_atomic(target, {"schema_version": 1})

        self.assertTrue(raced)
        self.assertFalse(target.exists())
        self.assertEqual(
            json.loads((moved_parent / "output.json").read_text(encoding="utf-8")),
            {"schema_version": 1},
        )

    def test_output_creation_rejects_symlink_ancestor_without_creating_outside_directories(self) -> None:
        from hwskill.sharing.output import write_json_atomic

        requested = self.root / "requested"
        outside = self.root / "outside"
        requested.mkdir()
        outside.mkdir()
        (requested / "link").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(OSError):
            write_json_atomic(requested / "link" / "nested" / "output.json", {"schema_version": 1})

        self.assertFalse((outside / "nested").exists())


if __name__ == "__main__":
    unittest.main()
