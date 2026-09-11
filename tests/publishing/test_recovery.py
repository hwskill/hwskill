from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.publishing.test_release import Clock, make_site, request_for


UTC = timezone.utc


class FailOnce:
    def __init__(self, target: str):
        self.target = target
        self.failed = False

    def __call__(self, stage: str) -> None:
        if stage == self.target and not self.failed:
            self.failed = True
            raise RuntimeError(f"injected {stage}")


class PublicationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def publisher(self, *, fault=None):
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore

        store = FileReleaseStore(self.root / "publication")
        return store, Publisher(
            store,
            feed_id="hwskill-main",
            public_base_url="https://skills.example.test",
            clock=Clock(datetime(2026, 9, 11, 5, 0, tzinfo=UTC)),
            fault_injector=fault,
        )

    def test_every_stage_failure_is_resumable_without_early_feed_or_duplicate_event(self) -> None:
        stages = ["after_prepared", "after_uploaded", "after_checked", "after_activated", "after_log_record"]
        expected_states = ["prepared", "uploaded", "checked", "activated", "activated"]
        for index, (stage, expected_state) in enumerate(zip(stages, expected_states)):
            with self.subTest(stage=stage):
                case_root = self.root / f"case-{index}"
                case_root.mkdir()
                original_root = self.root
                self.root = case_root
                self.addCleanup(setattr, self, "root", original_root)
                site = make_site(case_root, marker=stage)
                request = request_for(site, source_commit=f"{index + 5:040x}")
                store, failing = self.publisher(fault=FailOnce(stage))

                with self.assertRaisesRegex(RuntimeError, stage):
                    failing.publish(request)
                self.assertEqual(store.load_candidate(request.release_id).state.value, expected_state)
                self.assertEqual(store.read_feed(), [])

                _, retry = self.publisher()
                result = retry.publish(request)
                self.assertEqual(result.state.value, "committed")
                self.assertEqual(len(store.read_feed()), 1)
                event_ids = [event["event_id"] for event in store.read_feed()[0]["events"]]
                self.assertEqual(len(event_ids), len(set(event_ids)))
                self.root = original_root

    def test_activated_candidate_is_committed_before_a_new_release_advances(self) -> None:
        first_site = make_site(self.root, marker="activated")
        second_site = make_site(self.root, marker="next")
        first_request = request_for(first_site, source_commit="a" * 40)
        second_request = request_for(second_site, source_commit="b" * 40)
        store, failing = self.publisher(fault=FailOnce("after_activated"))
        with self.assertRaisesRegex(RuntimeError, "after_activated"):
            failing.publish(first_request)
        frozen = store.load_candidate(first_request.release_id).publication_time

        _, retry = self.publisher()
        retry.publish(second_request)

        records = store.read_feed()
        self.assertEqual([record["sequence"] for record in records], [1, 2])
        self.assertEqual(records[0]["release_id"], first_request.release_id)
        self.assertEqual(records[0]["publication_time"], frozen)
        self.assertEqual(store.read_current()["release_id"], second_request.release_id)

    def test_prepared_candidate_recovers_from_frozen_snapshot_after_source_is_deleted(self) -> None:
        site = make_site(self.root, marker="frozen-snapshot")
        request = request_for(site, source_commit="2" * 40)
        expected_index = (site / "index.html").read_bytes()
        store, failing = self.publisher(fault=FailOnce("after_prepared"))
        with self.assertRaisesRegex(RuntimeError, "after_prepared"):
            failing.publish(request)
        candidate = store.load_candidate(request.release_id)
        self.assertTrue(candidate.artifact_snapshot_path.is_dir())
        self.assertTrue(candidate.artifact_snapshot_digest)

        for path in sorted(site.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        site.rmdir()

        _, retry = self.publisher()
        result = retry.publish(request)

        self.assertEqual(result.state.value, "committed")
        self.assertEqual((store.release_path(result.release_id) / "index.html").read_bytes(), expected_index)

    def test_activated_recovery_rechecks_release_before_committing_feed(self) -> None:
        from hwskill.publishing.store import PublishingStoreError

        site = make_site(self.root, marker="activated-tamper")
        request = request_for(site, source_commit="3" * 40)
        store, failing = self.publisher(fault=FailOnce("after_activated"))
        with self.assertRaisesRegex(RuntimeError, "after_activated"):
            failing.publish(request)
        (store.release_path(request.release_id) / "index.html").write_text("tampered", encoding="utf-8")

        _, retry = self.publisher()
        with self.assertRaises(PublishingStoreError):
            retry.publish(request)
        self.assertEqual(store.read_feed(), [])

    def test_prepared_recovery_rederives_and_rejects_tampered_candidate_events(self) -> None:
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore, ReleaseIntegrityError

        mutations = {
            "event_id": "event-" + "f" * 64,
            "subject_revision": "sha256:" + "e" * 64,
        }
        for index, (field, replacement) in enumerate(mutations.items()):
            with self.subTest(field=field):
                case = self.root / f"candidate-event-{index}"
                case.mkdir()
                site = make_site(case, marker=field)
                request = request_for(site, source_commit=f"{index + 3:040x}")
                store = FileReleaseStore(case / "publication")
                failing = Publisher(
                    store,
                    feed_id="hwskill-main",
                    public_base_url="https://skills.example.test",
                    clock=Clock(datetime(2026, 9, 11, 5, 0, tzinfo=UTC)),
                    fault_injector=FailOnce("after_prepared"),
                )
                with self.assertRaisesRegex(RuntimeError, "after_prepared"):
                    failing.publish(request)
                candidate_path = store.candidate_path(request.release_id)
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate["events"][0][field] = replacement
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

                retry = Publisher(
                    FileReleaseStore(case / "publication"),
                    feed_id="hwskill-main",
                    public_base_url="https://skills.example.test",
                    clock=Clock(datetime(2026, 9, 11, 6, 0, tzinfo=UTC)),
                )
                with self.assertRaises(ReleaseIntegrityError):
                    retry.publish(request)
                self.assertEqual(store.read_feed(), [])

    def test_schema_valid_committed_at_tampering_is_rejected_on_recovery(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="committed-at-tamper")
        request = request_for(site, source_commit="2" * 40)
        store, failing = self.publisher(fault=FailOnce("after_log_record"))
        with self.assertRaisesRegex(RuntimeError, "after_log_record"):
            failing.publish(request)
        record_path = store.record_path(1)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["committed_at"] = "2099-01-01T00:00:00Z"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        _, retry = self.publisher()
        with self.assertRaises(ReleaseIntegrityError):
            retry.publish(request)
        self.assertEqual(store.read_feed(), [])

    def test_matching_candidate_and_record_committed_at_tampering_is_rejected(self) -> None:
        """Two mutable copies cannot authenticate an otherwise unanchored timestamp."""
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="matching-committed-at-tamper")
        request = request_for(site, source_commit="3" * 40)
        store, failing = self.publisher(fault=FailOnce("after_log_record"))
        with self.assertRaisesRegex(RuntimeError, "after_log_record"):
            failing.publish(request)
        replacement = "2099-01-01T00:00:00Z"
        candidate_path = store.candidate_path(request.release_id)
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate["committed_at"] = replacement
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        record_path = store.record_path(1)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["committed_at"] = replacement
        record_path.write_text(json.dumps(record), encoding="utf-8")

        _, retry = self.publisher()
        with self.assertRaises(ReleaseIntegrityError):
            retry.publish(request)
        self.assertEqual(store.read_feed(), [])

    def test_missing_skill_requires_an_explicit_withdrawn_tombstone(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        active = make_site(self.root, marker="skill-present")
        missing = make_site(self.root, marker="skill-missing")
        catalog_path = missing / "data/catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["entries"] = []
        catalog["source_commit"] = "5" * 40
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        recommendations_path = missing / "data/recommendations.json"
        recommendations = json.loads(recommendations_path.read_text(encoding="utf-8"))
        recommendations["recommendations"] = []
        recommendations_path.write_text(json.dumps(recommendations), encoding="utf-8")
        store, publisher = self.publisher()
        publisher.publish(request_for(active, source_commit="4" * 40))

        with self.assertRaisesRegex(ReleaseIntegrityError, "withdrawn tombstone"):
            publisher.publish(request_for(missing, source_commit="5" * 40))
        self.assertEqual(len(store.read_feed()), 1)

    def test_recommendation_withdrawal_requires_tombstone_and_id_cannot_be_republished(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        ready = make_site(self.root, marker="recommendation-ready-explicit")
        missing = make_site(self.root, include_recommendation=False, marker="recommendation-missing")
        draft = make_site(self.root, recommendation_status="draft", marker="recommendation-draft")
        withdrawn = make_site(self.root, recommendation_status="withdrawn", marker="recommendation-tombstone")
        republished = make_site(self.root, marker="recommendation-republished")
        store, publisher = self.publisher()
        publisher.publish(request_for(ready, source_commit="6" * 40))

        for index, invalid in enumerate((missing, draft), start=7):
            with self.subTest(status="missing" if invalid is missing else "draft"):
                with self.assertRaisesRegex(ReleaseIntegrityError, "withdrawn tombstone|draft"):
                    publisher.publish(request_for(invalid, source_commit=f"{index:040x}"))

        withdrawn_result = publisher.publish(request_for(withdrawn, source_commit="8" * 40))
        withdrawn_record = next(
            record for record in store.read_feed() if record["release_id"] == withdrawn_result.release_id
        )
        self.assertEqual(
            [event["type"] for event in withdrawn_record["events"] if event["subject_id"] == "example-workflow"],
            ["recommendation.withdrawn"],
        )
        with self.assertRaisesRegex(ReleaseIntegrityError, "new recommendation ID"):
            publisher.publish(request_for(republished, source_commit="9" * 40))

    def test_conflicting_pending_candidates_are_rejected_before_any_advances(self) -> None:
        from hwskill.publishing.store import PublishingStoreError

        first_site = make_site(self.root, marker="pending-a")
        second_site = make_site(self.root, marker="pending-b")
        first_request = request_for(first_site, source_commit="a" * 40)
        second_request = request_for(second_site, source_commit="b" * 40)
        store, failing = self.publisher(fault=FailOnce("after_prepared"))
        with self.assertRaisesRegex(RuntimeError, "after_prepared"):
            failing.publish(first_request)
        first = store.load_candidate(first_request.release_id).to_dict()
        first["release_id"] = second_request.release_id
        store.candidate_path(second_request.release_id).write_text(json.dumps(first), encoding="utf-8")

        _, retry = self.publisher()
        with self.assertRaises(PublishingStoreError):
            retry.publish(second_request)
        self.assertIsNone(store.read_current())
        self.assertEqual(store.read_feed(), [])

    def test_activate_rejects_same_sequence_for_a_different_release(self) -> None:
        from hwskill.publishing.store import StaleReleaseError

        site = make_site(self.root, marker="activation-collision")
        store, publisher = self.publisher()
        result = publisher.publish(request_for(site, source_commit="c" * 40))
        other_release = "release-" + "d" * 64

        with self.assertRaises(StaleReleaseError):
            store.activate(other_release, result.sequence)
        self.assertEqual(store.read_current()["release_id"], result.release_id)

    def test_committed_reuse_and_rollback_revalidate_immutable_release(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        first_site = make_site(self.root, marker="history-recheck-old")
        second_site = make_site(self.root, marker="history-recheck-new")
        first_request = request_for(first_site, source_commit="d" * 40)
        second_request = request_for(second_site, source_commit="e" * 40)
        store, publisher = self.publisher()
        first = publisher.publish(first_request)
        second = publisher.publish(second_request)
        (store.release_path(first.release_id) / "index.html").write_text("tampered", encoding="utf-8")

        with self.assertRaises(ReleaseIntegrityError):
            publisher.publish(first_request)
        with self.assertRaises(ReleaseIntegrityError):
            publisher.rollback(first.release_id)
        self.assertEqual(store.read_current()["release_id"], second.release_id)

    def test_new_publish_rejects_schema_valid_tampering_in_immutable_history(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        first_site = make_site(self.root, marker="history-event-a")
        second_site = make_site(self.root, marker="history-event-b")
        store, publisher = self.publisher()
        publisher.publish(request_for(first_site, source_commit="f" * 40))
        record_path = store.record_path(1)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["events"][0]["event_id"] = "event-schema-valid-tamper"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        with self.assertRaises(ReleaseIntegrityError):
            publisher.publish(request_for(second_site, source_commit="0" * 40))
        self.assertEqual(store.read_feed_head()["sequence"], 1)

    def test_recovery_rejects_a_changed_immutable_log_record(self) -> None:
        from hwskill.publishing.store import ImmutableReleaseError

        site = make_site(self.root, marker="tampered-record")
        request = request_for(site, source_commit="0" * 40)
        store, failing = self.publisher(fault=FailOnce("after_log_record"))
        with self.assertRaisesRegex(RuntimeError, "after_log_record"):
            failing.publish(request)
        record_path = store.record_path(1)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["release_id"] = "release-tampered"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        _, retry = self.publisher()
        with self.assertRaises(ImmutableReleaseError):
            retry.publish(request)
        self.assertEqual(store.read_feed(), [])

    def test_recovery_rejects_a_log_record_that_fails_the_release_schema(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="invalid-record")
        request = request_for(site, source_commit="1" * 40)
        store, failing = self.publisher(fault=FailOnce("after_log_record"))
        with self.assertRaisesRegex(RuntimeError, "after_log_record"):
            failing.publish(request)
        record_path = store.record_path(1)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["committed_at"] = "not-a-date-time"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        _, retry = self.publisher()
        with self.assertRaises(ReleaseIntegrityError):
            retry.publish(request)
        self.assertEqual(store.read_feed(), [])

    def test_republishing_old_release_cannot_overwrite_new_current_pointer(self) -> None:
        first_site = make_site(self.root, marker="old")
        second_site = make_site(self.root, marker="new")
        first_request = request_for(first_site, source_commit="c" * 40)
        second_request = request_for(second_site, source_commit="d" * 40)
        store, publisher = self.publisher()
        publisher.publish(first_request)
        publisher.publish(second_request)

        publisher.publish(first_request)

        self.assertEqual(store.read_current()["release_id"], second_request.release_id)
        self.assertEqual(len(store.read_feed()), 2)

    def test_rollback_changes_entrypoint_but_preserves_feed_history(self) -> None:
        first_site = make_site(self.root, marker="rollback-old")
        second_site = make_site(self.root, marker="rollback-new")
        store, publisher = self.publisher()
        first = publisher.publish(request_for(first_site, source_commit="e" * 40))
        publisher.publish(request_for(second_site, source_commit="f" * 40))
        feed_before = store.read_feed()

        publisher.rollback(first.release_id)

        self.assertEqual(store.read_current()["release_id"], first.release_id)
        self.assertEqual(store.read_feed(), feed_before)
        self.assertEqual(store.read_feed_head()["sequence"], 2)


if __name__ == "__main__":
    unittest.main()
