from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from hwskill.directory.entries import (
    _is_safe_external_path,
    _is_safe_requested_ref,
    _normalise_identity_path,
    _normalise_repository,
    _url_is_http,
)
from hwskill.directory.schema import validator_for

from .models import FeedSnapshot


class FeedValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


_SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")
_RECOMMENDATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_RELEASE_ID = re.compile(r"^release-[0-9a-f]{64}$")
_CONTENT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_safe_hosted_locator(value: object) -> bool:
    return (
        isinstance(value, str)
        and not value.startswith("/")
        and "\\" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and value.split("/")[0] == "skills-src"
    )


def stable_digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def content_digest(value: object) -> str:
    body = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def release_data_base(machine_url: str) -> str:
    parsed = urlsplit(machine_url)
    marker = "/data/"
    if marker not in parsed.path:
        raise FeedValidationError("invalid-reference", f"machine URL has no release data path: {machine_url}")
    prefix = parsed.path.rsplit(marker, 1)[0]
    return urlunsplit((parsed.scheme, parsed.netloc, prefix + "/data", "", ""))


def catalog_url(machine_url: str) -> str:
    return release_data_base(machine_url) + "/catalog.json"


def install_url(machine_url: str, skill_id: str) -> str:
    return release_data_base(machine_url) + f"/skills/{skill_id}/install.json"


def _document(snapshot: FeedSnapshot, url: str) -> Mapping[str, Any]:
    document = snapshot.documents.get(url)
    if not isinstance(document, Mapping):
        raise FeedValidationError("missing-document", f"snapshot lacks {url}")
    return document


def skill_item(snapshot: FeedSnapshot, event: Mapping[str, Any], skill_id: str) -> Mapping[str, Any]:
    catalog = _document(snapshot, catalog_url(str(event["machine_url"])))
    for item in catalog.get("entries", []):
        if isinstance(item, Mapping) and item.get("entry", {}).get("id") == skill_id:
            return item
    raise FeedValidationError("missing-subject", f"catalog lacks skill {skill_id}")


def recommendation_item(snapshot: FeedSnapshot, event: Mapping[str, Any]) -> Mapping[str, Any]:
    document = _document(snapshot, str(event["machine_url"]))
    for item in document.get("recommendations", []):
        if isinstance(item, Mapping) and item.get("id") == event["subject_id"]:
            return item
    raise FeedValidationError("missing-subject", f"recommendation document lacks {event['subject_id']}")


def skill_semantic(item: Mapping[str, Any]) -> dict[str, Any]:
    entry = item["entry"]
    identity = item.get("source_identity", {})
    semantic = {
        "source": entry.get("source"),
        "source_revision": identity.get("resolved_revision") if identity.get("kind") == "external" else None,
        "install": entry.get("install"),
        "lifecycle": entry.get("lifecycle", item.get("lifecycle", "active")),
        "install_capability": item.get("install_capability"),
    }
    if identity.get("kind") == "external":
        semantic["source_locator"] = identity.get("identity")
        semantic["requested_ref"] = identity.get("requested_ref")
    return semantic


