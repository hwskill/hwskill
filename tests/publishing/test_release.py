from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from contextlib import redirect_stdout


UTC = timezone.utc


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _content_digest(value: object) -> str:
    body = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def make_site(
    root: Path,
    *,
    lifecycle: str = "active",
    recommendation_body: str = "first",
    include_recommendation: bool = True,
    source_kind: str = "hosted",
    source_locator: str = "git",
    source_revision: str | None = None,
    source_requested_ref: str | None = None,
    recommendation_status: str = "ready",
    marker: str = "one",
) -> Path:
    site = root / f"site-{marker}"
    if source_kind == "hosted":
        source = {"kind": "hosted", "path": "skills-src/l2/local/example"}
        source_url = "https://github.com/hwskill/hwskill/blob/HEAD/skills-src/l2/local/example/SKILL.md"
    elif source_locator == "git":
        locator = {
            "type": "git",
            "repository": "https://source.example/example.git",
            "path": "skills/example",
            "file_url": "https://source.example/example/SKILL.md",
        }
        if source_requested_ref is not None:
            locator["ref"] = source_requested_ref
        source = {"kind": "external", "publicity": "public", "locator": locator}
        source_url = locator["file_url"]
    else:
        locator = {"type": "web", "url": "https://source.example/skill"}
        if source_requested_ref is not None:
            locator["version_note"] = source_requested_ref
        source = {"kind": "external", "publicity": "public", "locator": locator}
        source_url = locator["url"]
    normalized_entry = {
        "schema_version": 2,
        "id": "local/example",
        "name": "Example",
        "summary": "Example skill",
        "layer": "l2",
        "purposes": ["debugging"],
        "examples": [{"prompt": "Debug it", "expected_outcome": "Evidence"}],
        "source": source,
        "install": {
            "method": "upstream" if source_kind == "external" else "directory",
            "default_scope": "project",
            "instructions_url": "https://source.example/install",
        },
        "compatibility": {"agents": ["codex"], "systems": ["linux"], "requirements": []},
        "license": {"status": "unknown"},
        "lifecycle": lifecycle,
    }
    if lifecycle != "active":
        normalized_entry["lifecycle_reason"] = "retired"
    entry_digest = _content_digest(normalized_entry)
    entry = {
        "entry": normalized_entry,
        "entry_digest": entry_digest,
        "lifecycle": lifecycle,
        "translation": {
            "body_format": "markdown",
            "body": f"# Example\n\nTranslated body {marker}.",
            "translated_at": "2026-09-20",
            "source_url": source_url,
        },
    }
    recommendation = {
        "schema_version": 1,
        "id": "example-workflow",
        "skills": [{"id": "local/example"}],
        "title": "Example workflow",
        "body": recommendation_body,
        "author": "tests",
        "status": recommendation_status,
    }
    if recommendation_status == "withdrawn":
        recommendation["withdrawal_reason"] = "No longer recommended."
    _write_json(site / "data/catalog.json", {"schema_version": 2, "source_commit": marker, "entries": [entry]})
    _write_json(
        site / "data/recommendations.json",
        {"schema_version": 1, "recommendations": [recommendation] if include_recommendation else []},
    )
    install = {
        "schema_version": 2,
        "skill_id": "local/example",
        "entry_digest": entry_digest,
        "source": source,
        "install": normalized_entry["install"],
    }
    install["install_digest"] = _content_digest(install)
    _write_json(site / "data/skills/local/example/install.json", install)
    (site / "index.html").write_text(f"<html>{marker}</html>", encoding="utf-8")
    (site / "skills/local/example").mkdir(parents=True)
    (site / "skills/local/example/index.html").write_text(f"<html>skill {marker}</html>", encoding="utf-8")
    if include_recommendation:
        (site / "recommendations/example-workflow").mkdir(parents=True)
        (site / "recommendations/example-workflow/index.html").write_text(
            f"<html>recommendation {marker}</html>", encoding="utf-8"
        )
    return site


def catalog_digest(site: Path) -> str:
    return "sha256:" + hashlib.sha256((site / "data/catalog.json").read_bytes()).hexdigest()


def request_for(site: Path, *, source_commit: str) -> object:
    from hwskill.publishing.models import ReleaseRequest

    catalog_path = site / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["source_commit"] = source_commit
    _write_json(catalog_path, catalog)
    return ReleaseRequest(
        source_commit=source_commit,
        catalog_digest=catalog_digest(site),
        snapshot_digest="sha256:snapshot-v1",
        build_config_identity="astro-7-pagefind-1.5",
        artifact_dir=site,
    )


