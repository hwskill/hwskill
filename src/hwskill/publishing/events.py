from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .models import stable_digest
from .store import ReleaseIntegrityError


def _read_json(release: Mapping[str, bytes], relative: str) -> dict[str, Any]:
    return json.loads(release[relative].decode("utf-8"))


def _skills(release: Mapping[str, bytes] | None) -> dict[str, dict[str, Any]]:
    if release is None or "data/catalog.json" not in release:
        return {}
    return {item["entry"]["id"]: item for item in _read_json(release, "data/catalog.json").get("entries", [])}


def _recommendations(release: Mapping[str, bytes] | None) -> dict[str, dict[str, Any]]:
    if release is None or "data/recommendations.json" not in release:
        return {}
    return {
        item["id"]: item
        for item in _read_json(release, "data/recommendations.json").get("recommendations", [])
    }


def _skill_semantic(item: dict[str, Any]) -> dict[str, Any]:
    entry = item["entry"]
    source_identity = item.get("source_identity", {})
    source_revision = (
        source_identity.get("resolved_revision")
        if source_identity.get("kind") == "external"
        else None
    )
    semantic = {
        "source": entry.get("source"),
        "source_revision": source_revision,
        "install": entry.get("install"),
        "lifecycle": entry.get("lifecycle", item.get("lifecycle", "active")),
        "install_capability": item.get("install_capability"),
    }
    if source_identity.get("kind") == "external":
        semantic["source_locator"] = source_identity.get("identity")
        semantic["requested_ref"] = source_identity.get("requested_ref")
    return semantic


def _event_id(feed_id: str, event_type: str, subject_id: str, revision: str, previous_event_id: str | None) -> str:
    digest = stable_digest(
        {
            "feed_id": feed_id,
            "type": event_type,
            "subject_id": subject_id,
            "subject_revision": revision,
            "previous_event_id": previous_event_id,
        }
    )
    return "event-" + digest.removeprefix("sha256:")


def _history(
    records: Iterable[dict[str, Any]],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    seen: set[tuple[str, str]] = set()
    latest: dict[tuple[str, str], str] = {}
    latest_type: dict[tuple[str, str], str] = {}
    for record in records:
        for event in record.get("events", []):
            family = "skill" if event["type"].startswith("skill.") else "recommendation"
            key = (family, event["subject_id"])
            seen.add(key)
            latest[key] = event["event_id"]
            latest_type[key] = event["type"]
    return seen, latest, latest_type


def derive_events(
    *,
    feed_id: str,
    release_id: str,
    sequence: int,
    publication_time: str,
    current_release: Mapping[str, bytes],
    previous_release: Mapping[str, bytes] | None,
    prior_records: Iterable[dict[str, Any]],
    public_base_url: str,
) -> tuple[dict[str, Any], ...]:
    current_skills, previous_skills = _skills(current_release), _skills(previous_release)
    current_recommendations, previous_recommendations = _recommendations(current_release), _recommendations(previous_release)
    seen, latest, latest_type = _history(prior_records)
    events: list[dict[str, Any]] = []

    def add(
        event_type: str,
        subject_id: str,
        revision: str,
        skill_ids: list[str],
        family: str,
    ) -> None:
        key = (family, subject_id)
        event_id = _event_id(feed_id, event_type, subject_id, revision, latest.get(key))
        noun = "skills" if family == "skill" else "recommendations"
        release_base = f"{public_base_url.rstrip('/')}/releases/{release_id}"
        machine_path = (
            f"data/skills/{subject_id}/install.json"
            if family == "skill"
            else "data/recommendations.json"
        )
        events.append(
            {
                "schema_version": 1,
                "feed_id": feed_id,
                "event_id": event_id,
                "sequence": sequence,
                "release_id": release_id,
                "type": event_type,
                "subject_id": subject_id,
                "subject_revision": revision,
                "skill_ids": skill_ids,
                "published_at": publication_time,
                "page_url": f"{release_base}/{noun}/{subject_id}/",
                "machine_url": f"{release_base}/{machine_path}",
            }
        )

    for skill_id in sorted(set(current_skills) | set(previous_skills)):
        current, previous = current_skills.get(skill_id), previous_skills.get(skill_id)
        if current is None:
            raise ReleaseIntegrityError(
                f"skill {skill_id} disappeared; retain it as an explicit lifecycle=withdrawn tombstone"
            )
        current_semantic = _skill_semantic(current)
        current_lifecycle = current_semantic["lifecycle"]
        revision = stable_digest(current_semantic)
        if previous is None:
            if current_lifecycle == "withdrawn":
                event_type = "skill.withdrawn"
            else:
                event_type = "skill.updated" if ("skill", skill_id) in seen else "skill.added"
            add(event_type, skill_id, revision, [skill_id], "skill")
            continue
        previous_semantic = _skill_semantic(previous)
        if previous_semantic["lifecycle"] == "withdrawn" and current_lifecycle == "withdrawn":
            continue
        if current_semantic == previous_semantic:
            continue
        event_type = "skill.withdrawn" if current_lifecycle == "withdrawn" else "skill.updated"
        add(event_type, skill_id, revision, [skill_id], "skill")

    for recommendation_id in sorted(set(current_recommendations) | set(previous_recommendations)):
        current, previous = current_recommendations.get(recommendation_id), previous_recommendations.get(recommendation_id)
        if current is None:
            raise ReleaseIntegrityError(
                f"recommendation {recommendation_id} disappeared; retain an explicit withdrawn tombstone"
            )
        current_status = current.get("status")
        previous_status = previous.get("status") if previous is not None else None
        key = ("recommendation", recommendation_id)
        if current_status == "draft":
            if previous is not None or key in seen:
                raise ReleaseIntegrityError(
                    f"recommendation {recommendation_id} draft cannot replace a published item; retain a withdrawn tombstone"
                )
            continue
        if current_status == "withdrawn":
            if previous_status == "withdrawn" or latest_type.get(key) == "recommendation.withdrawn":
                continue
            if previous_status != "ready" and latest_type.get(key) != "recommendation.published":
                continue
            revision = stable_digest({"id": recommendation_id, "status": "withdrawn"})
            skill_ids = [skill["id"] for skill in current.get("skills", [])]
            add("recommendation.withdrawn", recommendation_id, revision, skill_ids, "recommendation")
            continue
        if current_status != "ready":
            raise ReleaseIntegrityError(f"recommendation {recommendation_id} has unsupported status {current_status!r}")
        if previous_status == "withdrawn" or latest_type.get(key) == "recommendation.withdrawn":
            raise ReleaseIntegrityError(
                f"withdrawn recommendation {recommendation_id} cannot be republished; create a new recommendation ID"
            )
        if previous_status == "ready" or latest_type.get(key) == "recommendation.published":
            continue
        if previous_status is None or previous_status == "draft":
            revision = stable_digest({"id": recommendation_id, "skills": current.get("skills", []), "status": "ready"})
            skill_ids = [skill["id"] for skill in current.get("skills", [])]
            add("recommendation.published", recommendation_id, revision, skill_ids, "recommendation")

    return tuple(sorted(events, key=lambda event: event["event_id"]))