def _validate_subject(snapshot: FeedSnapshot, event: Mapping[str, Any]) -> None:
    event_type = event["type"]
    if event_type.startswith("skill."):
        item = skill_item(snapshot, event, str(event["subject_id"]))
        semantic = skill_semantic(item)
        expected_value: object = semantic
        if event_type == "skill.withdrawn" and semantic["lifecycle"] != "withdrawn":
            raise FeedValidationError("invalid-transition", "withdrawn skill needs withdrawn catalog material")
        if event_type != "skill.withdrawn" and semantic["lifecycle"] == "withdrawn":
            raise FeedValidationError("invalid-transition", "withdrawn catalog material requires skill.withdrawn")
        install = _document(snapshot, str(event["machine_url"]))
        if install.get("skill_id") != event["subject_id"] or install.get("entry_digest") != item.get("entry_digest"):
            raise FeedValidationError("digest-mismatch", f"install material is not bound to {event['subject_id']}")
    else:
        recommendation = recommendation_item(snapshot, event)
        if event_type == "recommendation.withdrawn":
            if recommendation.get("status") != "withdrawn":
                raise FeedValidationError("invalid-transition", "withdrawn recommendation needs a tombstone")
            expected_value = {"id": event["subject_id"], "status": "withdrawn"}
        else:
            if recommendation.get("status") != "ready":
                raise FeedValidationError("invalid-transition", "published recommendation must be ready")
            expected_value = {
                "id": event["subject_id"],
                "skills": recommendation.get("skills", []),
                "status": "ready",
            }
        skill_ids = [reference.get("id") for reference in recommendation.get("skills", [])]
        if skill_ids != list(event["skill_ids"]):
            raise FeedValidationError("digest-mismatch", "recommendation skill references differ from its event")
        for skill_id in event["skill_ids"]:
            item = skill_item(snapshot, event, skill_id)
            if event_type == "recommendation.published" and (
                item["entry"].get("lifecycle") != "active"
                or item.get("lifecycle") != "active"
            ):
                raise FeedValidationError(
                    "invalid-transition",
                    f"recommendation {event['subject_id']} requires active skill {skill_id}",
                )
            install = _document(snapshot, install_url(str(event["machine_url"]), skill_id))
            if install.get("skill_id") != skill_id or install.get("entry_digest") != item.get("entry_digest"):
                raise FeedValidationError("digest-mismatch", f"install material is not bound to {skill_id}")
    if stable_digest(expected_value) != event["subject_revision"]:
        raise FeedValidationError("digest-mismatch", f"subject revision differs for {event['event_id']}")


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (parsed.hostname or "").lower(), port


def _validate_event_reference(snapshot: FeedSnapshot, event: Mapping[str, Any]) -> None:
    event_type = event["type"]
    subject_id = event["subject_id"]
    skill_ids = event["skill_ids"]
    if not all(isinstance(skill_id, str) and _SKILL_ID.fullmatch(skill_id) for skill_id in skill_ids):
        raise FeedValidationError("invalid-subject", f"event {event['event_id']} has an unsafe skill ID")
    release_id = event["release_id"]
    if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
        raise FeedValidationError("invalid-reference", f"event {event['event_id']} has an unsafe release ID")
    try:
        feed = urlsplit(snapshot.feed_url)
        feed_origin = _origin(snapshot.feed_url)
    except ValueError as exc:
        raise FeedValidationError("invalid-reference", "snapshot feed URL is invalid") from exc
    feed_path = feed.path.rstrip("/")
    if not feed_path.endswith("/feed"):
        raise FeedValidationError("invalid-reference", "snapshot feed URL must end in /feed")
    release_prefix = feed_path.removesuffix("/feed") + f"/releases/{release_id}"
    if event_type.startswith("skill."):
        if not isinstance(subject_id, str) or not _SKILL_ID.fullmatch(subject_id) or skill_ids != [subject_id]:
            raise FeedValidationError("invalid-subject", f"event {event['event_id']} has an invalid skill subject")
        expected_suffix = f"/data/skills/{subject_id}/install.json"
        expected_page = f"{release_prefix}/skills/{subject_id}/"
    else:
        if not isinstance(subject_id, str) or not _RECOMMENDATION_ID.fullmatch(subject_id):
            raise FeedValidationError("invalid-subject", f"event {event['event_id']} has an invalid recommendation subject")
        expected_suffix = "/data/recommendations.json"
        expected_page = f"{release_prefix}/recommendations/{subject_id}/"
    expected_machine = release_prefix + expected_suffix
    for field, expected_path in (("machine_url", expected_machine), ("page_url", expected_page)):
        value = event[field]
        try:
            parsed = urlsplit(value)
            safe = (
                parsed.scheme.lower() in {"http", "https"}
                and parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and _origin(value) == feed_origin
                and parsed.path == expected_path
            )
        except (TypeError, ValueError):
            safe = False
        if not safe:
            raise FeedValidationError("invalid-reference", f"event {event['event_id']} {field} is outside its release")


def _expected_event_id(event: Mapping[str, Any], previous_event_id: str | None) -> str:
    digest = stable_digest(
        {
            "feed_id": event["feed_id"],
            "type": event["type"],
            "subject_id": event["subject_id"],
            "subject_revision": event["subject_revision"],
            "previous_event_id": previous_event_id,
        }
    )
    return "event-" + digest.removeprefix("sha256:")


