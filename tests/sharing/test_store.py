from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class UpdateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_constructor_does_not_create_a_missing_state_database(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)

        self.assertFalse(store.database_exists)
        self.assertFalse(database.exists())

    def test_single_instance_lock_rejects_a_second_store(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, UpdateStoreLockedError

        first = SQLiteUpdateStore(self.root / "state.sqlite3")
        second = SQLiteUpdateStore(self.root / "state.sqlite3")

        with first.lock(), self.assertRaises(UpdateStoreLockedError):
            with second.lock():
                self.fail("second lock unexpectedly succeeded")

    def test_ack_transaction_rolls_back_cursor_and_pending_on_interruption(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        def interrupt(stage: str) -> None:
            if stage == "after_cursor_update":
                raise RuntimeError("power loss")

        store = SQLiteUpdateStore(self.root / "state.sqlite3", fault_injector=interrupt)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        store.save_pending(
            {
                "schema_version": 1,
                "consumer_id": "consumer-a",
                "batch_id": "batch-" + "a" * 64,
                "feed_id": "hwskill-main",
                "from_sequence": 0,
                "through_sequence": 1,
                "filter_digest": "sha256:filters",
                "snapshot_identity": "sha256:snapshot",
                "items": [],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "power loss"):
            store.commit_acknowledgement("consumer-a", "batch-" + "a" * 64)

        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)
        self.assertEqual(store.load_pending("consumer-a")["batch_id"], "batch-" + "a" * 64)

    def test_ack_transaction_rolls_back_ack_row_and_pending_delete_before_commit(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        def interrupt(stage: str) -> None:
            if stage == "before_ack_commit":
                raise RuntimeError("power loss")

        store = SQLiteUpdateStore(self.root / "state.sqlite3", fault_injector=interrupt)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch_id = "batch-" + "a" * 64
        store.save_pending(
            {
                "schema_version": 1,
                "consumer_id": "consumer-a",
                "batch_id": batch_id,
                "feed_id": "hwskill-main",
                "from_sequence": 0,
                "through_sequence": 1,
                "filter_digest": "sha256:filters",
                "snapshot_identity": "sha256:snapshot",
                "items": [],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "power loss"):
            store.commit_acknowledgement("consumer-a", batch_id)

        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)
        self.assertEqual(store.load_pending("consumer-a")["batch_id"], batch_id)
        self.assertIsNone(store.load_acknowledgement("consumer-a", batch_id))

    def test_duplicate_ack_rejects_a_corrupt_persisted_acknowledgement(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch_id = "batch-" + "a" * 64
        store.save_pending(
            {
                "schema_version": 1,
                "consumer_id": "consumer-a",
                "batch_id": batch_id,
                "feed_id": "hwskill-main",
                "from_sequence": 0,
                "through_sequence": 1,
                "filter_digest": "sha256:filters",
                "snapshot_identity": "sha256:snapshot",
                "items": [],
            }
        )
        acknowledgement = store.commit_acknowledgement("consumer-a", batch_id)
        acknowledgement["through_sequence"] = 999
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE acknowledgements SET acknowledgement_json = ? WHERE consumer_id = ? AND batch_id = ?",
                (json.dumps(acknowledgement), "consumer-a", batch_id),
            )

        with self.assertRaises(StateDatabaseCorruptError):
            store.commit_acknowledgement("consumer-a", batch_id)

    def test_pending_replacement_rolls_back_to_the_previous_batch_on_interruption(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        old = {
            "schema_version": 1,
            "consumer_id": "consumer-a",
            "batch_id": "batch-" + "a" * 64,
            "feed_id": "hwskill-main",
            "from_sequence": 0,
            "through_sequence": 1,
            "filter_digest": "sha256:filters",
            "snapshot_identity": "sha256:old",
            "items": [],
        }
        store.save_pending(old)
        new = {**old, "batch_id": "batch-" + "b" * 64, "through_sequence": 2, "snapshot_identity": "sha256:new"}

        def interrupt(stage: str) -> None:
            if stage == "before_pending_commit":
                raise RuntimeError("power loss")

        with self.assertRaisesRegex(RuntimeError, "power loss"):
            SQLiteUpdateStore(database, fault_injector=interrupt).save_pending(new)
        self.assertEqual(store.load_pending("consumer-a"), old)

    def test_pending_json_must_match_its_indexed_sqlite_columns(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch = {
            "schema_version": 1,
            "consumer_id": "consumer-a",
            "batch_id": "batch-" + "a" * 64,
            "feed_id": "hwskill-main",
            "from_sequence": 0,
            "through_sequence": 1,
            "filter_digest": "sha256:filters",
            "snapshot_identity": "sha256:snapshot",
            "items": [],
        }
        store.save_pending(batch)
        changed = {**batch, "batch_id": "batch-" + "b" * 64}
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE pending_batches SET batch_json = ? WHERE consumer_id = ?",
                (json.dumps(changed), "consumer-a"),
            )

        with self.assertRaises(StateDatabaseCorruptError):
            store.load_pending("consumer-a")
        with self.assertRaises(StateDatabaseCorruptError):
            store.commit_acknowledgement("consumer-a", batch["batch_id"])
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)

    def test_pending_boolean_sequence_cannot_alias_sqlite_integer_zero(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch = {
            "schema_version": 1,
            "consumer_id": "consumer-a",
            "batch_id": "batch-" + "a" * 64,
            "feed_id": "hwskill-main",
            "from_sequence": 0,
            "through_sequence": 1,
            "filter_digest": "sha256:filters",
            "snapshot_identity": "sha256:snapshot",
            "items": [],
        }
        store.save_pending(batch)
        batch["from_sequence"] = False
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE pending_batches SET batch_json = ? WHERE consumer_id = ?",
                (json.dumps(batch), "consumer-a"),
            )

        with self.assertRaises(StateDatabaseCorruptError):
            store.load_pending("consumer-a")

    def test_lock_is_released_when_the_owner_raises(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        first = SQLiteUpdateStore(self.root / "state.sqlite3")
        second = SQLiteUpdateStore(self.root / "state.sqlite3")
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with first.lock():
                raise RuntimeError("stop")
        with second.lock():
            pass

    def test_database_replacement_between_safe_open_and_sqlite_connect_is_rejected(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        database = self.root / "state.sqlite3"
        evil = self.root / "evil.sqlite3"
        for path, consumer_id in ((database, "consumer-a"), (evil, "consumer-evil")):
            SQLiteUpdateStore(path).initialize_consumer(
                consumer_id=consumer_id,
                feed_id="hwskill-main",
                feed_url="https://directory.test/feed",
                filter_digest="sha256:filters",
                cursor_sequence=0,
                initialization="replay",
            )
        displaced = self.root / "displaced.sqlite3"
        real_connect = sqlite3.connect
        raced = False

        def replace_before_connect(target, *args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                os.replace(database, displaced)
                os.symlink(evil, database)
            return real_connect(target, *args, **kwargs)

        with patch("hwskill.sharing.store.sqlite3.connect", side_effect=replace_before_connect):
            with self.assertRaises(StateDatabaseCorruptError):
                SQLiteUpdateStore(database).load_consumer("consumer-a")
        self.assertTrue(raced)

    def test_renaming_and_recreating_lock_file_cannot_admit_a_second_instance(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, UpdateStoreLockedError

        database = self.root / "state.sqlite3"
        lock_path = self.root / "state.sqlite3.lock"
        lock_path.write_text("first", encoding="utf-8")
        first = SQLiteUpdateStore(database)
        second = SQLiteUpdateStore(database)

        with first.lock():
            os.replace(lock_path, self.root / "displaced.lock")
            lock_path.write_text("replacement", encoding="utf-8")
            with self.assertRaises(UpdateStoreLockedError):
                with second.lock():
                    self.fail("replacement lock inode admitted a concurrent instance")

    def test_transaction_rejects_parent_directory_replacement_before_commit(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        state_root = self.root / "state-root"
        state_root.mkdir()
        database = state_root / "state.sqlite3"
        base = SQLiteUpdateStore(database)
        base.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        moved_root = self.root / "moved-state-root"

        def replace_parent(stage: str) -> None:
            if stage == "before_pending_commit":
                os.replace(state_root, moved_root)
                state_root.mkdir()

        store = SQLiteUpdateStore(database, fault_injector=replace_parent)
        caught: BaseException | None = None
        try:
            store.save_pending(
                {
                    "schema_version": 1,
                    "consumer_id": "consumer-a",
                    "batch_id": "batch-" + "a" * 64,
                    "feed_id": "hwskill-main",
                    "from_sequence": 0,
                    "through_sequence": 1,
                    "filter_digest": "sha256:filters",
                    "snapshot_identity": "sha256:snapshot",
                    "items": [],
                }
            )
        except BaseException as exc:
            caught = exc
        self.assertIsInstance(caught, StateDatabaseCorruptError)
        self.assertIsNone(SQLiteUpdateStore(moved_root / "state.sqlite3").load_pending("consumer-a"))

    def test_rollback_failure_does_not_replace_the_original_transaction_error(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        real_connect = sqlite3.connect

        class RollbackFailingConnection:
            def __init__(self, connection):
                object.__setattr__(self, "_connection", connection)

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def __setattr__(self, name, value):
                setattr(self._connection, name, value)

            def rollback(self):
                raise sqlite3.OperationalError("rollback is also busy")

        def connect_with_failing_rollback(*args, **kwargs):
            return RollbackFailingConnection(real_connect(*args, **kwargs))

        interrupted = SQLiteUpdateStore(
            database,
            fault_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("primary failure"))
            if stage == "before_pending_commit"
            else None,
        )
        with patch("hwskill.sharing.store.sqlite3.connect", side_effect=connect_with_failing_rollback):
            caught: BaseException | None = None
            try:
                interrupted.save_pending(
                    {
                        "schema_version": 1,
                        "consumer_id": "consumer-a",
                        "batch_id": "batch-" + "a" * 64,
                        "feed_id": "hwskill-main",
                        "from_sequence": 0,
                        "through_sequence": 1,
                        "filter_digest": "sha256:filters",
                        "snapshot_identity": "sha256:snapshot",
                        "items": [],
                    }
                )
            except BaseException as exc:
                caught = exc
        self.assertIsInstance(caught, RuntimeError)
        self.assertIn("primary failure", str(caught))

    def test_interrupted_first_schema_initialization_leaves_no_final_database_and_retry_succeeds(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore

        database = self.root / "state.sqlite3"

        def interrupt(stage: str) -> None:
            if stage == "after_database_schema":
                raise RuntimeError("schema initialization crashed")

        with self.assertRaisesRegex(RuntimeError, "schema initialization crashed"):
            SQLiteUpdateStore(database, fault_injector=interrupt).initialize_consumer(
                consumer_id="consumer-a",
                feed_id="hwskill-main",
                feed_url="https://directory.test/feed",
                filter_digest="sha256:filters",
                cursor_sequence=0,
                initialization="replay",
            )
        self.assertFalse(database.exists())

        recovered = SQLiteUpdateStore(database)
        recovered.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        self.assertEqual(recovered.load_consumer("consumer-a")["consumer_id"], "consumer-a")

    def test_first_database_publication_never_overwrites_a_competing_final_inode(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, UpdateStoreError

        database = self.root / "state.sqlite3"
        competing = self.root / "competing.sqlite3"
        competitor = SQLiteUpdateStore(competing)
        competitor.initialize_consumer(
            consumer_id="consumer-evil",
            feed_id="other-feed",
            feed_url="https://evil.test/feed",
            filter_digest="sha256:evil",
            cursor_sequence=0,
            initialization="replay",
        )

        def publish_competitor(stage: str) -> None:
            if stage == "before_database_publish":
                os.link(competing, database)

        with self.assertRaises(UpdateStoreError):
            SQLiteUpdateStore(database, fault_injector=publish_competitor).initialize_consumer(
                consumer_id="consumer-a",
                feed_id="hwskill-main",
                feed_url="https://directory.test/feed",
                filter_digest="sha256:filters",
                cursor_sequence=0,
                initialization="replay",
            )
        self.assertEqual(SQLiteUpdateStore(database).load_consumer("consumer-evil")["feed_id"], "other-feed")

    def test_consumer_read_rejects_invalid_types_enums_ranges_and_extra_columns(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        mutations = (
            ("cursor text", "UPDATE consumers SET cursor_sequence = 'oops'"),
            ("negative cursor", "UPDATE consumers SET cursor_sequence = -1"),
            ("bad initialization", "UPDATE consumers SET initialization = 'automatic'"),
            ("bad last ack", "UPDATE consumers SET last_acknowledged_batch_id = 'not-a-batch'"),
            ("empty feed", "UPDATE consumers SET feed_id = ''"),
            ("empty URL", "UPDATE consumers SET feed_url = ''"),
            ("empty filter", "UPDATE consumers SET filter_digest = ''"),
            ("extra column", "ALTER TABLE consumers ADD COLUMN injected TEXT"),
        )
        for index, (label, statement) in enumerate(mutations):
            with self.subTest(label=label):
                database = self.root / f"consumer-{index}.sqlite3"
                store = SQLiteUpdateStore(database)
                store.initialize_consumer(
                    consumer_id="consumer-a",
                    feed_id="hwskill-main",
                    feed_url="https://directory.test/feed",
                    filter_digest="sha256:filters",
                    cursor_sequence=0,
                    initialization="replay",
                )
                with sqlite3.connect(database) as connection:
                    connection.execute("PRAGMA ignore_check_constraints = ON")
                    connection.execute(statement)
                with self.assertRaises(StateDatabaseCorruptError):
                    store.load_consumer("consumer-a")

    def test_pending_read_rejects_synced_fields_that_no_longer_match_the_consumer(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch = {
            "schema_version": 1,
            "consumer_id": "consumer-a",
            "batch_id": "batch-" + "a" * 64,
            "feed_id": "hwskill-main",
            "from_sequence": 0,
            "through_sequence": 1,
            "filter_digest": "sha256:filters",
            "snapshot_identity": "sha256:snapshot",
            "items": [],
        }
        store.save_pending(batch)
        batch["filter_digest"] = "sha256:other"
        batch["from_sequence"] = "oops"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """UPDATE pending_batches
                   SET filter_digest = ?, from_sequence = ?, batch_json = ?
                   WHERE consumer_id = ?""",
                ("sha256:other", "oops", json.dumps(batch), "consumer-a"),
            )

        with self.assertRaises(StateDatabaseCorruptError):
            store.load_pending("consumer-a")

    def test_acknowledgement_read_rejects_a_non_integer_consumer_cursor_without_typeerror(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateDatabaseCorruptError

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch_id = "batch-" + "a" * 64
        store.save_pending(
            {
                "schema_version": 1,
                "consumer_id": "consumer-a",
                "batch_id": batch_id,
                "feed_id": "hwskill-main",
                "from_sequence": 0,
                "through_sequence": 1,
                "filter_digest": "sha256:filters",
                "snapshot_identity": "sha256:snapshot",
                "items": [],
            }
        )
        store.commit_acknowledgement("consumer-a", batch_id)
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE consumers SET cursor_sequence = 'oops'")

        caught: BaseException | None = None
        try:
            store.load_acknowledgement("consumer-a", batch_id)
        except BaseException as exc:
            caught = exc
        self.assertIsInstance(caught, StateDatabaseCorruptError)

    def test_save_pending_reports_uncertain_if_database_name_changes_inside_commit(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateCommitUncertainError

        database = self.root / "state.sqlite3"
        evil = self.root / "evil.sqlite3"
        moved = self.root / "committed.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        SQLiteUpdateStore(evil).initialize_consumer(
            consumer_id="consumer-evil",
            feed_id="other-feed",
            feed_url="https://evil.test/feed",
            filter_digest="sha256:evil",
            cursor_sequence=0,
            initialization="replay",
        )
        real_connect = sqlite3.connect
        raced = False

        class CommitRacingConnection:
            def __init__(self, connection):
                object.__setattr__(self, "_connection", connection)

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def __setattr__(self, name, value):
                setattr(self._connection, name, value)

            def commit(self):
                nonlocal raced
                result = self._connection.commit()
                if not raced:
                    raced = True
                    os.replace(database, moved)
                    os.link(evil, database)
                return result

        def racing_connect(*args, **kwargs):
            return CommitRacingConnection(real_connect(*args, **kwargs))

        with patch("hwskill.sharing.store.sqlite3.connect", side_effect=racing_connect):
            with self.assertRaises(StateCommitUncertainError):
                store.save_pending(
                    {
                        "schema_version": 1,
                        "consumer_id": "consumer-a",
                        "batch_id": "batch-" + "a" * 64,
                        "feed_id": "hwskill-main",
                        "from_sequence": 0,
                        "through_sequence": 1,
                        "filter_digest": "sha256:filters",
                        "snapshot_identity": "sha256:snapshot",
                        "items": [],
                    }
                )
        self.assertTrue(raced)
        self.assertEqual(SQLiteUpdateStore(moved).load_pending("consumer-a")["batch_id"], "batch-" + "a" * 64)
        self.assertEqual(SQLiteUpdateStore(database).load_consumer("consumer-evil")["feed_id"], "other-feed")

    def test_ack_reports_uncertain_if_database_name_changes_inside_commit(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, StateCommitUncertainError

        database = self.root / "state.sqlite3"
        evil = self.root / "evil.sqlite3"
        moved = self.root / "committed.sqlite3"
        store = SQLiteUpdateStore(database)
        store.initialize_consumer(
            consumer_id="consumer-a",
            feed_id="hwskill-main",
            feed_url="https://directory.test/feed",
            filter_digest="sha256:filters",
            cursor_sequence=0,
            initialization="replay",
        )
        batch_id = "batch-" + "a" * 64
        store.save_pending(
            {
                "schema_version": 1,
                "consumer_id": "consumer-a",
                "batch_id": batch_id,
                "feed_id": "hwskill-main",
                "from_sequence": 0,
                "through_sequence": 1,
                "filter_digest": "sha256:filters",
                "snapshot_identity": "sha256:snapshot",
                "items": [],
            }
        )
        SQLiteUpdateStore(evil).initialize_consumer(
            consumer_id="consumer-evil",
            feed_id="other-feed",
            feed_url="https://evil.test/feed",
            filter_digest="sha256:evil",
            cursor_sequence=0,
            initialization="replay",
        )
        real_connect = sqlite3.connect
        raced = False

        class CommitRacingConnection:
            def __init__(self, connection):
                object.__setattr__(self, "_connection", connection)

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def __setattr__(self, name, value):
                setattr(self._connection, name, value)

            def commit(self):
                nonlocal raced
                result = self._connection.commit()
                if not raced:
                    raced = True
                    os.replace(database, moved)
                    os.link(evil, database)
                return result

        def racing_connect(*args, **kwargs):
            return CommitRacingConnection(real_connect(*args, **kwargs))

        with patch("hwskill.sharing.store.sqlite3.connect", side_effect=racing_connect):
            with self.assertRaises(StateCommitUncertainError):
                store.commit_acknowledgement("consumer-a", batch_id)
        self.assertTrue(raced)
        committed = SQLiteUpdateStore(moved)
        self.assertEqual(committed.load_consumer("consumer-a")["cursor_sequence"], 1)
        self.assertEqual(committed.load_acknowledgement("consumer-a", batch_id)["batch_id"], batch_id)
        self.assertEqual(SQLiteUpdateStore(database).load_consumer("consumer-evil")["feed_id"], "other-feed")

    def test_state_creation_rejects_symlink_ancestor_without_creating_outside_directories(self) -> None:
        from hwskill.sharing.store import SQLiteUpdateStore, UpdateStoreError

        requested = self.root / "requested"
        outside = self.root / "outside"
        requested.mkdir()
        outside.mkdir()
        (requested / "link").symlink_to(outside, target_is_directory=True)
        store = SQLiteUpdateStore(requested / "link" / "nested" / "state.sqlite3")

        with self.assertRaises(UpdateStoreError):
            store.initialize_consumer(
                consumer_id="consumer-a",
                feed_id="hwskill-main",
                feed_url="https://directory.test/feed",
                filter_digest="sha256:filters",
                cursor_sequence=0,
                initialization="replay",
            )

        self.assertFalse((outside / "nested").exists())


if __name__ == "__main__":
    unittest.main()
