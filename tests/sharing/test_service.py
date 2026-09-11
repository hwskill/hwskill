from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tests.sharing.support import mutable_snapshot, refresh_snapshot_identity, valid_snapshot


class StaticReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls: list[str] = []

    def read_snapshot(self, feed_url: str):
        self.calls.append(feed_url)
        return self.snapshot


def advanced_empty_snapshot():
    from hwskill.sharing.models import FeedSnapshot

    payload = mutable_snapshot()
    second = deepcopy(payload["records"][0])
    second.update(
        sequence=2,
        release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        previous_sequence=1,
        publication_time="2026-09-12T01:00:00Z",
        committed_at="2026-09-12T01:01:00Z",
        events=[],
    )
    payload["records"].append(second)
    payload["head"].update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    refresh_snapshot_identity(payload)
    return FeedSnapshot.from_dict(payload)


class UpdateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, **changes):
        from hwskill.sharing.service import SharingConfig

        values = {
            "consumer_id": "consumer-a",
            "feed_url": "https://directory.test/feed",
        }
        values.update(changes)
        return SharingConfig(**values)

    def test_missing_database_requires_explicit_initialization_before_network_read(self) -> None:
        from hwskill.sharing.service import InitializationRequiredError, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        reader = StaticReader(valid_snapshot())
        database = self.root / "missing.sqlite3"

        with self.assertRaises(InitializationRequiredError):
            prepare_updates(reader, SQLiteUpdateStore(database), self.config())

        self.assertEqual(reader.calls, [])
        self.assertFalse(database.exists())

    def test_replay_and_baseline_are_distinct_explicit_first_run_modes(self) -> None:
        from hwskill.sharing.service import prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        replay = prepare_updates(
            StaticReader(valid_snapshot()),
            SQLiteUpdateStore(self.root / "replay.sqlite3"),
            self.config(initialization="replay"),
        )
        baseline = prepare_updates(
            StaticReader(valid_snapshot()),
            SQLiteUpdateStore(self.root / "baseline.sqlite3"),
            self.config(consumer_id="consumer-b", initialization="baseline"),
        )

        self.assertEqual((replay.from_sequence, replay.through_sequence, len(replay.items)), (0, 1, 1))
        self.assertEqual((baseline.from_sequence, baseline.through_sequence, baseline.items), (1, 1, ()))

    def test_prepare_retry_and_process_restart_return_the_same_pending_batch(self) -> None:
        from hwskill.sharing.service import prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        database = self.root / "state.sqlite3"
        config = self.config(initialization="replay")
        first = prepare_updates(StaticReader(valid_snapshot()), SQLiteUpdateStore(database), config)
        second = prepare_updates(
            StaticReader(valid_snapshot()),
            SQLiteUpdateStore(database),
            self.config(),
        )

        self.assertEqual(first, second)
        self.assertTrue(first.batch_id.startswith("batch-"))
        self.assertEqual(len(first.batch_id), 70)
        self.assertEqual(SQLiteUpdateStore(database).load_consumer("consumer-a")["cursor_sequence"], 0)

    def test_prepare_validates_the_latest_snapshot_before_reusing_pending(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.service import prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore
        from hwskill.sharing.validation import FeedValidationError

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        snapshot = valid_snapshot()
        prepare_updates(StaticReader(snapshot), store, self.config(initialization="replay"))
        changed = snapshot.to_dict()
        changed["head"]["release_id"] = "release-tampered"
        stale_identity = FeedSnapshot.from_dict(changed)

        with self.assertRaises(FeedValidationError):
            prepare_updates(StaticReader(stale_identity), store, self.config())

    def test_prepare_rederives_and_rejects_a_self_consistent_tampered_pending_batch(self) -> None:
        from hwskill.sharing.service import PendingBatchStateError, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        database = self.root / "state.sqlite3"
        store = SQLiteUpdateStore(database)
        batch = prepare_updates(StaticReader(valid_snapshot()), store, self.config(initialization="replay"))
        changed = batch.to_dict()
        changed["batch_id"] = "batch-" + "f" * 64
        with sqlite3.connect(database) as connection:
            connection.execute(
                """UPDATE pending_batches
                   SET batch_id = ?, batch_json = ?
                   WHERE consumer_id = ?""",
                (changed["batch_id"], json.dumps(changed), "consumer-a"),
            )

        with self.assertRaises(PendingBatchStateError):
            prepare_updates(StaticReader(valid_snapshot()), store, self.config())

    def test_snapshot_change_recomputes_pending_and_rejects_the_old_batch_id(self) -> None:
        from hwskill.sharing.service import AcknowledgementError, acknowledge, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        old = prepare_updates(StaticReader(valid_snapshot()), store, self.config(initialization="replay"))
        new = prepare_updates(StaticReader(advanced_empty_snapshot()), store, self.config())

        self.assertNotEqual(new.batch_id, old.batch_id)
        self.assertEqual((new.from_sequence, new.through_sequence), (0, 2))
        with self.assertRaises(AcknowledgementError):
            acknowledge(store, "consumer-a", old.batch_id)
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 0)

    def test_exact_ack_advances_cursor_and_duplicate_ack_is_idempotent(self) -> None:
        from hwskill.sharing.service import AcknowledgementError, acknowledge, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        batch = prepare_updates(StaticReader(valid_snapshot()), store, self.config(initialization="replay"))
        with self.assertRaises(AcknowledgementError):
            acknowledge(store, "consumer-a", "batch-" + "f" * 64)

        first = acknowledge(store, "consumer-a", batch.batch_id)
        second = acknowledge(store, "consumer-a", batch.batch_id)

        self.assertEqual(first, second)
        self.assertEqual(first.through_sequence, 1)
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 1)
        self.assertIsNone(store.load_pending("consumer-a"))

    def test_duplicate_ack_does_not_remove_a_newer_pending_batch(self) -> None:
        from hwskill.sharing.service import acknowledge, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        first = prepare_updates(StaticReader(valid_snapshot()), store, self.config(initialization="replay"))
        acknowledge(store, "consumer-a", first.batch_id)
        second = prepare_updates(StaticReader(advanced_empty_snapshot()), store, self.config())

        self.assertEqual(acknowledge(store, "consumer-a", first.batch_id).batch_id, first.batch_id)
        self.assertEqual(store.load_pending("consumer-a")["batch_id"], second.batch_id)
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 1)

    def test_filtered_empty_interval_remains_confirmable(self) -> None:
        from hwskill.sharing.models import UpdateFilters
        from hwskill.sharing.service import acknowledge, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        batch = prepare_updates(
            StaticReader(valid_snapshot()),
            store,
            self.config(
                initialization="replay",
                filters=UpdateFilters(change_types=("skill.withdrawn",)),
            ),
        )

        self.assertEqual(batch.items, ())
        self.assertEqual((batch.from_sequence, batch.through_sequence), (0, 1))
        acknowledge(store, "consumer-a", batch.batch_id)
        self.assertEqual(store.load_consumer("consumer-a")["cursor_sequence"], 1)

    def test_consumer_cannot_silently_change_feed_or_filters(self) -> None:
        from hwskill.sharing.models import UpdateFilters
        from hwskill.sharing.service import ConsumerConfigurationError, prepare_updates
        from hwskill.sharing.store import SQLiteUpdateStore

        store = SQLiteUpdateStore(self.root / "state.sqlite3")
        prepare_updates(StaticReader(valid_snapshot()), store, self.config(initialization="replay"))

        for changed in (
            self.config(feed_url="https://other.test/feed"),
            self.config(filters=UpdateFilters(purposes=("debugging",))),
        ):
            with self.subTest(changed=changed), self.assertRaises(ConsumerConfigurationError):
                prepare_updates(StaticReader(valid_snapshot()), store, changed)


if __name__ == "__main__":
    unittest.main()
