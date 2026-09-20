from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


FIXTURE = Path(__file__).parent / "fixtures" / "valid-snapshot.json"
V2_FIXTURE = Path(__file__).parent / "fixtures" / "valid-v2-snapshot.json"


def stable_digest(value: object) -> str:
    import hashlib

    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def refresh_event_ids(payload: dict[str, Any]) -> None:
    latest: dict[tuple[str, str], str] = {}
    for record in payload["records"]:
        for event in record["events"]:
            family = "skill" if event["type"].startswith("skill.") else "recommendation"
            key = (family, event["subject_id"])
            event["event_id"] = "event-" + stable_digest(
                {
                    "feed_id": event["feed_id"],
                    "type": event["type"],
                    "subject_id": event["subject_id"],
                    "subject_revision": event["subject_revision"],
                    "previous_event_id": latest.get(key),
                }
            ).removeprefix("sha256:")
            latest[key] = event["event_id"]
        record["events"].sort(key=lambda event: event["event_id"])


def refresh_snapshot_identity(payload: dict[str, Any], *, refresh_ids: bool = False) -> None:
    if refresh_ids:
        refresh_event_ids(payload)
    payload["snapshot_identity"] = stable_digest(
        {"head": payload["head"], "records": payload["records"], "documents": payload["documents"]}
    )


def clone_release_documents(payload: dict[str, Any], old_release: str, new_release: str) -> None:
    marker = f"/releases/{old_release}/"
    additions = {
        url.replace(marker, f"/releases/{new_release}/"): deepcopy(document)
        for url, document in payload["documents"].items()
        if marker in url
    }
    payload["documents"].update(additions)


def fixture_payload(base_url: str = "https://directory.test") -> dict[str, Any]:
    text = FIXTURE.read_text(encoding="utf-8").replace("https://directory.test", base_url.rstrip("/"))
    payload = json.loads(text)
    refresh_snapshot_identity(payload, refresh_ids=True)
    return payload


def valid_snapshot(base_url: str = "https://directory.test"):
    from hwskill.sharing.models import FeedSnapshot

    return FeedSnapshot.from_dict(fixture_payload(base_url))


def valid_v2_snapshot(base_url: str = "https://directory.test"):
    from hwskill.sharing.models import FeedSnapshot

    text = V2_FIXTURE.read_text(encoding="utf-8").replace(
        "https://directory.test", base_url.rstrip("/")
    )
    payload = json.loads(text)
    refresh_snapshot_identity(payload, refresh_ids=True)
    return FeedSnapshot.from_dict(payload)


def server_documents(base_url: str) -> dict[str, bytes]:
    payload = fixture_payload(base_url)
    result = {
        "/feed/head.json": payload["head"],
        "/feed/records/00000000000000000001.json": payload["records"][0],
    }
    for url, document in payload["documents"].items():
        path = url.removeprefix(base_url.rstrip("/"))
        result[path] = document
    return {path: json.dumps(value, ensure_ascii=False).encode("utf-8") for path, value in result.items()}


def mutable_snapshot(base_url: str = "https://directory.test"):
    return deepcopy(fixture_payload(base_url))
