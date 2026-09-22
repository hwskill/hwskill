from __future__ import annotations

from typing import Mapping

from hwskill.directory.schema import validator_for

from .models import FeedSnapshot
from .v1 import (
    FeedValidationError,
    _RELEASE_ID,
    _document,
    _expected_event_id,
    _validate_event_reference,
    _validate_event_transition,
    catalog_url,
    content_digest,
    install_url,
    recommendation_item,
    skill_item,
    stable_digest,
)


def skill_semantic(item: Mapping[str, Any]) -> dict[str, Any]:
    entry = item["entry"]
    return {
        "source": entry.get("source"),
        "install": entry.get("install"),
        "lifecycle": entry.get("lifecycle", item.get("lifecycle", "active")),
    }


def _first_schema_error(name: str, document: Mapping[str, Any]) -> str | None:
    errors = sorted(validator_for(name).iter_errors(document), key=lambda error: list(error.absolute_path))
    if not errors:
        return None
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def _validate_catalog_documents(snapshot: FeedSnapshot) -> None:
    urls = {
        catalog_url(str(event["machine_url"]))
        for record in snapshot.records
        for event in record["events"]
    }
    for url in sorted(urls):
        catalog = _document(snapshot, url)
        error = _first_schema_error("catalog", catalog)
        if error:
            raise FeedValidationError("invalid-document", f"catalog {url}: {error}")
        release_commits = {
            record["source_commit"]
            for record in snapshot.records
            if any(catalog_url(str(event["machine_url"])) == url for event in record["events"])
        }
        if release_commits != {catalog["source_commit"]}:
            raise FeedValidationError("digest-mismatch", f"catalog {url} source_commit differs from its release record")
        seen: set[str] = set()
        for item in catalog["entries"]:
            entry = item["entry"]
            skill_id = entry["id"]
            if skill_id in seen:
                raise FeedValidationError("invalid-document", f"catalog {url} contains duplicate skill ID {skill_id}")
            seen.add(skill_id)
            entry_error = _first_schema_error("entry", entry)
            if entry_error:
                raise FeedValidationError("invalid-document", f"catalog entry {skill_id}: {entry_error}")
            if content_digest(entry) != item["entry_digest"]:
                raise FeedValidationError("digest-mismatch", f"catalog entry digest differs for {skill_id}")
            if item["lifecycle"] != entry.get("lifecycle", "active"):
                raise FeedValidationError("digest-mismatch", f"catalog lifecycle differs for {skill_id}")


def _validate_install(snapshot: FeedSnapshot, event: Mapping[str, Any], skill_id: str) -> None:
    url = install_url(str(event["machine_url"]), skill_id)
    install = _document(snapshot, url)
    expected_fields = {"schema_version", "skill_id", "entry_digest", "source", "install", "install_digest"}
    if set(install) != expected_fields or install.get("schema_version") != 2:
        raise FeedValidationError("invalid-document", f"install document {url} has an invalid v2 shape")
    item = skill_item(snapshot, event, skill_id)
    entry = item["entry"]
    if install["skill_id"] != skill_id or install["entry_digest"] != item["entry_digest"]:
        raise FeedValidationError("digest-mismatch", f"install material is not bound to {skill_id}")
    if install["source"] != entry["source"] or install["install"] != entry["install"]:
        raise FeedValidationError("digest-mismatch", f"install source or method differs from catalog entry {skill_id}")
    unsigned = {key: value for key, value in install.items() if key != "install_digest"}
    if content_digest(unsigned) != install["install_digest"]:
        raise FeedValidationError("digest-mismatch", f"install document digest differs for {skill_id}")


def _validate_recommendations(snapshot: FeedSnapshot, event: Mapping[str, Any]) -> None:
    document = _document(snapshot, str(event["machine_url"]))
    if set(document) != {"schema_version", "recommendations"} or document.get("schema_version") != 1:
        raise FeedValidationError("invalid-document", "recommendations document has an invalid shape")
    seen: set[str] = set()
    for recommendation in document["recommendations"]:
        error = _first_schema_error("recommendation", recommendation)
        if error:
            raise FeedValidationError("invalid-document", f"recommendation: {error}")
        recommendation_id = recommendation["id"]
        if recommendation_id in seen:
            raise FeedValidationError("invalid-document", f"duplicate recommendation ID {recommendation_id}")
        seen.add(recommendation_id)
        if recommendation["status"] == "draft":
            raise FeedValidationError("invalid-document", f"published release contains draft {recommendation_id}")


