from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

from tests.sharing.support import clone_release_documents, mutable_snapshot, refresh_snapshot_identity, valid_snapshot


def digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def content_digest(value: object) -> str:
    body = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


class UpdateFilteringTests(unittest.TestCase):
    def test_new_skill_and_its_first_recommendation_merge_into_one_stable_item(self) -> None:
        from hwskill.sharing.filtering import build_items

        snapshot = valid_snapshot()
        first = build_items(snapshot, from_sequence=0)
        second = build_items(snapshot, from_sequence=0)

        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0].change_type, "skill.added+recommendation.published")
        self.assertEqual(len(first[0].event_ids), 2)
        self.assertTrue(all(event_id.startswith("event-") and len(event_id) == 70 for event_id in first[0].event_ids))
        self.assertEqual(first[0].skill_refs, ("acme/alpha",))
        self.assertEqual(first[0].recommendation_refs, ("alpha-guide",))
        self.assertEqual(first[0].source_versions[0].requested_ref, "v1")
        self.assertIsNone(first[0].source_versions[0].resolved_revision)
        self.assertEqual(
            first[0].source_versions[0].to_dict(),
            {"skill_id": "acme/alpha", "requested_ref": "v1", "resolved_revision": None},
        )
        self.assertEqual(first[0].lifecycle, "active")

    def test_merged_change_type_does_not_depend_on_hashed_event_id_order(self) -> None:
        from hwskill.sharing.filtering import build_items
        from hwskill.sharing.models import FeedSnapshot

        payload = mutable_snapshot()
        self.assertEqual(payload["records"][0]["events"][0]["type"], "recommendation.published")

        item = build_items(FeedSnapshot.from_dict(payload), from_sequence=0)[0]

        self.assertEqual(item.change_type, "skill.added+recommendation.published")
        self.assertEqual(len(item.event_ids), 2)

    def test_multi_skill_recommendation_remains_one_topic_item(self) -> None:
        from hwskill.sharing.filtering import build_items
        from hwskill.sharing.models import FeedSnapshot

        payload = mutable_snapshot()
        catalog_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/catalog.json"
        install_url = "https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/beta/install.json"
        beta = deepcopy(payload["documents"][catalog_url]["entries"][0])
        beta["entry"].update(id="acme/beta", name="Beta 性能", summary="分析性能热点。", purposes=["performance"])
        beta_identity = "git:https://source.example/beta.git\0skills/beta"
        beta["source_identity"].update(identity=beta_identity, requested_ref="v1", resolved_revision=None)
        beta["entry"]["source"] = {"kind": "external", "identity": beta_identity}
        beta["entry_digest"] = content_digest(beta["entry"])
        payload["documents"][catalog_url]["entries"].append(beta)
        install = deepcopy(payload["documents"]["https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/acme/alpha/install.json"])
        install.update(skill_id="acme/beta", entry_digest=beta["entry_digest"])
        install["source"].update(
            repository="https://source.example/beta.git",
            path="skills/beta",
            requested_ref="v1",
            resolved_revision=None,
        )
        install["install_digest"] = content_digest({key: value for key, value in install.items() if key != "install_digest"})
        payload["documents"][install_url] = install
        recommendation = payload["documents"]["https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/recommendations.json"]["recommendations"][0]
        recommendation["skills"] = [{"id": "acme/alpha"}, {"id": "acme/beta"}]
        recommendation_event = next(event for event in payload["records"][0]["events"] if event["type"] == "recommendation.published")
        alpha_event = next(event for event in payload["records"][0]["events"] if event["type"] == "skill.added")
        recommendation_event["skill_ids"] = ["acme/alpha", "acme/beta"]
        recommendation_event["subject_revision"] = digest(
            {"id": "alpha-guide", "skills": recommendation["skills"], "status": "ready"}
        )
        beta_event = deepcopy(alpha_event)
        beta_event.update(
            subject_id="acme/beta",
            skill_ids=["acme/beta"],
            page_url="https://directory.test/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/acme/beta/",
            machine_url=install_url,
            subject_revision=digest(
                {
                    "source": {"kind": "external", "identity": beta_identity},
                    "source_locator": beta_identity,
                    "requested_ref": "v1",
                    "source_revision": None,
                    "install": {
                        "method": "upstream",
                        "default_scope": "project",
                        "instructions_url": "https://source.example/alpha",
                    },
                    "lifecycle": "active",
                    "install_capability": "installable",
                }
            ),
        )
        payload["records"][0]["events"] = sorted(
            [alpha_event, beta_event, recommendation_event],
            key=lambda event: event["event_id"],
        )
        refresh_snapshot_identity(payload, refresh_ids=True)

        items = build_items(FeedSnapshot.from_dict(payload), from_sequence=0)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].change_type, "skill.added+recommendation.published")
        self.assertEqual(items[0].skill_refs, ("acme/alpha", "acme/beta"))
        self.assertEqual(len(items[0].install_urls), 2)
        self.assertEqual(
            tuple(version.resolved_revision for version in items[0].source_versions),
            (None, None),
        )

    def test_record_without_events_does_not_promote_an_ordinary_edit(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.filtering import build_items

        payload = mutable_snapshot()
        second = deepcopy(payload["records"][0])
        second.update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", previous_sequence=1)
        second["events"] = []
        payload["records"].append(second)
        payload["head"].update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        refresh_snapshot_identity(payload)

        self.assertEqual(build_items(FeedSnapshot.from_dict(payload), from_sequence=1), [])

    def test_latest_withdrawals_suppress_promotions_and_remain_as_control_changes(self) -> None:
        from hwskill.sharing.models import FeedSnapshot
        from hwskill.sharing.filtering import build_items

        payload = mutable_snapshot()
        clone_release_documents(payload, "release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        second = deepcopy(payload["records"][0])
        second.update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", previous_sequence=1)
        skill_event = deepcopy(next(event for event in second["events"] if event["type"] == "skill.added"))
        catalog_url = "https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/data/catalog.json"
        catalog_item = payload["documents"][catalog_url]["entries"][0]
        catalog_item["entry"]["lifecycle"] = "withdrawn"
        catalog_item["lifecycle"] = "withdrawn"
        catalog_item["install_capability"] = "disabled"
        catalog_item["entry_digest"] = content_digest(catalog_item["entry"])
        install_url = "https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/data/skills/acme/alpha/install.json"
        install = payload["documents"][install_url]
        install["entry_digest"] = catalog_item["entry_digest"]
        install["install_digest"] = content_digest({key: value for key, value in install.items() if key != "install_digest"})
        skill_event.update(
            sequence=2,
            release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            type="skill.withdrawn",
            machine_url=install_url,
            page_url="https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/skills/acme/alpha/",
            subject_revision=digest({
                "source": catalog_item["entry"]["source"],
                "source_locator": catalog_item["source_identity"]["identity"],
                "requested_ref": catalog_item["source_identity"]["requested_ref"],
                "source_revision": None,
                "install": catalog_item["entry"]["install"],
                "lifecycle": "withdrawn",
                "install_capability": "disabled",
            }),
        )
        recommendation_event = deepcopy(next(event for event in second["events"] if event["type"] == "recommendation.published"))
        recommendation_url = "https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/data/recommendations.json"
        recommendation = payload["documents"][recommendation_url]["recommendations"][0]
        recommendation["status"] = "withdrawn"
        recommendation["withdrawal_reason"] = "retired"
        recommendation_event.update(
            sequence=2,
            release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            type="recommendation.withdrawn",
            machine_url=recommendation_url,
            page_url="https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/recommendations/alpha-guide/",
            subject_revision=digest({"id": "alpha-guide", "status": "withdrawn"}),
        )
        second["publication_time"] = "2026-09-12T01:00:00Z"
        second["committed_at"] = "2026-09-12T01:01:00Z"
        skill_event["published_at"] = second["publication_time"]
        recommendation_event["published_at"] = second["publication_time"]
        second["events"] = [skill_event, recommendation_event]
        payload["records"].append(second)
        payload["head"].update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        refresh_snapshot_identity(payload, refresh_ids=True)

        items = build_items(FeedSnapshot.from_dict(payload), from_sequence=0)

        self.assertEqual(
            sorted(item.change_type for item in items),
            ["recommendation.withdrawn", "skill.withdrawn"],
        )
        self.assertTrue(all(item.lifecycle == "withdrawn" for item in items))

    def test_deprecated_skill_update_remains_a_control_change(self) -> None:
        from hwskill.sharing.filtering import build_items
        from hwskill.sharing.models import FeedSnapshot

        payload = mutable_snapshot()
        clone_release_documents(payload, "release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        event = deepcopy(next(event for event in payload["records"][0]["events"] if event["type"] == "skill.added"))
        event.update(
            sequence=2,
            release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            type="skill.updated",
            published_at="2026-09-12T01:00:00Z",
            page_url="https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/skills/acme/alpha/",
            machine_url="https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/data/skills/acme/alpha/install.json",
        )
        catalog_url = "https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/data/catalog.json"
        item = payload["documents"][catalog_url]["entries"][0]
        item["entry"]["lifecycle"] = "deprecated"
        item["lifecycle"] = "deprecated"
        item["entry_digest"] = content_digest(item["entry"])
        install_url = "https://directory.test/releases/release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/data/skills/acme/alpha/install.json"
        install = payload["documents"][install_url]
        install["entry_digest"] = item["entry_digest"]
        install["install_digest"] = content_digest({key: value for key, value in install.items() if key != "install_digest"})
        event["subject_revision"] = digest(
            {
                "source": item["entry"]["source"],
                "source_locator": item["source_identity"]["identity"],
                "requested_ref": item["source_identity"]["requested_ref"],
                "source_revision": None,
                "install": {
                    "method": "upstream",
                    "default_scope": "project",
                    "instructions_url": "https://source.example/alpha",
                },
                "lifecycle": "deprecated",
                "install_capability": "installable",
            }
        )
        second = deepcopy(payload["records"][0])
        second.update(
            sequence=2,
            release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            previous_sequence=1,
            publication_time=event["published_at"],
            committed_at=event["published_at"],
            events=[event],
        )
        payload["records"].append(second)
        payload["head"].update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        refresh_snapshot_identity(payload, refresh_ids=True)

        items = build_items(FeedSnapshot.from_dict(payload), from_sequence=0)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].change_type, "skill.updated")
        self.assertEqual(items[0].lifecycle, "deprecated")

    def test_filters_can_yield_an_explicitly_empty_interval(self) -> None:
        from hwskill.sharing.filtering import build_items
        from hwskill.sharing.models import UpdateFilters

        items = build_items(
            valid_snapshot(),
            from_sequence=0,
            filters=UpdateFilters(purposes=("performance",), change_types=("skill.updated",)),
        )

        self.assertEqual(items, [])

    def test_change_type_filter_accepts_the_exact_merged_item_type(self) -> None:
        from hwskill.sharing.filtering import build_items
        from hwskill.sharing.models import UpdateFilters

        items = build_items(
            valid_snapshot(),
            from_sequence=0,
            filters=UpdateFilters(change_types=("skill.added+recommendation.published",)),
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].change_type, "skill.added+recommendation.published")

    def test_cursor_must_be_an_integer_inside_the_snapshot_interval(self) -> None:
        from hwskill.sharing.filtering import build_items

        snapshot = valid_snapshot()
        for cursor in (True, 0.5, "0", snapshot.head_sequence + 1):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                build_items(snapshot, from_sequence=cursor)


if __name__ == "__main__":
    unittest.main()