def _validate_event_transition(
    event: Mapping[str, Any],
    previous_type: str | None,
    previous_revision: str | None,
) -> None:
    event_type = event["type"]
    if previous_revision == event["subject_revision"]:
        raise FeedValidationError("duplicate-event", f"event {event['event_id']} repeats the prior semantic revision")
    if event_type.startswith("recommendation."):
        if previous_type == "recommendation.withdrawn":
            raise FeedValidationError("invalid-transition", "withdrawn recommendation IDs are terminal")
        if previous_type is None and event_type != "recommendation.published":
            raise FeedValidationError("invalid-transition", "recommendation withdrawal requires a prior publication")
        if previous_type == "recommendation.published" and event_type != "recommendation.withdrawn":
            raise FeedValidationError("invalid-transition", "published recommendation can only transition to withdrawn")
    else:
        if previous_type is None and event_type not in {"skill.added", "skill.withdrawn"}:
            raise FeedValidationError("invalid-transition", "first skill event must be added or withdrawn")
        if previous_type == "skill.withdrawn" and event_type != "skill.updated":
            raise FeedValidationError("invalid-transition", "restored skill must use skill.updated")
        if previous_type is not None and previous_type != "skill.withdrawn" and event_type == "skill.added":
            raise FeedValidationError("invalid-transition", "skill.added cannot repeat for the same skill")


def _validate_catalog_documents(snapshot: FeedSnapshot) -> None:
    urls = {
        catalog_url(str(event["machine_url"]))
        for record in snapshot.records
        for event in record["events"]
    }
    for url in sorted(urls):
        document = _document(snapshot, url)
        version = document.get("schema_version")
        if version != 1:
            code = "unknown-major" if isinstance(version, int) and version > 1 else "invalid-document"
            raise FeedValidationError(code, f"catalog {url} has unsupported schema {version}")
        errors = sorted(validator_for("catalog-v1").iter_errors(document), key=lambda error: list(error.absolute_path))
        if errors:
            raise FeedValidationError("invalid-document", f"catalog {url}: {errors[0].message}")
        release_commits = {
            record["source_commit"]
            for record in snapshot.records
            if any(catalog_url(str(event["machine_url"])) == url for event in record["events"])
        }
        if release_commits != {document["source_commit"]}:
            raise FeedValidationError("digest-mismatch", f"catalog {url} source_commit differs from its release record")
        seen_skill_ids: set[str] = set()
        for item in document["entries"]:
            skill_id = item["entry"]["id"]
            if skill_id in seen_skill_ids:
                raise FeedValidationError("invalid-document", f"catalog {url} contains duplicate skill ID {skill_id}")
            seen_skill_ids.add(skill_id)
            if content_digest(item["entry"]) != item["entry_digest"]:
                raise FeedValidationError("digest-mismatch", f"catalog {url} contains a changed entry digest")
            entry = item["entry"]
            if item["lifecycle"] != entry["lifecycle"]:
                raise FeedValidationError("invalid-document", f"catalog {url} lifecycle wrapper differs for {skill_id}")
            if entry["lifecycle"] == "withdrawn" and item["install_capability"] != "disabled":
                raise FeedValidationError("invalid-document", f"catalog {url} withdrawn skill {skill_id} is not disabled")
            for field, value in (
                ("install.instructions_url", entry["install"].get("instructions_url")),
                ("license.url", entry["license"].get("url")),
            ):
                if value is not None and not _url_is_http(value):
                    raise FeedValidationError("invalid-document", f"catalog {url} {field} is unsafe")


