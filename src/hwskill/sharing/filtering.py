from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from .models import FeedSnapshot, SourceVersion, UpdateFilters, UpdateItem
from .validation import catalog_url, install_url, recommendation_item, skill_item, stable_digest, validate_snapshot


def _events(snapshot: FeedSnapshot) -> list[Mapping[str, Any]]:
    return [event for record in snapshot.records for event in record["events"]]


def _latest_states(snapshot: FeedSnapshot) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    skills: dict[str, tuple[str, str]] = {}
    recommendations: dict[str, str] = {}
    for event in _events(snapshot):
        event_type = event["type"]
        for skill_id in event["skill_ids"]:
            item = skill_item(snapshot, event, skill_id)
            lifecycle = item["entry"].get("lifecycle", item.get("lifecycle"))
            capability = item.get("install_capability") if snapshot.schema_version == 1 else None
            skills[skill_id] = (lifecycle, capability)
        if event_type.startswith("skill."):
            if event_type == "skill.withdrawn":
                _, capability = skills[event["subject_id"]]
                skills[event["subject_id"]] = ("withdrawn", capability)
        elif event_type == "recommendation.withdrawn":
            recommendations[event["subject_id"]] = "withdrawn"
        else:
            recommendations[event["subject_id"]] = "active"
    return skills, recommendations


def _skill_metadata(snapshot: FeedSnapshot, event: Mapping[str, Any], skill_id: str):
    item = skill_item(snapshot, event, skill_id)
    entry = item["entry"]
    machine = install_url(str(event["machine_url"]), skill_id)
    install = snapshot.documents[machine]
    if snapshot.schema_version == 2:
        return item, entry, machine, None
    source = install.get("source", {})
    identity = item.get("source_identity", {})
    requested_ref = source.get("requested_ref")
    if requested_ref is None:
        requested_ref = identity.get("requested_ref")
    revision = source.get("resolved_revision")
    if revision is None:
        revision = identity.get("resolved_revision")
    return item, entry, machine, SourceVersion(
        skill_id=skill_id,
        requested_ref=requested_ref,
        resolved_revision=revision,
    )


def _make_item(
    snapshot: FeedSnapshot,
    grouped_events: Iterable[Mapping[str, Any]],
    latest_skills: Mapping[str, tuple[str, str]],
) -> UpdateItem:
    events = tuple(sorted(grouped_events, key=lambda event: event["event_id"]))
    recommendation_event = next((event for event in events if event["type"].startswith("recommendation.")), None)
    primary = recommendation_event or events[0]
    skill_ids = tuple(sorted({skill_id for event in events for skill_id in event["skill_ids"]}))
    metadata = [_skill_metadata(snapshot, primary, skill_id) for skill_id in skill_ids]
    source_versions = tuple(item[3] for item in metadata) if snapshot.schema_version == 1 else None
    install_urls = tuple(item[2] for item in metadata)
    purposes = tuple(sorted({purpose for _, entry, _, _ in metadata for purpose in entry.get("purposes", [])}))
    recommendation_refs = tuple(
        sorted(event["subject_id"] for event in events if event["type"].startswith("recommendation."))
    )
    if recommendation_event is not None:
        recommendation = recommendation_item(snapshot, recommendation_event)
        title = recommendation["title"]
        summary = recommendation["body"]
        topics = tuple(sorted(recommendation.get("topics", [])))
    else:
        entry = metadata[0][1]
        title = entry["name"]
        summary = entry["summary"]
        topics = ()
    types = [event["type"] for event in events]
    if set(types) == {"skill.added", "recommendation.published"}:
        change_type = "skill.added+recommendation.published"
    else:
        change_type = "+".join(sorted(types))
    if any(event["type"].endswith("withdrawn") for event in events):
        lifecycle = "withdrawn"
    else:
        lifecycle = next(
            (
                latest_skills[skill_id][0]
                for skill_id in skill_ids
                if skill_id in latest_skills and latest_skills[skill_id][0] != "active"
            ),
            "active",
        )
    capability_priority = {
        "installable": 0,
        "guidance_only": 1,
        "temporarily_unavailable": 2,
        "disabled": 3,
    }
    install_capability = None
    if snapshot.schema_version == 1:
        install_capability = max(
            (item[0]["install_capability"] for item in metadata),
            key=lambda capability: capability_priority[capability],
        )
    event_ids = tuple(event["event_id"] for event in events)
    item_id = "item-" + stable_digest({"event_ids": event_ids}).removeprefix("sha256:")
    return UpdateItem(
        schema_version=snapshot.schema_version,
        item_id=item_id,
        sequence=int(primary["sequence"]),
        event_ids=event_ids,
        change_type=change_type,
        title=title,
        summary=summary,
        skill_refs=skill_ids,
        recommendation_refs=recommendation_refs,
        source_versions=source_versions,
        detail_url=primary["page_url"],
        install_urls=install_urls,
        lifecycle=lifecycle,
        install_capability=install_capability,
        purposes=purposes,
        topics=topics,
    )


