from __future__ import annotations

from typing import Any, Mapping

from .models import FeedSnapshot, SnapshotVersion
from .v1 import (
    FeedValidationError,
    catalog_url,
    content_digest,
    install_url,
    recommendation_item,
    release_data_base,
    skill_item,
    skill_semantic as skill_semantic_v1,
    stable_digest,
    validate_snapshot_v1,
)


def _snapshot(value: FeedSnapshot | Mapping[str, Any]) -> FeedSnapshot:
    if isinstance(value, FeedSnapshot):
        return value
    if not isinstance(value, Mapping):
        raise FeedValidationError("invalid-document", "snapshot must be an object")
    try:
        return FeedSnapshot.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedValidationError("invalid-document", "snapshot fields are incomplete") from exc


def skill_semantic(item: Mapping[str, Any]) -> dict[str, Any]:
    entry = item.get("entry", {})
    if entry.get("schema_version") == 2:
        return {
            "source": entry.get("source"),
            "install": entry.get("install"),
            "lifecycle": entry.get("lifecycle", item.get("lifecycle", "active")),
        }
    return skill_semantic_v1(item)


def validate_snapshot(
    document: FeedSnapshot | Mapping[str, Any], *, validate_subjects: bool = True
) -> SnapshotVersion:
    snapshot = _snapshot(document)
    version = snapshot.schema_version
    if type(version) is not int:
        raise FeedValidationError("unknown-major", f"snapshot schema {version} is unsupported")
    if version == 1:
        validate_snapshot_v1(snapshot, validate_subjects=validate_subjects)
        return SnapshotVersion.V1
    if version == 2:
        from .v2 import validate_snapshot_v2

        validate_snapshot_v2(snapshot, validate_subjects=validate_subjects)
        return SnapshotVersion.V2
    raise FeedValidationError("unknown-major", f"snapshot schema {version} is unsupported")