def _validate_machine_documents(snapshot: FeedSnapshot) -> None:
    checked_install_urls: set[str] = set()
    checked_recommendation_urls: set[str] = set()
    required_install = {
        "schema_version",
        "skill_id",
        "entry_digest",
        "source",
        "install",
        "verification_summary",
        "install_digest",
    }
    for record in snapshot.records:
        for event in record["events"]:
            machine_url = str(event["machine_url"])
            if event["type"].startswith("recommendation.") and machine_url not in checked_recommendation_urls:
                document = _document(snapshot, machine_url)
                version = document.get("schema_version")
                if type(version) is not int or version != 1:
                    code = "unknown-major" if isinstance(version, int) and version > 1 else "invalid-document"
                    raise FeedValidationError(code, f"recommendations {machine_url} has unsupported schema {version}")
                if set(document) != {"schema_version", "recommendations"} or not isinstance(document["recommendations"], list):
                    raise FeedValidationError("invalid-document", f"recommendations {machine_url} has an invalid wrapper")
                seen_recommendation_ids: set[str] = set()
                for recommendation in document["recommendations"]:
                    errors = sorted(
                        validator_for("recommendation").iter_errors(recommendation),
                        key=lambda error: list(error.absolute_path),
                    )
                    if errors:
                        raise FeedValidationError("invalid-document", f"recommendations {machine_url}: {errors[0].message}")
                    if recommendation["status"] not in {"ready", "withdrawn"}:
                        raise FeedValidationError(
                            "invalid-document",
                            f"published recommendation document {machine_url} contains draft material",
                        )
                    recommendation_id = recommendation["id"]
                    if recommendation_id in seen_recommendation_ids:
                        raise FeedValidationError(
                            "invalid-document",
                            f"recommendations {machine_url} contains duplicate recommendation ID {recommendation_id}",
                        )
                    seen_recommendation_ids.add(recommendation_id)
                    for evidence in recommendation.get("evidence", []):
                        if not _url_is_http(evidence.get("url")):
                            raise FeedValidationError(
                                "invalid-document",
                                f"recommendations {machine_url} evidence URL is unsafe",
                            )
                checked_recommendation_urls.add(machine_url)
            for skill_id in event["skill_ids"]:
                url = install_url(machine_url, skill_id)
                if url in checked_install_urls:
                    continue
                install = _document(snapshot, url)
                version = install.get("schema_version")
                if type(version) is not int or version != 1:
                    code = "unknown-major" if isinstance(version, int) and version > 1 else "invalid-document"
                    raise FeedValidationError(code, f"install document {url} has unsupported schema {version}")
                if not required_install.issubset(install):
                    missing = sorted(required_install.difference(install))
                    raise FeedValidationError("invalid-document", f"install document {url} lacks {missing}")
                if not isinstance(install.get("source"), Mapping) or not isinstance(install.get("install"), Mapping):
                    raise FeedValidationError("invalid-document", f"install document {url} has invalid source/install data")
                if not isinstance(install.get("verification_summary"), Mapping):
                    raise FeedValidationError("invalid-document", f"install document {url} has an invalid verification summary")
                summary = install["verification_summary"]
                if set(summary) != {"metadata", "acquisition", "installation", "behavior"}:
                    raise FeedValidationError("invalid-document", f"install document {url} has incomplete verification stages")
                for stage_name, stage in summary.items():
                    if (
                        not isinstance(stage, Mapping)
                        or set(stage) != {"result", "report_id", "executed_at"}
                        or stage.get("result") not in {"pass", "fail", "blocked", "not_run"}
                        or not (stage.get("report_id") is None or isinstance(stage.get("report_id"), str))
                        or not (stage.get("executed_at") is None or isinstance(stage.get("executed_at"), str))
                    ):
                        raise FeedValidationError("invalid-document", f"install document {url} has invalid {stage_name} verification")
                if set(install) != required_install:
                    raise FeedValidationError("invalid-document", f"install document {url} has unknown fields")
                unsigned = {key: value for key, value in install.items() if key != "install_digest"}
                if content_digest(unsigned) != install["install_digest"]:
                    raise FeedValidationError("digest-mismatch", f"install document {url} has a changed digest")
                item = skill_item(snapshot, event, skill_id)
                entry = item["entry"]
                identity = item["source_identity"]
                source = install["source"]
                if install["skill_id"] != skill_id or install["entry_digest"] != item["entry_digest"]:
                    raise FeedValidationError("digest-mismatch", f"install does not match catalog item {skill_id}")
                if install["install"] != entry["install"]:
                    raise FeedValidationError("digest-mismatch", f"install method does not match catalog item {skill_id}")
                if install["verification_summary"] != item["verification_summary"]:
                    raise FeedValidationError("digest-mismatch", f"install verification summary does not match catalog item {skill_id}")
                instructions_url = install["install"].get("instructions_url")
                if instructions_url is not None and not _url_is_http(instructions_url):
                    raise FeedValidationError("invalid-document", f"install instructions URL is unsafe for {skill_id}")
                if entry["source"].get("kind") != identity.get("kind") or entry["source"].get("identity") != identity.get("identity"):
                    raise FeedValidationError("digest-mismatch", f"catalog source identity is inconsistent for {skill_id}")
                if source.get("kind") != identity.get("kind") or source.get("resolved_revision") != identity.get("resolved_revision"):
                    raise FeedValidationError("digest-mismatch", f"install source does not match catalog identity for {skill_id}")
                if identity.get("kind") == "hosted":
                    if set(source) != {"kind", "path", "resolved_revision"}:
                        raise FeedValidationError("invalid-document", f"hosted install source has unknown fields for {skill_id}")
                    path = source.get("path")
                    release_commit = next(
                        record["source_commit"]
                        for record in snapshot.records
                        if record["release_id"] == event["release_id"]
                    )
                    hosted_matches = (
                        _is_safe_hosted_locator(path)
                        and identity.get("identity") == f"hosted:{_normalise_identity_path(path)}"
                        and identity.get("requested_ref") is None
                        and identity.get("resolved_revision") == release_commit
                        and source.get("resolved_revision") == release_commit
                        and isinstance(identity.get("content_digest"), str)
                        and _CONTENT_DIGEST.fullmatch(identity["content_digest"]) is not None
                    )
                    if not hosted_matches:
                        raise FeedValidationError("digest-mismatch", f"hosted source is unsafe or not bound to its release for {skill_id}")
                elif identity.get("kind") == "external":
                    git_fields = {"kind", "repository", "path", "requested_ref", "resolved_revision"}
                    web_fields = {"kind", "url", "requested_ref", "resolved_revision"}
                    source_fields = set(source)
                    if source_fields not in (git_fields, web_fields):
                        raise FeedValidationError(
                            "invalid-document",
                            f"external install source is not an exact git or web variant for {skill_id}",
                        )
                    try:
                        if source_fields == git_fields:
                            repository = source.get("repository")
                            path = source.get("path")
                            requested_ref = source.get("requested_ref")
                            locator_matches = (
                                _url_is_http(repository)
                                and _is_safe_external_path(path)
                                and _is_safe_requested_ref(requested_ref)
                                and identity.get("requested_ref") == requested_ref
                            )
                            expected_identity = (
                                f"git:{_normalise_repository(repository)}\0{_normalise_identity_path(path)}"
                                if locator_matches
                                else None
                            )
                        else:
                            url = source.get("url")
                            requested_ref = source.get("requested_ref")
                            locator_matches = (
                                _url_is_http(url)
                                and (
                                    requested_ref is None
                                    or isinstance(requested_ref, str) and bool(requested_ref)
                                )
                                and identity.get("requested_ref") == requested_ref
                            )
                            expected_identity = f"web:{_normalise_repository(url)}" if locator_matches else None
                    except (TypeError, ValueError):
                        locator_matches = False
                        expected_identity = None
                    if not locator_matches or identity.get("identity") != expected_identity:
                        raise FeedValidationError("digest-mismatch", f"external install locator is unsafe or does not match catalog for {skill_id}")
                else:
                    raise FeedValidationError("invalid-document", f"unsupported source kind for {skill_id}")
                checked_install_urls.add(url)


