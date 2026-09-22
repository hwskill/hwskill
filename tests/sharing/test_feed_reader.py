from __future__ import annotations

from copy import deepcopy
import json
import math
import hashlib
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request

from tests.sharing.support import mutable_snapshot, server_documents


class BytesResponse:
    def __init__(self, value: bytes):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.value if size < 0 else self.value[:size]


class SequenceOpener:
    def __init__(self, failures):
        self.failures = list(failures)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request.full_url)
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        raise AssertionError("sequence opener requires a terminal response")


class MappingOpener:
    def __init__(self, documents, *, required_token=None):
        self.documents = documents
        self.required_token = required_token
        self.requests = []

    def open(self, request, timeout):
        authorization = request.get_header("Authorization")
        self.requests.append((request.full_url, authorization))
        if self.required_token is not None and authorization != f"Bearer {self.required_token}":
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        path = "/" + request.full_url.split("/", 3)[-1]
        body = self.documents.get(path)
        if body is None:
            raise HTTPError(request.full_url, 404, "missing", {}, None)
        return BytesResponse(body)


class FeedReaderTests(unittest.TestCase):
    def test_public_static_feed_sends_no_authorization(self) -> None:
        from hwskill.sharing.feed_reader import FeedReader

        base = "https://directory.test"
        opener = MappingOpener(server_documents(base))
        snapshot = FeedReader(opener=opener).read_snapshot(f"{base}/feed")

        self.assertEqual(snapshot.feed_id, "hwskill-main")
        self.assertEqual(snapshot.head_sequence, 1)
        self.assertTrue(opener.requests)
        self.assertTrue(all(authorization is None for _, authorization in opener.requests))

    def test_private_feed_requires_and_sends_bearer_authentication(self) -> None:
        from hwskill.sharing.feed_reader import FeedAuthenticationError, FeedReader

        with self.assertRaises(FeedAuthenticationError):
            FeedReader(private=True).read_snapshot("https://directory.test/feed")

        base = "https://directory.test"
        opener = MappingOpener(server_documents(base), required_token="secret")
        snapshot = FeedReader(private=True, token="secret", opener=opener).read_snapshot(f"{base}/feed")

        self.assertEqual(snapshot.head_sequence, 1)
        self.assertTrue(all(authorization == "Bearer secret" for _, authorization in opener.requests))

    def test_cross_origin_and_non_http_redirects_are_rejected(self) -> None:
        from hwskill.sharing.feed_reader import _SafeRedirectHandler

        original = Request(
            "https://private.example/feed/head.json",
            headers={"Authorization": "Bearer secret"},
        )
        for destination in (
            "https://public.example/feed/head.json",
            "ftp://private.example/feed/head.json",
            "http://127.0.0.1/internal.json",
            "https://private.example/internal.json",
        ):
            with self.subTest(destination=destination), self.assertRaises(HTTPError):
                _SafeRedirectHandler().redirect_request(original, None, 302, "Found", {}, destination)

    def test_timeout_and_429_are_retried_with_a_finite_limit(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        timeout_opener = SequenceOpener([socket.timeout("slow"), socket.timeout("slow"), socket.timeout("slow")])
        with self.assertRaises(FeedReadError):
            FeedReader(opener=timeout_opener, max_attempts=3, retry_delay=0.001, sleep=lambda _delay: None).read_snapshot(
                "https://directory.test/feed"
            )
        self.assertEqual(len(timeout_opener.requests), 3)

        rate_limited = SequenceOpener([
            HTTPError("https://directory.test/feed/head.json", 429, "limited", {}, None),
            HTTPError("https://directory.test/feed/head.json", 429, "limited", {}, None),
            HTTPError("https://directory.test/feed/head.json", 429, "limited", {}, None),
        ])
        with self.assertRaises(FeedReadError):
            FeedReader(opener=rate_limited, max_attempts=3, retry_delay=0.001, sleep=lambda _delay: None).read_snapshot(
                "https://directory.test/feed"
            )
        self.assertEqual(len(rate_limited.requests), 3)

    def test_deterministic_http_and_json_errors_are_not_retried(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        missing = SequenceOpener([HTTPError("https://directory.test/feed/head.json", 404, "missing", {}, None)])
        with self.assertRaises(FeedReadError):
            FeedReader(opener=missing, max_attempts=5, retry_delay=0.001, sleep=lambda _delay: None).read_snapshot("https://directory.test/feed")
        self.assertEqual(len(missing.requests), 1)

        invalid_json = MappingOpener({"/feed/head.json": b"not-json"})
        with self.assertRaises(FeedReadError):
            FeedReader(opener=invalid_json, max_attempts=5, retry_delay=0.001, sleep=lambda _delay: None).read_snapshot(
                "https://directory.test/feed"
            )
        self.assertEqual(len(invalid_json.requests), 1)

    def test_unsafe_subject_id_is_rejected_before_any_resource_request(self) -> None:
        from hwskill.sharing.feed_reader import FeedReader
        from hwskill.sharing.validation import FeedValidationError

        base = "https://directory.test"
        payload = mutable_snapshot(base)
        event = payload["records"][0]["events"][0]
        event["subject_id"] = "../../admin"
        event["skill_ids"] = ["../../admin"]
        event["machine_url"] = f"{base}/releases/release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/data/skills/../../admin/install.json"
        documents = server_documents(base)
        documents["/feed/records/00000000000000000001.json"] = json.dumps(
            payload["records"][0]
        ).encode("utf-8")
        opener = MappingOpener(documents)

        with self.assertRaisesRegex(FeedValidationError, "invalid-subject"):
            FeedReader(opener=opener).read_snapshot(f"{base}/feed")

        self.assertEqual(len(opener.requests), 2)

    def test_cross_origin_event_references_are_rejected_before_resource_requests(self) -> None:
        from hwskill.sharing.feed_reader import FeedReader
        from hwskill.sharing.validation import FeedValidationError

        base = "https://directory.test"
        payload = mutable_snapshot(base)
        for event in payload["records"][0]["events"]:
            event["machine_url"] = event["machine_url"].replace(base, "https://169.254.169.254")
        documents = server_documents(base)
        documents["/feed/records/00000000000000000001.json"] = json.dumps(payload["records"][0]).encode()
        opener = MappingOpener(documents)

        with self.assertRaisesRegex(FeedValidationError, "invalid-reference"):
            FeedReader(opener=opener).read_snapshot(f"{base}/feed")
        self.assertEqual(len(opener.requests), 2)

    def test_response_and_feed_work_are_bounded(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        oversized = MappingOpener({"/feed/head.json": b"{" + b" " * 32 + b"}"})
        with self.assertRaisesRegex(FeedReadError, "response exceeds"):
            FeedReader(opener=oversized, max_response_bytes=16).read_snapshot("https://directory.test/feed")

        documents = {"/feed/head.json": json.dumps({"schema_version": 1, "sequence": 3, "release_id": "r"}).encode()}
        too_many = MappingOpener(documents)
        with self.assertRaisesRegex(FeedReadError, "sequence exceeds"):
            FeedReader(opener=too_many, max_records=2).read_snapshot("https://directory.test/feed")
        self.assertEqual(len(too_many.requests), 1)

        base = "https://directory.test"
        event_limited = MappingOpener(server_documents(base))
        with self.assertRaisesRegex(FeedReadError, "event count exceeds"):
            FeedReader(opener=event_limited, max_events=1).read_snapshot(f"{base}/feed")
        self.assertEqual(len(event_limited.requests), 2)

        request_limited = MappingOpener(server_documents(base))
        with self.assertRaisesRegex(FeedReadError, "request limit exceeded"):
            FeedReader(opener=request_limited, max_requests=2).read_snapshot(f"{base}/feed")
        self.assertEqual(len(request_limited.requests), 2)

    def test_snapshot_byte_budget_is_cumulative_and_checked_before_json_parse(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        head = json.dumps({"schema_version": 1, "sequence": 1, "release_id": "release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}).encode()
        invalid_record = b"not-json"
        opener = MappingOpener(
            {
                "/feed/head.json": head,
                "/feed/records/00000000000000000001.json": invalid_record,
            }
        )
        with self.assertRaisesRegex(FeedReadError, "snapshot.*bytes"):
            FeedReader(
                opener=opener,
                max_response_bytes=1024,
                max_snapshot_bytes=len(head) + len(invalid_record) - 1,
            ).read_snapshot("https://directory.test/feed")
        self.assertEqual(len(opener.requests), 2)

    def test_event_limit_stops_during_record_acquisition(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        base = "https://directory.test"
        documents = server_documents(base)
        head = json.loads(documents["/feed/head.json"])
        head.update(sequence=2, release_id="release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        documents["/feed/head.json"] = json.dumps(head).encode()
        documents["/feed/records/00000000000000000002.json"] = json.dumps(
            {"schema_version": 1, "sequence": 2, "release_id": "release-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "events": []}
        ).encode()
        opener = MappingOpener(documents)

        with self.assertRaisesRegex(FeedReadError, "event count exceeds"):
            FeedReader(opener=opener, max_events=1).read_snapshot(f"{base}/feed")
        self.assertEqual(len(opener.requests), 2)

    def test_reader_limits_and_delays_must_be_positive(self) -> None:
        from hwskill.sharing.feed_reader import FeedReader

        for kwargs in (
            {"timeout": 0},
            {"retry_delay": -1},
            {"max_response_bytes": 0},
            {"max_snapshot_bytes": 0},
            {"max_records": 0},
            {"max_events": 0},
            {"max_requests": 0},
            {"max_attempts": 11},
            {"timeout": math.inf},
            {"retry_delay": math.nan},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FeedReader(**kwargs)

    def test_unsafe_feed_urls_are_rejected_before_requests(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        opener = MappingOpener({})
        for url in (
            "ftp://directory.test/feed",
            "https://user:secret@directory.test/feed",
            "https://directory.test/feed?alternate=/internal",
            "https://directory.test/feed#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(FeedReadError):
                FeedReader(opener=opener).read_snapshot(url)
        self.assertEqual(opener.requests, [])

    def test_tls_verification_errors_are_not_retried(self) -> None:
        from hwskill.sharing.feed_reader import FeedReadError, FeedReader

        opener = SequenceOpener([URLError(ssl.SSLCertVerificationError("bad certificate"))])
        with self.assertRaises(FeedReadError):
            FeedReader(opener=opener, max_attempts=5, retry_delay=0.01, sleep=lambda _delay: None).read_snapshot(
                "https://directory.test/feed"
            )
        self.assertEqual(len(opener.requests), 1)

    def test_reads_a_release_committed_by_the_task5_publisher(self) -> None:
        from hwskill.publishing.models import ReleaseRequest
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore
        from hwskill.sharing.feed_reader import FeedReader

        from tests.publishing.test_release import make_site, request_for

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = make_site(root, marker="reader-integration")
            request = request_for(artifact, source_commit="1" * 40)
            store = FileReleaseStore(root / "published")
            Publisher(store, feed_id="hwskill-main", public_base_url="https://directory.test").publish(request)
            documents = {
                "/" + str(path.relative_to(store.root)): path.read_bytes()
                for path in store.root.rglob("*.json")
                if "/.publishing/" not in str(path)
            }
            snapshot = FeedReader(opener=MappingOpener(documents)).read_snapshot("https://directory.test/feed")

        self.assertEqual(snapshot.head_sequence, 1)
        self.assertEqual(len(snapshot.records[0]["events"]), 2)

    def test_v2_active_skill_update_is_filtered_without_capability_state(self) -> None:
        from hwskill.publishing.models import ReleaseRequest
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore
        from hwskill.sharing.feed_reader import FeedReader
        from hwskill.sharing.filtering import build_items

        from tests.publishing.test_release import make_site, request_for

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = make_site(root, source_kind="external", source_requested_ref="v1", marker="reader-v2-first")
            second = make_site(root, source_kind="external", source_requested_ref="v2", marker="reader-v2-second")
            available = request_for(first, source_commit="1" * 40)
            unavailable = request_for(second, source_commit="2" * 40)
            store = FileReleaseStore(root / "published")
            publisher = Publisher(store, feed_id="hwskill-main", public_base_url="https://directory.test")
            publisher.publish(available)
            publisher.publish(unavailable)
            documents = {
                "/" + str(path.relative_to(store.root)): path.read_bytes()
                for path in store.root.rglob("*.json")
                if "/.publishing/" not in str(path)
            }
            snapshot = FeedReader(opener=MappingOpener(documents)).read_snapshot("https://directory.test/feed")

        self.assertEqual(snapshot.head_sequence, 2)
        items = build_items(snapshot, from_sequence=1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].change_type, "skill.updated")
        self.assertEqual(items[0].lifecycle, "active")
        self.assertNotIn("install_capability", items[0].to_dict())

    def test_reads_a_task2_web_source_published_by_task5(self) -> None:
        from hwskill.directory.catalog import build_repository
        from hwskill.publishing.models import ReleaseRequest
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore
        from hwskill.sharing.feed_reader import FeedReader
        from hwskill.sharing.filtering import build_items

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            shutil.copytree(Path("tests/directory/fixtures/valid"), repository)
            external = repository / "entries/l2/upstream/external.yaml"
            external.write_text(
                external.read_text(encoding="utf-8").replace(
                    "type: git\n    repository: https://example.com/org/repository.git\n"
                    "    path: skills/review\n    file_url: https://example.com/source/SKILL.md\n    ref: v1.2.3",
                    "type: web\n    url: HTTPS://EXAMPLE.COM/tools/review/\n"
                    "    version_note: maintained release page",
                ),
                encoding="utf-8",
            )
            subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(repository), "config", "user.email", "sharing@example.test"], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(repository), "config", "user.name", "Sharing Test"], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
            derived = root / "derived"
            build_repository(repository, derived)
            artifact = root / "artifact"
            shutil.copytree(derived, artifact / "data")
            catalog = json.loads((artifact / "data/catalog.json").read_text(encoding="utf-8"))
            web_item = next(item for item in catalog["entries"] if item["entry"]["id"] == "upstream/external")
            install_path = artifact / "data/skills/upstream/external/install.json"
            install = json.loads(install_path.read_text(encoding="utf-8"))
            self.assertEqual(
                install["source"],
                {
                    "kind": "external",
                    "publicity": "public",
                    "locator": {
                        "type": "web",
                        "url": "HTTPS://EXAMPLE.COM/tools/review/",
                        "version_note": "maintained release page",
                    },
                },
            )
            self.assertNotIn("source_identity", web_item)

            (artifact / "index.html").write_text("<html>fixture</html>", encoding="utf-8")
            for item in catalog["entries"]:
                page = artifact / "skills" / item["entry"]["id"] / "index.html"
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text("<html>skill</html>", encoding="utf-8")
            recommendations = json.loads((artifact / "data/recommendations.json").read_text(encoding="utf-8"))
            for recommendation in recommendations["recommendations"]:
                page = artifact / "recommendations" / recommendation["id"] / "index.html"
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text("<html>recommendation</html>", encoding="utf-8")
            status = json.loads((artifact / "data/status.json").read_text(encoding="utf-8"))
            catalog_bytes = (artifact / "data/catalog.json").read_bytes()
            request = ReleaseRequest(
                source_commit=catalog["source_commit"],
                catalog_digest="sha256:" + hashlib.sha256(catalog_bytes).hexdigest(),
                snapshot_digest=status["input_digest"],
                build_config_identity="sharing-web-integration-v1",
                artifact_dir=artifact,
            )
            store = FileReleaseStore(root / "published")
            Publisher(store, feed_id="hwskill-main", public_base_url="https://directory.test").publish(request)
            documents = {
                "/" + str(path.relative_to(store.root)): path.read_bytes()
                for path in store.root.rglob("*.json")
                if "/.publishing/" not in str(path)
            }
            snapshot = FeedReader(opener=MappingOpener(documents)).read_snapshot("https://directory.test/feed")

        self.assertEqual(snapshot.head_sequence, 1)
        self.assertTrue(
            any(event["subject_id"] == "upstream/external" for event in snapshot.records[0]["events"])
        )
        external_update = next(
            item for item in build_items(snapshot, from_sequence=0)
            if "upstream/external" in item.skill_refs
        )
        self.assertNotIn("source_versions", external_update.to_dict())

    def test_reads_a_task5_recommendation_withdrawal_from_its_own_release(self) -> None:
        from hwskill.publishing.models import ReleaseRequest
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore
        from hwskill.sharing.feed_reader import FeedReader

        with TemporaryDirectory() as temporary:
            from tests.publishing.test_release import make_site, request_for

            root = Path(temporary)
            ready_site = make_site(root, marker="reader-ready")
            withdrawn_site = make_site(root, recommendation_status="withdrawn", marker="reader-withdrawn")
            ready = request_for(ready_site, source_commit="a" * 40)
            withdrawn = request_for(withdrawn_site, source_commit="b" * 40)
            store = FileReleaseStore(root / "published")
            publisher = Publisher(store, feed_id="hwskill-main", public_base_url="https://directory.test")
            publisher.publish(ready)
            withdrawn_release = publisher.publish(withdrawn)
            documents = {
                "/" + str(path.relative_to(store.root)): path.read_bytes()
                for path in store.root.rglob("*.json")
                if "/.publishing/" not in str(path)
            }
            snapshot = FeedReader(opener=MappingOpener(documents)).read_snapshot("https://directory.test/feed")

        withdrawal = next(
            event
            for record in snapshot.records
            for event in record["events"]
            if event["type"] == "recommendation.withdrawn"
        )
        self.assertEqual(withdrawal["release_id"], withdrawn_release.release_id)
        self.assertIn(f"/releases/{withdrawn_release.release_id}/", withdrawal["page_url"])


if __name__ == "__main__":
    unittest.main()