def rewrite_external_material(
    site: Path,
    *,
    repository: str | None = None,
    source_path: str | None = None,
    requested_ref: str | None = None,
) -> None:
    """Keep every published digest internally consistent around hostile source metadata."""
    catalog_path = site / "data/catalog.json"
    install_path = site / "data/skills/local/example/install.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    install = json.loads(install_path.read_text(encoding="utf-8"))
    source = install["source"]["locator"]
    if repository is not None:
        source["repository"] = repository
    if source_path is not None:
        source["path"] = source_path
    if requested_ref is not None:
        source["ref"] = requested_ref
    item = catalog["entries"][0]
    item["entry"]["source"] = install["source"]
    item["entry_digest"] = _content_digest(item["entry"])
    install["entry_digest"] = item["entry_digest"]
    install["install_digest"] = _content_digest(
        {key: value for key, value in install.items() if key != "install_digest"}
    )
    _write_json(catalog_path, catalog)
    _write_json(install_path, install)


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(minutes=1)
        return result


class PublicationReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def publisher(self, store_name: str, *, clock: Clock | None = None, fault=None):
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore

        store = FileReleaseStore(self.root / store_name)
        publisher = Publisher(
            store,
            feed_id="hwskill-main",
            public_base_url="https://skills.example.test",
            clock=clock or Clock(datetime(2026, 9, 11, 1, 2, tzinfo=UTC)),
            fault_injector=fault,
        )
        return store, publisher

    def test_same_inputs_have_stable_release_and_event_ids(self) -> None:
        site = make_site(self.root, marker="stable")
        first_store, first = self.publisher("first", clock=Clock(datetime(2026, 9, 11, tzinfo=UTC)))
        second_store, second = self.publisher("second", clock=Clock(datetime(2027, 1, 1, tzinfo=UTC)))

        first_result = first.publish(request_for(site, source_commit="a" * 40))
        second_result = second.publish(request_for(site, source_commit="a" * 40))

        self.assertEqual(first_result.release_id, second_result.release_id)
        self.assertEqual(
            [event["event_id"] for event in first_store.read_feed()[0]["events"]],
            [event["event_id"] for event in second_store.read_feed()[0]["events"]],
        )

    def test_new_release_emits_only_v2_catalog_install_record_and_events(self) -> None:
        site = make_site(self.root, marker="v2-contract")
        store, publisher = self.publisher("v2-contract-store")

        result = publisher.publish(request_for(site, source_commit="8" * 40))

        record = store.read_feed()[0]
        self.assertEqual(record["schema_version"], 2)
        self.assertTrue(all(event["schema_version"] == 2 for event in record["events"]))
        catalog = json.loads((store.release_path(result.release_id) / "data/catalog.json").read_text())
        install = json.loads(
            (store.release_path(result.release_id) / "data/skills/local/example/install.json").read_text()
        )
        self.assertEqual(catalog["schema_version"], 2)
        self.assertEqual(install["schema_version"], 2)
        for removed in ("source_identity", "verification_summary", "install_capability"):
            self.assertNotIn(removed, catalog["entries"][0])
        for removed in ("resolved_revision", "verification_summary"):
            self.assertNotIn(removed, json.dumps(install, sort_keys=True))

    def test_publication_time_is_frozen_when_prepared_candidate_retries(self) -> None:
        from hwskill.publishing.models import CandidateState

        site = make_site(self.root, marker="retry")
        clock = Clock(datetime(2026, 9, 11, 3, 0, tzinfo=UTC))

        def fail_after_prepare(stage: str) -> None:
            if stage == "after_prepared":
                raise RuntimeError("injected prepare stop")

        store, failing = self.publisher("retry-store", clock=clock, fault=fail_after_prepare)
        request = request_for(site, source_commit="b" * 40)
        with self.assertRaisesRegex(RuntimeError, "injected prepare stop"):
            failing.publish(request)
        candidate = store.load_candidate(request.release_id)
        self.assertEqual(candidate.state, CandidateState.PREPARED)
        frozen_time = candidate.publication_time

        _, retry = self.publisher("retry-store", clock=clock)
        result = retry.publish(request)
        self.assertEqual(result.publication_time, frozen_time)
        self.assertEqual(store.read_feed()[0]["publication_time"], frozen_time)

    def test_release_directory_is_immutable(self) -> None:
        from hwskill.publishing.store import ImmutableReleaseError

        site = make_site(self.root, marker="immutable")
        store, publisher = self.publisher("immutable-store")
        request = request_for(site, source_commit="c" * 40)
        result = publisher.publish(request)
        published_index = store.release_path(result.release_id) / "index.html"
        original = published_index.read_bytes()
        (site / "index.html").write_text("changed without changing the declared inputs", encoding="utf-8")

        with self.assertRaises(ImmutableReleaseError):
            store.upload_release(result.release_id, site)
        self.assertEqual(published_index.read_bytes(), original)

    def test_single_writer_lock_rejects_a_second_publisher(self) -> None:
        from hwskill.publishing.store import PublicationLockedError

        site = make_site(self.root, marker="lock")
        store, publisher = self.publisher("lock-store")
        with store.writer_lock():
            with self.assertRaises(PublicationLockedError):
                publisher.publish(request_for(site, source_commit="d" * 40))

    def test_read_check_detects_corrupted_uploaded_bytes_before_activation(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="read-check")
        request = request_for(site, source_commit="e" * 40)

        def stop_after_upload(stage: str) -> None:
            if stage == "after_uploaded":
                raise RuntimeError("stop after upload")

        store, failing = self.publisher("check-store", fault=stop_after_upload)
        with self.assertRaisesRegex(RuntimeError, "stop after upload"):
            failing.publish(request)
        (store.release_path(request.release_id) / "index.html").write_text("corrupt", encoding="utf-8")

        _, retry = self.publisher("check-store")
        with self.assertRaises(ReleaseIntegrityError):
            retry.publish(request)
        self.assertIsNone(store.read_current())
        self.assertEqual(store.read_feed(), [])

    def test_declared_catalog_digest_must_match_the_published_catalog(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="catalog-binding")
        store, publisher = self.publisher("catalog-binding-store")
        request = replace(request_for(site, source_commit="9" * 40), catalog_digest="sha256:not-the-catalog")

        with self.assertRaises(ReleaseIntegrityError):
            publisher.publish(request)
        self.assertIsNone(store.load_candidate(request.release_id))
        self.assertEqual(store.read_feed(), [])

    def test_catalog_source_commit_must_match_release_request(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="source-commit-binding")
        request = request_for(site, source_commit="1" * 40)
        mismatched = replace(request, source_commit="2" * 40)
        store, publisher = self.publisher("source-commit-binding-store")

        with self.assertRaisesRegex(ReleaseIntegrityError, "source commit"):
            publisher.publish(mismatched)
        self.assertIsNone(store.load_candidate(mismatched.release_id))

    def test_ordinary_recommendation_edit_does_not_emit_an_event(self) -> None:
        first_site = make_site(self.root, recommendation_body="first wording", marker="rec-a")
        second_site = make_site(self.root, recommendation_body="edited wording", marker="rec-b")
        store, publisher = self.publisher("recommendation-store")
        publisher.publish(request_for(first_site, source_commit="f" * 40))
        result = publisher.publish(request_for(second_site, source_commit="1" * 40))

        record = next(item for item in store.read_feed() if item["release_id"] == result.release_id)
        self.assertEqual(record["events"], [])

    def test_external_resolved_revision_is_not_persisted_or_evented(self) -> None:
        first_site = make_site(
            self.root,
            source_kind="external",
            source_revision="1" * 40,
            marker="external-a",
        )
        second_site = make_site(
            self.root,
            source_kind="external",
            source_revision="2" * 40,
            marker="external-b",
        )
        store, publisher = self.publisher("external-revision-store")
        publisher.publish(request_for(first_site, source_commit="5" * 40))
        result = publisher.publish(request_for(second_site, source_commit="6" * 40))

        record = next(item for item in store.read_feed() if item["release_id"] == result.release_id)
        self.assertEqual(
            [event["type"] for event in record["events"] if event["subject_id"] == "local/example"],
            [],
        )

    def test_external_requested_ref_change_emits_skill_updated(self) -> None:
        first_site = make_site(
            self.root,
            source_kind="external",
            source_revision="1" * 40,
            source_requested_ref="v1",
            marker="external-ref-a",
        )
        second_site = make_site(
            self.root,
            source_kind="external",
            source_revision="1" * 40,
            source_requested_ref="v2",
            marker="external-ref-b",
        )
        store, publisher = self.publisher("external-ref-store")
        publisher.publish(request_for(first_site, source_commit="3" * 40))
        result = publisher.publish(request_for(second_site, source_commit="4" * 40))

        record = next(item for item in store.read_feed() if item["release_id"] == result.release_id)
        self.assertEqual(
            [event["type"] for event in record["events"] if event["subject_id"] == "local/example"],
            ["skill.updated"],
        )

    def test_artifact_symlinks_and_non_regular_nodes_are_rejected_without_reading_them(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        cases = []
        outside = self.root / "outside-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        symlink_site = make_site(self.root, marker="unsafe-symlink")
        (symlink_site / "leak.txt").symlink_to(outside)
        cases.append(("symlink", symlink_site))

        fifo_site = make_site(self.root, marker="unsafe-fifo")
        os.mkfifo(fifo_site / "blocking.pipe")
        cases.append(("fifo", fifo_site))

        socket_site = make_site(self.root, marker="unsafe-socket")
        socket_path = socket_site / "publisher.sock"
        os.mknod(socket_path, stat.S_IFSOCK | 0o600)
        cases.append(("socket", socket_site))

        device_site = make_site(self.root, marker="unsafe-device")
        try:
            os.mknod(device_site / "null-device", stat.S_IFCHR | 0o600, os.makedev(1, 3))
        except PermissionError:
            pass
        else:
            cases.append(("device", device_site))

        for index, (kind, site) in enumerate(cases):
            with self.subTest(kind=kind):
                request = request_for(site, source_commit=f"{index + 5:040x}")
                store, publisher = self.publisher(f"unsafe-{kind}-store")
                with self.assertRaises(ReleaseIntegrityError):
                    publisher.publish(request)
                self.assertIsNone(store.load_candidate(request.release_id))
                self.assertFalse(store.release_path(request.release_id).exists())

    def test_store_root_and_managed_subtrees_cannot_be_symlinks(self) -> None:
        from hwskill.publishing.store import FileReleaseStore, ReleaseIntegrityError

        outside = self.root / "outside-store"
        outside.mkdir()
        root_link = self.root / "root-link"
        root_link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ReleaseIntegrityError):
            FileReleaseStore(root_link)

        root = self.root / "unsafe-subtree"
        root.mkdir()
        (root / "candidates").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ReleaseIntegrityError):
            FileReleaseStore(root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_path_apis_reject_invalid_release_ids_and_sequences(self) -> None:
        from hwskill.publishing.store import FileReleaseStore

        store = FileReleaseStore(self.root / "validated-paths")
        invalid_ids = ["../escape", "release-short", "release-" + "g" * 64, "/absolute"]
        for release_id in invalid_ids:
            with self.subTest(release_id=release_id):
                with self.assertRaises(ValueError):
                    store.release_path(release_id)
                with self.assertRaises(ValueError):
                    store.candidate_path(release_id)
        for sequence in [0, -1, True, "1"]:
            with self.subTest(sequence=sequence):
                with self.assertRaises(ValueError):
                    store.record_path(sequence)

    def test_store_namespace_cannot_change_feed_or_public_base_url(self) -> None:
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import PublishingStoreError

        site = make_site(self.root, marker="namespace")
        store, publisher = self.publisher("namespace-store")
        publisher.publish(request_for(site, source_commit="8" * 40))

        with self.assertRaisesRegex(PublishingStoreError, "namespace"):
            Publisher(
                store,
                feed_id="other-feed",
                public_base_url="https://other.example.test",
            ).publish(request_for(site, source_commit="8" * 40))

        other_site = make_site(self.root, marker="namespace-build")
        other_request = replace(
            request_for(other_site, source_commit="b" * 40),
            build_config_identity="different-build-config",
        )
        with self.assertRaisesRegex(PublishingStoreError, "namespace"):
            publisher.publish(other_request)

    def test_public_base_url_must_be_canonical_safe_https_or_local_http(self) -> None:
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore

        store = FileReleaseStore(self.root / "url-store")
        invalid = [
            "http://skills.example.test",
            "https://user:secret@skills.example.test",
            "https://skills.example.test/base?query=yes",
            "https://skills.example.test/base#fragment",
            "https://skills.example.test/a/../b",
            "https://skills.example.test//double",
        ]
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    Publisher(store, feed_id="hwskill-main", public_base_url=url)
        Publisher(store, feed_id="hwskill-main", public_base_url="http://127.0.0.1:8080")

    def test_cli_reports_invalid_store_or_url_as_structured_failure(self) -> None:
        from hwskill.publishing.cli import main

        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--store-root",
                    str(self.root / "cli-invalid"),
                    "--public-base-url",
                    "http://insecure.example.test",
                    "release",
                    "--artifact-dir",
                    str(self.root / "absent"),
                    "--source-commit",
                    "a" * 40,
                    "--catalog-digest",
                    "sha256:catalog",
                    "--snapshot-digest",
                    "sha256:snapshot",
                    "--build-config-identity",
                    "build",
                ]
            )

        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["result"], "error")

    def test_artifact_and_store_must_not_overlap(self) -> None:
        from hwskill.publishing.service import Publisher
        from hwskill.publishing.store import FileReleaseStore, ReleaseIntegrityError

        store = FileReleaseStore(self.root / "overlap-store")
        artifact = make_site(store.root, marker="inside-store")
        publisher = Publisher(store, feed_id="hwskill-main", public_base_url="https://skills.example.test")

        with self.assertRaisesRegex(ReleaseIntegrityError, "overlap"):
            publisher.publish(request_for(artifact, source_commit="c" * 40))

        outer_artifact = make_site(self.root, marker="contains-store")
        nested_store = FileReleaseStore(outer_artifact / "publication")
        nested_publisher = Publisher(
            nested_store,
            feed_id="hwskill-main",
            public_base_url="https://skills.example.test",
        )
        with self.assertRaisesRegex(ReleaseIntegrityError, "overlap"):
            nested_publisher.publish(request_for(outer_artifact, source_commit="d" * 40))

    def test_artifact_machine_json_is_schema_checked_before_activation(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="invalid-machine-json")
        request = request_for(site, source_commit="d" * 40)
        catalog_path = site / "data/catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["unexpected"] = True
        _write_json(catalog_path, catalog)
        request = replace(request, catalog_digest=catalog_digest(site))
        store, publisher = self.publisher("invalid-machine-json-store")

        with self.assertRaisesRegex(ReleaseIntegrityError, "catalog"):
            publisher.publish(request)
        self.assertIsNone(store.read_current())
        self.assertEqual(store.read_feed(), [])

    def test_install_material_is_fully_bound_to_its_catalog_item(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        mutations = {
            "source": lambda install: install["source"].update(identity="external:evil-source"),
            "install": lambda install: install["install"].update(instructions_url="https://evil.example/install"),
            "removed-state": lambda install: install.update(verification_summary={}),
        }
        for index, (name, mutate) in enumerate(mutations.items()):
            with self.subTest(name=name):
                site = make_site(self.root, source_kind="external", marker=f"install-bind-{name}")
                install_path = site / "data/skills/local/example/install.json"
                install = json.loads(install_path.read_text(encoding="utf-8"))
                mutate(install)
                install["install_digest"] = _content_digest(
                    {key: value for key, value in install.items() if key != "install_digest"}
                )
                _write_json(install_path, install)
                store, publisher = self.publisher(f"install-bind-{index}")

                with self.assertRaisesRegex(ReleaseIntegrityError, "install"):
                    publisher.publish(request_for(site, source_commit=f"{index + 3:040x}"))
                self.assertIsNone(store.read_current())

    def test_install_source_rejects_mixed_identity_and_locator_fields(self) -> None:
        """A valid identity must not hide a second, conflicting locator."""
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, source_kind="external", marker="install-mixed-source")
        install_path = site / "data/skills/local/example/install.json"
        install = json.loads(install_path.read_text(encoding="utf-8"))
        install["source"]["identity"] = "git:https://source.example/example.git"
        install["install_digest"] = _content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        _write_json(install_path, install)
        store, publisher = self.publisher("install-mixed-source-store")

        with self.assertRaisesRegex(ReleaseIntegrityError, "source"):
            publisher.publish(request_for(site, source_commit="8" * 40))
        self.assertIsNone(store.read_current())

    def test_install_source_web_variant_is_exact_and_normalized(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        valid = make_site(
            self.root,
            source_kind="external",
            source_locator="web",
            marker="install-web-valid",
        )
        _, publisher = self.publisher("install-web-valid-store")
        self.assertEqual(
            publisher.publish(request_for(valid, source_commit="7" * 40)).state.value,
            "committed",
        )

        invalid = make_site(
            self.root,
            source_kind="external",
            source_locator="web",
            marker="install-web-mixed",
        )
        install_path = invalid / "data/skills/local/example/install.json"
        install = json.loads(install_path.read_text(encoding="utf-8"))
        install["source"].update(repository="https://source.example/repository.git", path="skills/example")
        install["install_digest"] = _content_digest(
            {key: value for key, value in install.items() if key != "install_digest"}
        )
        _write_json(install_path, install)
        store, publisher = self.publisher("install-web-mixed-store")
        with self.assertRaisesRegex(ReleaseIntegrityError, "source"):
            publisher.publish(request_for(invalid, source_commit="9" * 40))
        self.assertIsNone(store.read_current())

    def test_consistent_hostile_external_source_metadata_is_rejected(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        cases = {
            "credentials": {"repository": "https://user:secret@source.example/example.git"},
            "traversal": {"source_path": "skills/../evil"},
            "option": {"requested_ref": "--upload-pack=evil"},
            "whitespace": {"requested_ref": " v1"},
            "control": {"requested_ref": "v1\tother"},
        }
        for index, (name, mutation) in enumerate(cases.items()):
            with self.subTest(name=name):
                site = make_site(self.root, source_kind="external", marker=f"hostile-source-{name}")
                rewrite_external_material(site, **mutation)
                store, publisher = self.publisher(f"hostile-source-{index}")
                with self.assertRaisesRegex(ReleaseIntegrityError, "source|path|ref|URL"):
                    publisher.publish(request_for(site, source_commit=f"{index + 10:040x}"))
                self.assertIsNone(store.read_current())

    def test_consistent_credential_urls_are_rejected_across_public_artifacts(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        for index, field in enumerate(("web", "instructions", "license", "evidence")):
            with self.subTest(field=field):
                site = make_site(
                    self.root,
                    source_kind="external",
                    source_locator="web" if field == "web" else "git",
                    marker=f"credential-{field}",
                )
                catalog_path = site / "data/catalog.json"
                install_path = site / "data/skills/local/example/install.json"
                recommendations_path = site / "data/recommendations.json"
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                install = json.loads(install_path.read_text(encoding="utf-8"))
                item = catalog["entries"][0]
                if field == "web":
                    url = "https://user:secret@source.example/skill"
                    install["source"]["locator"]["url"] = url
                    item["entry"]["source"]["locator"]["url"] = url
                elif field == "instructions":
                    url = "https://user:secret@source.example/install"
                    item["entry"]["install"]["instructions_url"] = url
                    install["install"]["instructions_url"] = url
                elif field == "license":
                    item["entry"]["license"] = {
                        "status": "known",
                        "url": "https://user:secret@source.example/license",
                    }
                else:
                    recommendations = json.loads(recommendations_path.read_text(encoding="utf-8"))
                    recommendations["recommendations"][0]["evidence"] = [
                        {
                            "url": "https://user:secret@source.example/evidence",
                            "observed_at": "2026-09-11T00:00:00Z",
                        }
                    ]
                    _write_json(recommendations_path, recommendations)
                item["entry_digest"] = _content_digest(item["entry"])
                install["entry_digest"] = item["entry_digest"]
                install["install_digest"] = _content_digest(
                    {key: value for key, value in install.items() if key != "install_digest"}
                )
                _write_json(catalog_path, catalog)
                _write_json(install_path, install)
                store, publisher = self.publisher(f"credential-artifact-{index}")
                with self.assertRaisesRegex(ReleaseIntegrityError, "URL"):
                    publisher.publish(request_for(site, source_commit=f"{index + 20:040x}"))
                self.assertIsNone(store.read_current())

    def test_release_validation_never_reopens_machine_json_through_a_path(self) -> None:
        site = make_site(self.root, marker="release-path-race")
        request = request_for(site, source_commit="7" * 40)
        store, publisher = self.publisher("release-path-race-store")
        outside = self.root / "outside-catalog.json"
        catalog_bytes = (site / "data/catalog.json").read_bytes()
        outside.write_bytes(catalog_bytes)
        original_read_text = Path.read_text
        unsafe_reads: list[Path] = []

        def racing_read_text(path: Path, *args, **kwargs):
            text = str(path)
            parts = path.parts
            if text.startswith("/proc/self/fd/") and parts[-2:] == ("data", "catalog.json"):
                descriptor = int(parts[4])
                opened_root = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
                if opened_root.parent == store.releases:
                    actual = opened_root / "data/catalog.json"
                    original = actual.read_bytes()
                    actual.unlink()
                    actual.symlink_to(outside)
                    unsafe_reads.append(actual)
                    try:
                        return original_read_text(path, *args, **kwargs)
                    finally:
                        actual.unlink()
                        actual.write_bytes(original)
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=racing_read_text):
            result = publisher.publish(request)

        self.assertEqual(result.state.value, "committed")
        self.assertEqual(unsafe_reads, [])

    def test_draft_recommendation_cannot_enter_a_checked_release(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, recommendation_status="draft", marker="draft-machine-json")
        store, publisher = self.publisher("draft-machine-json-store")

        with self.assertRaisesRegex(ReleaseIntegrityError, "draft"):
            publisher.publish(request_for(site, source_commit="e" * 40))
        self.assertIsNone(store.read_current())

    def test_status_input_identity_must_match_when_artifact_exposes_it(self) -> None:
        from hwskill.publishing.store import ReleaseIntegrityError

        site = make_site(self.root, marker="input-identity")
        _write_json(
            site / "data/status.json",
            {"schema_version": 1, "input_digest": "different-snapshot", "result": "pass"},
        )
        store, publisher = self.publisher("input-identity-store")

        with self.assertRaisesRegex(ReleaseIntegrityError, "input identity"):
            publisher.publish(request_for(site, source_commit="f" * 40))

    def test_release_publish_race_does_not_replace_competing_destination(self) -> None:
        from hwskill.publishing import store as store_module
        from hwskill.publishing.store import ImmutableReleaseError

        site = make_site(self.root, marker="publish-race")
        request = request_for(site, source_commit="9" * 40)
        store, publisher = self.publisher("publish-race-store")
        real_rename = store_module._rename_noreplace

        def race(dir_fd: int, source: str, destination: str) -> None:
            if Path(os.readlink(f"/proc/self/fd/{dir_fd}")) == store.releases:
                competing = store.release_path(request.release_id)
                competing.mkdir()
                (competing / "owner.txt").write_text("competitor", encoding="utf-8")
            real_rename(dir_fd, source, destination)

        with patch("hwskill.publishing.store._rename_noreplace", side_effect=race):
            with self.assertRaises(ImmutableReleaseError):
                publisher.publish(request)
        self.assertEqual((store.release_path(request.release_id) / "owner.txt").read_text(), "competitor")

    def test_successful_rename_does_not_delete_a_reused_staging_name(self) -> None:
        from hwskill.publishing import store as store_module

        site = make_site(self.root, marker="staging-reuse-success")
        request = request_for(site, source_commit="6" * 40)
        store, publisher = self.publisher("staging-reuse-success-store")
        real_rename = store_module._rename_noreplace
        attacker_paths: list[Path] = []

        def reuse_after_success(dir_fd: int, source: str, destination: str) -> None:
            real_rename(dir_fd, source, destination)
            if Path(os.readlink(f"/proc/self/fd/{dir_fd}")) == store.releases:
                attacker = store.releases / source
                attacker.mkdir()
                (attacker / "owner.txt").write_text("attacker", encoding="utf-8")
                attacker_paths.append(attacker)

        with patch("hwskill.publishing.store._rename_noreplace", side_effect=reuse_after_success):
            publisher.publish(request)

        self.assertEqual(len(attacker_paths), 1)
        self.assertTrue(attacker_paths[0].is_dir())
        self.assertEqual((attacker_paths[0] / "owner.txt").read_text(encoding="utf-8"), "attacker")

    def test_failed_rename_only_cleans_the_original_staging_inode(self) -> None:
        from hwskill.publishing import store as store_module

        site = make_site(self.root, marker="staging-reuse-failure")
        request = request_for(site, source_commit="5" * 40)
        store, publisher = self.publisher("staging-reuse-failure-store")
        real_rename = store_module._rename_noreplace
        attacker_paths: list[Path] = []

        def replace_before_failure(dir_fd: int, source: str, destination: str) -> None:
            if Path(os.readlink(f"/proc/self/fd/{dir_fd}")) != store.releases:
                real_rename(dir_fd, source, destination)
                return
            os.rename(source, source + ".moved", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            attacker = store.releases / source
            attacker.mkdir()
            (attacker / "owner.txt").write_text("attacker", encoding="utf-8")
            attacker_paths.append(attacker)
            raise OSError("injected rename failure")

        with patch("hwskill.publishing.store._rename_noreplace", side_effect=replace_before_failure):
            with self.assertRaisesRegex(OSError, "injected rename failure"):
                publisher.publish(request)

        self.assertEqual(len(attacker_paths), 1)
        self.assertTrue(attacker_paths[0].is_dir())
        self.assertEqual((attacker_paths[0] / "owner.txt").read_text(encoding="utf-8"), "attacker")

    def test_failed_rename_never_rmdirs_a_reusable_staging_name(self) -> None:
        """A pathname check cannot make a later rmdir target the same inode."""
        from hwskill.publishing import store as store_module

        site = make_site(self.root, marker="staging-no-rmdir")
        request = request_for(site, source_commit="4" * 40)
        store, publisher = self.publisher("staging-no-rmdir-store")
        real_rename = store_module._rename_noreplace
        real_rmdir = os.rmdir
        staging_rmdirs: list[str] = []

        def fail_release_rename(dir_fd: int, source: str, destination: str) -> None:
            if Path(os.readlink(f"/proc/self/fd/{dir_fd}")) == store.releases:
                raise OSError("injected rename failure")
            real_rename(dir_fd, source, destination)

        def record_rmdir(path, *args, **kwargs):
            dir_fd = kwargs.get("dir_fd")
            if dir_fd is not None and Path(os.readlink(f"/proc/self/fd/{dir_fd}")) == store.releases:
                staging_rmdirs.append(os.fspath(path))
            return real_rmdir(path, *args, **kwargs)

        with patch("hwskill.publishing.store._rename_noreplace", side_effect=fail_release_rename), patch(
            "hwskill.publishing.store.os.rmdir", side_effect=record_rmdir
        ):
            with self.assertRaisesRegex(OSError, "injected rename failure"):
                publisher.publish(request)

        self.assertEqual(staging_rmdirs, [])

    def test_durable_writes_fsync_files_and_parent_directories(self) -> None:
        site = make_site(self.root, marker="durability")
        request = request_for(site, source_commit="a" * 40)
        store, publisher = self.publisher("durability-store")
        real_fsync = os.fsync
        synced_modes: list[int] = []

        def recording_fsync(fd: int) -> None:
            synced_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with patch("hwskill.publishing.store.os.fsync", side_effect=recording_fsync):
            publisher.publish(request)

        self.assertTrue(any(stat.S_ISREG(mode) for mode in synced_modes))
        self.assertGreaterEqual(sum(stat.S_ISDIR(mode) for mode in synced_modes), 2)

    def test_withdraw_and_restore_emit_distinct_explicit_events(self) -> None:
        active = make_site(self.root, lifecycle="active", marker="life-a")
        withdrawn = make_site(
            self.root,
            lifecycle="withdrawn",
            recommendation_status="withdrawn",
            marker="life-b",
        )
        restored = make_site(
            self.root,
            lifecycle="active",
            recommendation_status="withdrawn",
            marker="life-c",
        )
        store, publisher = self.publisher("lifecycle-store")
        publisher.publish(request_for(active, source_commit="2" * 40))
        publisher.publish(request_for(withdrawn, source_commit="3" * 40))
        publisher.publish(request_for(restored, source_commit="4" * 40))

        skill_events = [
            event
            for record in store.read_feed()
            for event in record["events"]
            if event["subject_id"] == "local/example"
        ]
        self.assertEqual([event["type"] for event in skill_events], ["skill.added", "skill.withdrawn", "skill.updated"])
        self.assertEqual(len({event["event_id"] for event in skill_events}), 3)

    def test_withdrawn_skill_source_changes_do_not_repeat_the_terminal_event(self) -> None:
        active = make_site(
            self.root,
            source_kind="external",
            source_requested_ref="v1",
            marker="terminal-active",
        )
        withdrawn = make_site(
            self.root,
            lifecycle="withdrawn",
            recommendation_status="withdrawn",
            source_kind="external",
            source_requested_ref="v1",
            marker="terminal-withdrawn-v1",
        )
        changed = make_site(
            self.root,
            lifecycle="withdrawn",
            recommendation_status="withdrawn",
            source_kind="external",
            source_requested_ref="v2",
            marker="terminal-withdrawn-v2",
        )
        store, publisher = self.publisher("terminal-withdrawal-store")
        publisher.publish(request_for(active, source_commit="1" * 40))
        publisher.publish(request_for(withdrawn, source_commit="2" * 40))
        third = publisher.publish(request_for(changed, source_commit="3" * 40))

        third_record = next(record for record in store.read_feed() if record["release_id"] == third.release_id)
        self.assertEqual(
            [event for event in third_record["events"] if event["subject_id"] == "local/example"],
            [],
        )

    def test_event_references_resolve_inside_the_immutable_release(self) -> None:
        site = make_site(self.root, marker="references")
        store, publisher = self.publisher("reference-store")
        result = publisher.publish(request_for(site, source_commit="7" * 40))
        prefix = f"/releases/{result.release_id}/"

        for event in store.read_feed()[0]["events"]:
            for field in ("page_url", "machine_url"):
                path = urlsplit(event[field]).path
                self.assertTrue(path.startswith(prefix), event[field])
                relative = path.removeprefix(prefix).rstrip("/")
                target = store.release_path(result.release_id) / relative
                if field == "page_url":
                    target /= "index.html"
                self.assertTrue(target.is_file(), event[field])

    def test_recommendation_withdrawal_uses_explicit_tombstone_and_retained_page(self) -> None:
        ready = make_site(self.root, marker="recommendation-ready")
        withdrawn = make_site(
            self.root,
            recommendation_status="withdrawn",
            marker="recommendation-withdrawn",
        )
        store, publisher = self.publisher("recommendation-lifecycle-store")
        first = publisher.publish(request_for(ready, source_commit="8" * 40))
        second = publisher.publish(request_for(withdrawn, source_commit="9" * 40))

        events = [
            event
            for record in store.read_feed()
            for event in record["events"]
            if event["subject_id"] == "example-workflow"
        ]
        self.assertEqual(
            [event["type"] for event in events],
            ["recommendation.published", "recommendation.withdrawn"],
        )
        self.assertEqual(len({event["event_id"] for event in events}), 2)
        self.assertNotEqual(first.release_id, second.release_id)
        for field in ("page_url", "machine_url"):
            self.assertIn(f"/releases/{second.release_id}/", events[1][field])
        page_path = urlsplit(events[1]["page_url"]).path.removeprefix(
            f"/releases/{second.release_id}/"
        ).rstrip("/")
        self.assertTrue((store.release_path(second.release_id) / page_path / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