def validate_snapshot_v1(snapshot: FeedSnapshot, *, validate_subjects: bool = True) -> FeedSnapshot:
    if type(snapshot.schema_version) is not int or snapshot.schema_version != 1:
        raise FeedValidationError("unknown-major", f"snapshot schema {snapshot.schema_version} is unsupported")
    if not isinstance(snapshot.feed_id, str) or not snapshot.feed_id.strip():
        raise FeedValidationError("feed-id-mismatch", "snapshot needs a stable feed identity")
    if set(snapshot.head) != {"schema_version", "sequence", "release_id"}:
        raise FeedValidationError("invalid-head", "head must contain exactly schema_version, sequence, and release_id")
    head_version = snapshot.head.get("schema_version")
    if type(head_version) is not int or head_version != 1:
        raise FeedValidationError("unknown-major", f"head schema {head_version} is unsupported")
    head_sequence = snapshot.head["sequence"]
    if isinstance(head_sequence, bool) or not isinstance(head_sequence, int):
        raise FeedValidationError("invalid-head", "head sequence must be an integer")
    if head_sequence < 1:
        raise FeedValidationError("invalid-head", "head sequence must be positive")
    if not isinstance(snapshot.head.get("release_id"), str) or not _RELEASE_ID.fullmatch(snapshot.head["release_id"]):
        raise FeedValidationError("invalid-head", "head release_id must be release- followed by 64 lowercase hex characters")
    sequences = [record.get("sequence") for record in snapshot.records]
    if sequences != list(range(1, head_sequence + 1)):
        raise FeedValidationError("sequence-gap", f"expected 1..{head_sequence}, got {sequences}")
    if not snapshot.records or snapshot.records[-1].get("release_id") != snapshot.head.get("release_id"):
        raise FeedValidationError("head-mismatch", "head does not identify the last record")
    event_ids: set[str] = set()
    feed_ids: set[str] = set()
    latest_events: dict[tuple[str, str], tuple[str, str, str]] = {}
    release_ids: set[str] = set()
    for index, record in enumerate(snapshot.records, start=1):
        release_id = record.get("release_id")
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise FeedValidationError("invalid-record", f"record {index} has an unsafe release_id")
        if release_id in release_ids:
            raise FeedValidationError("invalid-record", f"record {index} has a duplicate release_id")
        release_ids.add(release_id)
        version = record.get("schema_version")
        if version != 1:
            code = "unknown-major" if isinstance(version, int) and version > 1 else "invalid-record"
            raise FeedValidationError(code, f"record {index} schema {version} is unsupported")
        errors = sorted(validator_for("release-v1").iter_errors(record), key=lambda error: list(error.absolute_path))
        if errors:
            raise FeedValidationError("invalid-record", f"record {index}: {errors[0].message}")
        expected_previous = None if index == 1 else index - 1
        if record.get("previous_sequence") != expected_previous:
            raise FeedValidationError("sequence-gap", f"record {index} has the wrong previous sequence")
        if [event["event_id"] for event in record["events"]] != sorted(event["event_id"] for event in record["events"]):
            raise FeedValidationError("invalid-record", f"record {index} events are not ID-sorted")
        for event in record["events"]:
            if event["sequence"] != index or event["release_id"] != record["release_id"]:
                raise FeedValidationError("invalid-record", f"event {event['event_id']} is outside its record")
            if event["published_at"] != record["publication_time"]:
                raise FeedValidationError("invalid-record", f"event {event['event_id']} has a different publication time")
            if event["event_id"] in event_ids:
                raise FeedValidationError("duplicate-event", f"event ID {event['event_id']} occurs more than once")
            event_ids.add(event["event_id"])
            feed_ids.add(event["feed_id"])
            _validate_event_reference(snapshot, event)
            family = "skill" if event["type"].startswith("skill.") else "recommendation"
            subject_key = (family, event["subject_id"])
            previous = latest_events.get(subject_key)
            expected_event_id = _expected_event_id(event, previous[0] if previous else None)
            if event["event_id"] != expected_event_id:
                raise FeedValidationError("event-id-mismatch", f"event ID does not match its previous-event chain")
            _validate_event_transition(event, previous[1] if previous else None, previous[2] if previous else None)
            latest_events[subject_key] = (event["event_id"], event["type"], event["subject_revision"])
    if feed_ids and feed_ids != {snapshot.feed_id}:
        raise FeedValidationError("feed-id-mismatch", f"events identify {sorted(feed_ids)}, expected {snapshot.feed_id}")
    if validate_subjects:
        _validate_catalog_documents(snapshot)
        _validate_machine_documents(snapshot)
        for record in snapshot.records:
            for event in record["events"]:
                _validate_subject(snapshot, event)
        expected_identity = stable_digest(
            {"head": snapshot.head, "records": snapshot.records, "documents": snapshot.documents}
        )
        if snapshot.snapshot_identity != expected_identity:
            raise FeedValidationError("digest-mismatch", "snapshot digest-mismatch: snapshot_identity is stale")
    return snapshot