def _validate_subject(snapshot: FeedSnapshot, event: Mapping[str, Any]) -> None:
    event_type = event["type"]
    if event_type.startswith("skill."):
        item = skill_item(snapshot, event, str(event["subject_id"]))
        semantic: object = skill_semantic(item)
        lifecycle = semantic["lifecycle"]
        if event_type == "skill.withdrawn" and lifecycle != "withdrawn":
            raise FeedValidationError("invalid-transition", "withdrawn skill needs withdrawn catalog material")
        if event_type != "skill.withdrawn" and lifecycle == "withdrawn":
            raise FeedValidationError("invalid-transition", "withdrawn catalog material requires skill.withdrawn")
        _validate_install(snapshot, event, str(event["subject_id"]))
    else:
        _validate_recommendations(snapshot, event)
        recommendation = recommendation_item(snapshot, event)
        if event_type == "recommendation.withdrawn":
            if recommendation.get("status") != "withdrawn":
                raise FeedValidationError("invalid-transition", "withdrawn recommendation needs a tombstone")
            semantic = {"id": event["subject_id"], "status": "withdrawn"}
        else:
            if recommendation.get("status") != "ready":
                raise FeedValidationError("invalid-transition", "published recommendation must be ready")
            semantic = {"id": event["subject_id"], "skills": recommendation.get("skills", []), "status": "ready"}
        skill_ids = [reference.get("id") for reference in recommendation.get("skills", [])]
        if skill_ids != list(event["skill_ids"]):
            raise FeedValidationError("digest-mismatch", "recommendation skill references differ from its event")
        for skill_id in event["skill_ids"]:
            item = skill_item(snapshot, event, skill_id)
            if event_type == "recommendation.published" and item["entry"].get("lifecycle", "active") != "active":
                raise FeedValidationError("invalid-transition", f"recommendation requires active skill {skill_id}")
            _validate_install(snapshot, event, skill_id)
    if stable_digest(semantic) != event["subject_revision"]:
        raise FeedValidationError("digest-mismatch", f"subject revision differs for {event['event_id']}")


def validate_snapshot_v2(snapshot: FeedSnapshot, *, validate_subjects: bool = True) -> FeedSnapshot:
    if snapshot.schema_version != 2:
        raise FeedValidationError("unknown-major", f"snapshot schema {snapshot.schema_version} is unsupported")
    if not isinstance(snapshot.feed_id, str) or not snapshot.feed_id.strip():
        raise FeedValidationError("feed-id-mismatch", "snapshot needs a stable feed identity")
    if set(snapshot.head) != {"schema_version", "sequence", "release_id"} or snapshot.head.get("schema_version") != 2:
        raise FeedValidationError("invalid-head", "v2 head must contain exactly schema_version, sequence, and release_id")
    head_sequence = snapshot.head.get("sequence")
    if isinstance(head_sequence, bool) or not isinstance(head_sequence, int) or head_sequence < 1:
        raise FeedValidationError("invalid-head", "head sequence must be a positive integer")
    if not isinstance(snapshot.head.get("release_id"), str) or not _RELEASE_ID.fullmatch(snapshot.head["release_id"]):
        raise FeedValidationError("invalid-head", "head release_id is invalid")
    if [record.get("sequence") for record in snapshot.records] != list(range(1, head_sequence + 1)):
        raise FeedValidationError("sequence-gap", "snapshot records are not contiguous")
    if not snapshot.records or snapshot.records[-1].get("release_id") != snapshot.head["release_id"]:
        raise FeedValidationError("head-mismatch", "head does not identify the last record")
    event_ids: set[str] = set()
    feed_ids: set[str] = set()
    latest: dict[tuple[str, str], tuple[str, str, str]] = {}
    release_ids: set[str] = set()
    for index, record in enumerate(snapshot.records, start=1):
        release_id = record.get("release_id")
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id) or release_id in release_ids:
            raise FeedValidationError("invalid-record", f"record {index} has an invalid or duplicate release_id")
        release_ids.add(release_id)
        error = _first_schema_error("release", record)
        if error:
            raise FeedValidationError("invalid-record", f"record {index}: {error}")
        expected_previous = None if index == 1 else index - 1
        if record.get("previous_sequence") != expected_previous:
            raise FeedValidationError("sequence-gap", f"record {index} has the wrong previous sequence")
        if [event["event_id"] for event in record["events"]] != sorted(event["event_id"] for event in record["events"]):
            raise FeedValidationError("invalid-record", f"record {index} events are not ID-sorted")
        for event in record["events"]:
            if event["sequence"] != index or event["release_id"] != release_id or event["published_at"] != record["publication_time"]:
                raise FeedValidationError("invalid-record", f"event {event['event_id']} is outside its record")
            if event["event_id"] in event_ids:
                raise FeedValidationError("duplicate-event", f"event ID {event['event_id']} occurs more than once")
            event_ids.add(event["event_id"])
            feed_ids.add(event["feed_id"])
            _validate_event_reference(snapshot, event)
            family = "skill" if event["type"].startswith("skill.") else "recommendation"
            key = (family, event["subject_id"])
            previous = latest.get(key)
            if event["event_id"] != _expected_event_id(event, previous[0] if previous else None):
                raise FeedValidationError("event-id-mismatch", "event ID does not match its previous-event chain")
            _validate_event_transition(event, previous[1] if previous else None, previous[2] if previous else None)
            latest[key] = (event["event_id"], event["type"], event["subject_revision"])
    if feed_ids and feed_ids != {snapshot.feed_id}:
        raise FeedValidationError("feed-id-mismatch", "events do not match the snapshot feed ID")
    if validate_subjects:
        _validate_catalog_documents(snapshot)
        for record in snapshot.records:
            for event in record["events"]:
                _validate_subject(snapshot, event)
        expected = stable_digest({"head": snapshot.head, "records": snapshot.records, "documents": snapshot.documents})
        if snapshot.snapshot_identity != expected:
            raise FeedValidationError("digest-mismatch", "snapshot_identity is stale")
    return snapshot