def _matches(item: UpdateItem, filters: UpdateFilters) -> bool:
    if filters.purposes and not set(filters.purposes).intersection(item.purposes):
        return False
    if filters.skill_ids and not set(filters.skill_ids).intersection(item.skill_refs):
        return False
    if filters.change_types:
        item_change_types = {item.change_type, *item.change_type.split("+")}
        if not set(filters.change_types).intersection(item_change_types):
            return False
    if filters.lifecycles and item.lifecycle not in filters.lifecycles:
        return False
    if filters.topics and not set(filters.topics).intersection(item.topics):
        return False
    return True


def build_items(
    snapshot: FeedSnapshot,
    from_sequence: int,
    filters: UpdateFilters | None = None,
) -> list[UpdateItem]:
    validate_snapshot(snapshot)
    if isinstance(from_sequence, bool) or not isinstance(from_sequence, int):
        raise ValueError("from_sequence must be an integer")
    if from_sequence < 0 or from_sequence > snapshot.head_sequence:
        raise ValueError("from_sequence must be inside the snapshot interval")
    filters = filters or UpdateFilters()
    latest_skills, latest_recommendations = _latest_states(snapshot)
    selected = [event for event in _events(snapshot) if event["sequence"] > from_sequence]

    active_events: list[Mapping[str, Any]] = []
    for event in selected:
        event_type = event["type"]
        if event_type == "recommendation.published":
            if latest_recommendations.get(event["subject_id"]) != "active":
                continue
            if any(
                latest_skills.get(skill_id) is None
                or latest_skills[skill_id][0] != "active"
                or (
                    snapshot.schema_version == 1
                    and latest_skills[skill_id][1] not in {"installable", "guidance_only"}
                )
                for skill_id in event["skill_ids"]
            ):
                continue
        elif event_type in {"skill.added", "skill.updated"}:
            latest = latest_skills.get(event["subject_id"])
            event_item = skill_item(snapshot, event, event["subject_id"])
            event_lifecycle = event_item["entry"].get("lifecycle", event_item.get("lifecycle", "active"))
            event_capability = event_item.get("install_capability") if snapshot.schema_version == 1 else None
            event_state = (event_lifecycle, event_capability)
            latest_available = latest is not None and latest[0] == "active" and (
                snapshot.schema_version == 2 or latest[1] in {"installable", "guidance_only"}
            )
            is_latest_control = event_type == "skill.updated" and event_state == latest and not latest_available
            if not latest_available and not is_latest_control:
                continue
        active_events.append(event)

    by_sequence: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for event in active_events:
        by_sequence[int(event["sequence"])].append(event)
    items: list[UpdateItem] = []
    for sequence in sorted(by_sequence):
        events = by_sequence[sequence]
        consumed: set[str] = set()
        recommendations = sorted(
            (event for event in events if event["type"] == "recommendation.published"),
            key=lambda event: event["event_id"],
        )
        for recommendation in recommendations:
            additions = [
                event
                for event in events
                if event["type"] == "skill.added"
                and event["subject_id"] in recommendation["skill_ids"]
                and event["event_id"] not in consumed
            ]
            group = additions + [recommendation]
            consumed.update(event["event_id"] for event in group)
            items.append(_make_item(snapshot, group, latest_skills))
        for event in sorted(events, key=lambda item: item["event_id"]):
            if event["event_id"] not in consumed:
                items.append(_make_item(snapshot, [event], latest_skills))
    return sorted((item for item in items if _matches(item, filters)), key=lambda item: (item.sequence, item.item_id))
