from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests.sharing.support import mutable_snapshot, refresh_snapshot_identity, valid_snapshot


def _rebind_external_git(
    payload: dict,
    *,
    repository: str | None = None,
    path: str | None = None,
    requested_ref: str | None = None,
) -> None:
    """Keep every producer digest/binding valid while changing one locator field."""
    from hwskill.directory.entries import _normalise_identity_path, _normalise_repository
    from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
    from hwskill.publishing.models import stable_digest
    from hwskill.sharing.validation import content_digest

    catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
    install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
    item = payload["documents"][catalog_url]["entries"][0]
    install = payload["documents"][install_url]
    source = install["source"]
    if repository is not None:
        source["repository"] = repository
    if path is not None:
        source["path"] = path
    if requested_ref is not None:
        source["requested_ref"] = requested_ref
    identity = f"git:{_normalise_repository(source['repository'])}\0{_normalise_identity_path(source['path'])}"
    item["entry"]["source"]["identity"] = identity
    item["source_identity"]["identity"] = identity
    item["source_identity"]["requested_ref"] = source["requested_ref"]
    item["entry_digest"] = content_digest(item["entry"])
    install["entry_digest"] = item["entry_digest"]
    install["install_digest"] = content_digest(
        {key: value for key, value in install.items() if key != "install_digest"}
    )
    skill_event = next(
        event for event in payload["records"][0]["events"] if event["subject_id"] == "acme/alpha"
    )
    skill_event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
    refresh_snapshot_identity(payload, refresh_ids=True)


class FeedValidationTests(unittest.TestCase):
    def test_frozen_v1_document_dispatches_to_the_historical_reader(self) -> None:
        from hwskill.sharing.models import SnapshotVersion
        from hwskill.sharing.validation import validate_snapshot

        fixture = Path(__file__).parent / "fixtures" / "valid-snapshot.json"
        document = json.loads(fixture.read_text(encoding="utf-8"))

        self.assertEqual(validate_snapshot(document), SnapshotVersion.V1)

    def test_v2_document_dispatches_without_removed_catalog_state(self) -> None:
        from hwskill.sharing.models import SnapshotVersion
        from hwskill.sharing.validation import validate_snapshot

        fixture = Path(__file__).parent / "fixtures" / "valid-v2-snapshot.json"
        document = json.loads(fixture.read_text(encoding="utf-8"))

        self.assertEqual(validate_snapshot(document), SnapshotVersion.V2)
        item = next(iter(document["documents"].values()))["entries"][0]
        self.assertFalse({"source_identity", "verification_summary", "install_capability"} & set(item))

    def test_frozen_v1_skill_semantic_handles_hosted_and_external_sources(self) -> None:
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.sharing.validation import skill_semantic

        external = mutable_snapshot()["documents"][
            "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        ]["entries"][0]
        hosted = deepcopy(external)
        hosted["entry"]["source"] = {"kind": "hosted", "identity": "hosted:skills/acme/alpha"}
        hosted["source_identity"] = {
            "kind": "hosted",
            "identity": "hosted:skills/acme/alpha",
            "requested_ref": None,
            "resolved_revision": None,
            "content_digest": "sha256:hosted-content",
        }

        self.assertEqual(skill_semantic(external), publisher_skill_semantic(external))
        self.assertEqual(skill_semantic(hosted), publisher_skill_semantic(hosted))

    def test_valid_task5_snapshot_is_accepted(self) -> None:
        from hwskill.sharing.models import SnapshotVersion
        from hwskill.sharing.validation import validate_snapshot

        snapshot = valid_snapshot()
        self.assertEqual(validate_snapshot(snapshot), SnapshotVersion.V1)

    def test_unknown_major_schema_stops_validation(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        payload["records"][0]["schema_version"] = 2
        with self.assertRaisesRegex(FeedValidationError, "unknown-major"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_snapshot_head_and_install_use_strict_v1_shapes(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        for mutation in ("snapshot-bool", "head-extra", "head-bool", "install-bool", "summary-missing-stage"):
            payload = mutable_snapshot()
            install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
            if mutation == "snapshot-bool":
                payload["schema_version"] = True
            elif mutation == "head-extra":
                payload["head"]["unexpected"] = True
            elif mutation == "head-bool":
                payload["head"]["sequence"] = True
            elif mutation == "install-bool":
                payload["documents"][install_url]["schema_version"] = True
            else:
                del payload["documents"][install_url]["verification_summary"]["behavior"]
            install = payload["documents"][install_url]
            install["install_digest"] = content_digest(
                {key: value for key, value in install.items() if key != "install_digest"}
            )
            refresh_snapshot_identity(payload)
            with self.subTest(mutation=mutation), self.assertRaises(FeedValidationError):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_sequence_gap_is_rejected(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        second = deepcopy(payload["records"][0])
        second["sequence"] = 3
        second["release_id"] = "release-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        second["previous_sequence"] = 2
        second["events"] = []
        payload["records"].append(second)
        payload["head"].update(sequence=3, release_id="release-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc")
        with self.assertRaisesRegex(FeedValidationError, "sequence-gap"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_eventless_release_ids_are_strict_and_unique_across_sequences(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        for release_id in ("release-not-a-content-id", "../escape"):
            payload = mutable_snapshot()
            second = deepcopy(payload["records"][0])
            second.update(sequence=2, release_id=release_id, previous_sequence=1, events=[])
            payload["records"].append(second)
            payload["head"].update(sequence=2, release_id=release_id)
            refresh_snapshot_identity(payload)
            with self.subTest(release_id=release_id), self.assertRaisesRegex(FeedValidationError, "release"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

        payload = mutable_snapshot()
        second = deepcopy(payload["records"][0])
        second.update(sequence=2, previous_sequence=1, events=[])
        payload["records"].append(second)
        payload["head"].update(sequence=2)
        refresh_snapshot_identity(payload)
        with self.assertRaisesRegex(FeedValidationError, "duplicate.*release"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_subject_revision_digest_mismatch_is_rejected(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        payload["records"][0]["events"][0]["subject_revision"] = "sha256:not-the-semantic-digest"
        refresh_snapshot_identity(payload, refresh_ids=True)
        with self.assertRaisesRegex(FeedValidationError, "digest-mismatch"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_duplicate_event_id_is_rejected_across_records(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        second = deepcopy(payload["records"][0])
        second.update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", previous_sequence=1)
        second["events"] = [deepcopy(payload["records"][0]["events"][0])]
        second["events"][0].update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        payload["records"].append(second)
        payload["head"].update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        with self.assertRaisesRegex(FeedValidationError, "duplicate-event"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_eventless_feed_still_requires_an_explicit_feed_identity(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        payload["feed_id"] = ""
        payload["records"][0]["events"] = []

        with self.assertRaisesRegex(FeedValidationError, "feed-id-mismatch"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_catalog_with_unknown_major_or_unknown_field_is_rejected(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        unknown_major = mutable_snapshot()
        unknown_major["documents"][catalog_url]["schema_version"] = 2
        with self.assertRaisesRegex(FeedValidationError, "unknown-major"):
            validate_snapshot(FeedSnapshot.from_dict(unknown_major))

        unknown_field = mutable_snapshot()
        unknown_field["documents"][catalog_url]["unexpected"] = True
        with self.assertRaisesRegex(FeedValidationError, "invalid-document"):
            validate_snapshot(FeedSnapshot.from_dict(unknown_field))

    def test_recommendation_and_install_documents_are_schema_checked(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        recommendation_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/recommendations.json"
        invalid_recommendation = mutable_snapshot()
        invalid_recommendation["documents"][recommendation_url]["recommendations"][0]["unexpected"] = True
        with self.assertRaisesRegex(FeedValidationError, "invalid-document"):
            validate_snapshot(FeedSnapshot.from_dict(invalid_recommendation))

        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        incomplete_install = mutable_snapshot()
        del incomplete_install["documents"][install_url]["entry_digest"]
        with self.assertRaisesRegex(FeedValidationError, "invalid-document"):
            validate_snapshot(FeedSnapshot.from_dict(incomplete_install))

    def test_published_recommendation_document_cannot_contain_unpublished_drafts(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        recommendation_url = next(
            url for url in payload["documents"] if url.endswith("/data/recommendations.json")
        )
        recommendations = payload["documents"][recommendation_url]["recommendations"]
        draft = deepcopy(recommendations[0])
        draft.update(id="private-draft", status="draft")
        recommendations.append(draft)
        refresh_snapshot_identity(payload)

        with self.assertRaisesRegex(FeedValidationError, "draft|published recommendation"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_catalog_and_install_content_digests_are_verified(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        changed_catalog = mutable_snapshot()
        changed_catalog["documents"][catalog_url]["entries"][0]["entry"]["summary"] = "tampered"
        with self.assertRaisesRegex(FeedValidationError, "digest-mismatch"):
            validate_snapshot(FeedSnapshot.from_dict(changed_catalog))

        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        changed_install = mutable_snapshot()
        changed_install["documents"][install_url]["source"]["resolved_revision"] = "tampered"
        with self.assertRaisesRegex(FeedValidationError, "digest-mismatch"):
            validate_snapshot(FeedSnapshot.from_dict(changed_install))

    def test_snapshot_identity_is_recomputed_from_canonical_documents(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        recommendation_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/recommendations.json"
        payload["documents"][recommendation_url]["recommendations"][0]["body"] = "tampered after snapshot"

        with self.assertRaisesRegex(FeedValidationError, "snapshot.*digest-mismatch"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_install_material_is_fully_bound_to_catalog(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        payload = mutable_snapshot()
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        install = payload["documents"][install_url]
        install["source"]["resolved_revision"] = "different-revision"
        install["install_digest"] = content_digest({key: value for key, value in install.items() if key != "install_digest"})
        refresh_snapshot_identity(payload)

        with self.assertRaisesRegex(FeedValidationError, "install.*catalog|catalog.*install"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

        for mutation in ("unknown-field", "bad-summary", "source-extra"):
            payload = mutable_snapshot()
            install = payload["documents"][install_url]
            if mutation == "unknown-field":
                install["unexpected"] = True
            elif mutation == "bad-summary":
                install["verification_summary"] = []
            else:
                install["source"]["unexpected"] = True
            install["install_digest"] = content_digest(
                {key: value for key, value in install.items() if key != "install_digest"}
            )
            refresh_snapshot_identity(payload)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(FeedValidationError, "invalid-document"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_install_verification_summary_cannot_exceed_the_catalog_claim(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        payload = mutable_snapshot()
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        install = payload["documents"][install_url]
        install["verification_summary"]["behavior"] = {
            "result": "pass",
            "report_id": "report-invented",
            "executed_at": "2026-09-11T01:00:00Z",
        }
        install["install_digest"] = content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        refresh_snapshot_identity(payload)

        with self.assertRaisesRegex(FeedValidationError, "verification.*catalog"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_catalog_source_commit_is_bound_to_its_release_record(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        payload["documents"][catalog_url]["source_commit"] = "2" * 40
        refresh_snapshot_identity(payload)

        with self.assertRaisesRegex(FeedValidationError, "source_commit.*release"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_catalog_and_recommendation_subjects_are_unique(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        recommendation_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/recommendations.json"
        for document_url, collection in (
            (catalog_url, "entries"),
            (recommendation_url, "recommendations"),
        ):
            payload = mutable_snapshot()
            values = payload["documents"][document_url][collection]
            values.append(deepcopy(values[0]))
            refresh_snapshot_identity(payload)
            with self.subTest(collection=collection), self.assertRaisesRegex(FeedValidationError, "duplicate"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_catalog_lifecycle_wrapper_is_consistent_and_withdrawn_is_disabled(self) -> None:
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.publishing.models import stable_digest
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        for mutation in ("wrapper-mismatch", "withdrawn-installable"):
            payload = mutable_snapshot()
            item = payload["documents"][catalog_url]["entries"][0]
            if mutation == "wrapper-mismatch":
                item["lifecycle"] = "deprecated"
            else:
                item["entry"]["lifecycle"] = "withdrawn"
                item["entry"]["lifecycle_reason"] = "retired"
                item["lifecycle"] = "withdrawn"
                item["install_capability"] = "installable"
                item["entry_digest"] = content_digest(item["entry"])
                install = payload["documents"][install_url]
                install["entry_digest"] = item["entry_digest"]
                install["install_digest"] = content_digest(
                    {key: value for key, value in install.items() if key != "install_digest"}
                )
                event = next(
                    event for event in payload["records"][0]["events"] if event["subject_id"] == "acme/alpha"
                )
                event["type"] = "skill.withdrawn"
                event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
            refresh_snapshot_identity(payload, refresh_ids=True)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(FeedValidationError, "lifecycle|withdrawn"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_ready_recommendation_cannot_reference_a_withdrawn_skill(self) -> None:
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.publishing.models import stable_digest
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        release_id = "release-" + "a" * 64
        catalog_url = f"https://directory.test/releases/{release_id}/data/catalog.json"
        install_url = f"https://directory.test/releases/{release_id}/data/skills/acme/alpha/install.json"
        payload = mutable_snapshot()
        item = payload["documents"][catalog_url]["entries"][0]
        item["entry"]["lifecycle"] = "withdrawn"
        item["entry"]["lifecycle_reason"] = "retired"
        item["lifecycle"] = "withdrawn"
        item["install_capability"] = "disabled"
        item["entry_digest"] = content_digest(item["entry"])
        install = payload["documents"][install_url]
        install["entry_digest"] = item["entry_digest"]
        install["install_digest"] = content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        skill_event = next(event for event in payload["records"][0]["events"] if event["type"].startswith("skill."))
        skill_event["type"] = "skill.withdrawn"
        skill_event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
        refresh_snapshot_identity(payload, refresh_ids=True)

        with self.assertRaisesRegex(FeedValidationError, "recommendation.*active"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_external_web_install_rejects_git_locator_fields(self) -> None:
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.publishing.models import stable_digest
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        payload = mutable_snapshot()
        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        item = payload["documents"][catalog_url]["entries"][0]
        item["entry"]["source"] = {
            "kind": "external",
            "identity": "web:https://source.example/alpha",
        }
        item["source_identity"] = {
            "kind": "external",
            "identity": "web:https://source.example/alpha",
            "requested_ref": "stable page",
            "resolved_revision": None,
            "content_digest": None,
        }
        item["entry_digest"] = content_digest(item["entry"])
        install = payload["documents"][install_url]
        install["entry_digest"] = item["entry_digest"]
        install["source"] = {
            "kind": "external",
            "url": "HTTPS://SOURCE.EXAMPLE/alpha/",
            "requested_ref": "stable page",
            "resolved_revision": None,
            "repository": "https://attacker.example/repository.git",
        }
        install["install_digest"] = content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        skill_event = next(
            event for event in payload["records"][0]["events"] if event["subject_id"] == "acme/alpha"
        )
        skill_event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
        refresh_snapshot_identity(payload, refresh_ids=True)

        with self.assertRaisesRegex(FeedValidationError, "exact git or web variant"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_external_git_install_requires_string_locator_fields(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        payload = mutable_snapshot()
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        install = payload["documents"][install_url]
        install["source"]["path"] = None
        install["install_digest"] = content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        refresh_snapshot_identity(payload)

        with self.assertRaisesRegex(FeedValidationError, "external install locator"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_external_git_security_is_rechecked_after_all_bindings_are_recomputed(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        attacks = (
            {"repository": "https://user:secret@source.example/alpha.git"},
            {"path": "skills/../evil"},
            {"requested_ref": "--upload-pack=evil"},
        )
        for attack in attacks:
            payload = mutable_snapshot()
            _rebind_external_git(payload, **attack)
            with self.subTest(attack=attack), self.assertRaisesRegex(FeedValidationError, "unsafe"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_external_web_security_is_rechecked_after_all_bindings_are_recomputed(self) -> None:
        from hwskill.directory.entries import _normalise_repository
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.publishing.models import stable_digest
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        payload = mutable_snapshot()
        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        item = payload["documents"][catalog_url]["entries"][0]
        url = "https://user:secret@source.example/alpha"
        identity = f"web:{_normalise_repository(url)}"
        item["entry"]["source"] = {"kind": "external", "identity": identity}
        item["source_identity"].update(identity=identity, requested_ref="stable page")
        item["entry_digest"] = content_digest(item["entry"])
        install = payload["documents"][install_url]
        install["entry_digest"] = item["entry_digest"]
        install["source"] = {
            "kind": "external",
            "url": url,
            "requested_ref": "stable page",
            "resolved_revision": None,
        }
        install["install_digest"] = content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        skill_event = next(
            event for event in payload["records"][0]["events"] if event["subject_id"] == "acme/alpha"
        )
        skill_event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
        refresh_snapshot_identity(payload, refresh_ids=True)

        with self.assertRaisesRegex(FeedValidationError, "unsafe"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_hosted_source_path_revision_and_content_are_independently_verified(self) -> None:
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.publishing.models import stable_digest
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        release_id = "release-" + "a" * 64
        catalog_url = f"https://directory.test/releases/{release_id}/data/catalog.json"
        install_url = f"https://directory.test/releases/{release_id}/data/skills/acme/alpha/install.json"
        for attack in ("path", "revision", "content"):
            payload = mutable_snapshot()
            item = payload["documents"][catalog_url]["entries"][0]
            install = payload["documents"][install_url]
            path = "skills-src/../evil" if attack == "path" else "skills-src/l2/manual/alpha"
            revision = "2" * 40 if attack == "revision" else payload["records"][0]["source_commit"]
            item["entry"]["source"] = {"kind": "hosted", "identity": f"hosted:{path}"}
            item["source_identity"] = {
                "kind": "hosted",
                "identity": f"hosted:{path}",
                "requested_ref": None,
                "resolved_revision": revision,
                "content_digest": "invalid" if attack == "content" else "sha256:" + "3" * 64,
            }
            item["entry_digest"] = content_digest(item["entry"])
            install["entry_digest"] = item["entry_digest"]
            install["source"] = {"kind": "hosted", "path": path, "resolved_revision": revision}
            install["install_digest"] = content_digest(
                {key: value for key, value in install.items() if key != "install_digest"}
            )
            skill_event = next(
                event for event in payload["records"][0]["events"] if event["subject_id"] == "acme/alpha"
            )
            skill_event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
            refresh_snapshot_identity(payload, refresh_ids=True)

            with self.subTest(attack=attack), self.assertRaisesRegex(FeedValidationError, "hosted"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_public_document_urls_reject_userinfo_after_digest_rebinding(self) -> None:
        from hwskill.sharing.v1 import skill_semantic as publisher_skill_semantic
        from hwskill.publishing.models import stable_digest
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, content_digest, validate_snapshot

        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
        recommendation_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/recommendations.json"
        for surface in ("instructions", "license", "evidence", "page", "machine"):
            payload = mutable_snapshot()
            item = payload["documents"][catalog_url]["entries"][0]
            install = payload["documents"][install_url]
            skill_event = next(
                event for event in payload["records"][0]["events"] if event["subject_id"] == "acme/alpha"
            )
            if surface == "instructions":
                value = "https://user:secret@source.example/alpha"
                item["entry"]["install"]["instructions_url"] = value
                install["install"]["instructions_url"] = value
            elif surface == "license":
                item["entry"]["license"] = {
                    "status": "known",
                    "url": "https://user:secret@source.example/license",
                }
            elif surface == "evidence":
                payload["documents"][recommendation_url]["recommendations"][0]["evidence"] = [
                    {
                        "url": "https://user:secret@source.example/evidence",
                        "observed_at": "2026-09-11T01:00:00Z",
                    }
                ]
            elif surface == "page":
                skill_event["page_url"] = (
                    "https://user:secret@directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/acme/alpha/"
                )
            else:
                skill_event["machine_url"] = (
                    "https://user:secret@directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"
                )
            if surface in {"instructions", "license"}:
                item["entry_digest"] = content_digest(item["entry"])
                install["entry_digest"] = item["entry_digest"]
                install["install_digest"] = content_digest(
                    {key: value for key, value in install.items() if key != "install_digest"}
                )
                skill_event["subject_revision"] = stable_digest(publisher_skill_semantic(item))
            refresh_snapshot_identity(payload, refresh_ids=True)
            with self.subTest(surface=surface), self.assertRaisesRegex(FeedValidationError, "unsafe|invalid-reference"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_event_id_must_match_the_task5_previous_event_chain(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        payload = mutable_snapshot()
        payload["records"][0]["events"][0]["event_id"] = "event-" + "0" * 64
        payload["records"][0]["events"].sort(key=lambda event: event["event_id"])
        refresh_snapshot_identity(payload)

        with self.assertRaisesRegex(FeedValidationError, "event-id-mismatch"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_skill_withdrawal_requires_withdrawn_catalog_material(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, skill_semantic, stable_digest, validate_snapshot

        payload = mutable_snapshot()
        event = next(event for event in payload["records"][0]["events"] if event["type"] == "skill.added")
        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        semantic = skill_semantic(payload["documents"][catalog_url]["entries"][0])
        event["type"] = "skill.withdrawn"
        event["subject_revision"] = stable_digest({"previous": semantic, "lifecycle": "withdrawn"})
        refresh_snapshot_identity(payload, refresh_ids=True)

        with self.assertRaisesRegex(FeedValidationError, "invalid-transition"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_event_urls_are_bound_to_origin_release_and_subject(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, validate_snapshot

        for field, value in (
            ("machine_url", "https://directory.test/releases/other/data/skills/acme/alpha/install.json"),
            ("page_url", "https://evil.example/phish"),
            ("page_url", "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/acme/alpha/?redirect=1"),
        ):
            payload = mutable_snapshot()
            payload["records"][0]["events"][0][field] = value
            refresh_snapshot_identity(payload)
            with self.subTest(field=field, value=value), self.assertRaisesRegex(FeedValidationError, "invalid-reference"):
                validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_withdrawn_recommendation_requires_tombstone_and_is_terminal(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.validation import FeedValidationError, stable_digest, validate_snapshot

        payload = mutable_snapshot()
        first = payload["records"][0]
        original = next(event for event in first["events"] if event["type"] == "recommendation.published")

        def add_release(sequence: int, release_id: str, event_type: str, status: str) -> None:
            release_ids = {
                1: "release-" + "a" * 64,
                2: "release-" + "b" * 64,
                3: "release-" + "c" * 64,
            }
            previous_release = release_ids[sequence - 1]
            for suffix in ("data/catalog.json", "data/skills/acme/alpha/install.json"):
                old = f"https://directory.test/releases/{previous_release}/{suffix}"
                new = f"https://directory.test/releases/{release_id}/{suffix}"
                payload["documents"][new] = deepcopy(payload["documents"][old])
            old_recommendations = f"https://directory.test/releases/{previous_release}/data/recommendations.json"
            recommendations = deepcopy(payload["documents"][old_recommendations])
            recommendations["recommendations"][0]["status"] = status
            if status == "withdrawn":
                recommendations["recommendations"][0]["withdrawal_reason"] = "retired"
            else:
                recommendations["recommendations"][0].pop("withdrawal_reason", None)
            new_recommendations = f"https://directory.test/releases/{release_id}/data/recommendations.json"
            payload["documents"][new_recommendations] = recommendations
            event = deepcopy(original)
            event.update(
                sequence=sequence,
                release_id=release_id,
                type=event_type,
                published_at=f"2026-09-{10 + sequence:02d}T01:00:00Z",
                page_url=f"https://directory.test/releases/{release_id}/recommendations/alpha-guide/",
                machine_url=new_recommendations,
                subject_revision=stable_digest(
                    {"id": "alpha-guide", "status": "withdrawn"}
                    if status == "withdrawn"
                    else {"id": "alpha-guide", "skills": recommendations["recommendations"][0]["skills"], "status": "ready"}
                ),
            )
            record = deepcopy(first)
            record.update(
                sequence=sequence,
                release_id=release_id,
                previous_sequence=sequence - 1,
                publication_time=event["published_at"],
                committed_at=event["published_at"],
                events=[event],
            )
            payload["records"].append(record)
            payload["head"].update(sequence=sequence, release_id=release_id)

        add_release(2, "release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "recommendation.withdrawn", "withdrawn")
        refresh_snapshot_identity(payload, refresh_ids=True)
        self.assertEqual(
            payload["records"][-1]["events"][0]["type"],
            "recommendation.withdrawn",
        )

        add_release(3, "release-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "recommendation.published", "ready")
        refresh_snapshot_identity(payload, refresh_ids=True)
        with self.assertRaisesRegex(FeedValidationError, "invalid-transition"):
            validate_snapshot(FeedSnapshot.from_dict(payload))

    def test_release_url_derivation_handles_data_in_the_deployment_prefix(self) -> None:
        from hwskill.sharing.models import SnapshotVersion
        from hwskill.sharing.validation import validate_snapshot

        snapshot = valid_snapshot("https://directory.test/tenant/data/application")
        self.assertEqual(validate_snapshot(snapshot), SnapshotVersion.V1)


if __name__ == "__main__":
    unittest.main()
