from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class FeedSnapshot:
    schema_version: int
    feed_url: str
    feed_id: str
    head: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]
    documents: Mapping[str, Mapping[str, Any]]
    snapshot_identity: str

    @property
    def head_sequence(self) -> int:
        return int(self.head["sequence"])

    @property
    def head_release_id(self) -> str:
        return str(self.head["release_id"])

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FeedSnapshot":
        return cls(
            schema_version=value["schema_version"],
            feed_url=value["feed_url"],
            feed_id=value["feed_id"],
            head=value["head"],
            records=tuple(value.get("records", ())),
            documents=value.get("documents", {}),
            snapshot_identity=value["snapshot_identity"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "feed_url": self.feed_url,
            "feed_id": self.feed_id,
            "head": dict(self.head),
            "records": [dict(record) for record in self.records],
            "documents": {url: dict(document) for url, document in self.documents.items()},
            "snapshot_identity": self.snapshot_identity,
        }


@dataclass(frozen=True)
class SourceVersion:
    skill_id: str
    requested_ref: str | None
    resolved_revision: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "requested_ref": self.requested_ref,
            "resolved_revision": self.resolved_revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceVersion":
        if set(value) != {"skill_id", "requested_ref", "resolved_revision"}:
            raise ValueError("source version fields are invalid")
        if not isinstance(value["skill_id"], str):
            raise ValueError("source version skill_id must be a string")
        for field_name in ("requested_ref", "resolved_revision"):
            if value[field_name] is not None and not isinstance(value[field_name], str):
                raise ValueError(f"source version {field_name} must be a string or null")
        return cls(**dict(value))


@dataclass(frozen=True)
class UpdateItem:
    schema_version: int
    item_id: str
    sequence: int
    event_ids: tuple[str, ...]
    change_type: str
    title: str
    summary: str
    skill_refs: tuple[str, ...]
    recommendation_refs: tuple[str, ...]
    source_versions: tuple[SourceVersion, ...]
    detail_url: str
    install_urls: tuple[str, ...]
    lifecycle: str
    install_capability: str
    purposes: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "item_id": self.item_id,
            "sequence": self.sequence,
            "event_ids": list(self.event_ids),
            "change_type": self.change_type,
            "title": self.title,
            "summary": self.summary,
            "skill_refs": list(self.skill_refs),
            "recommendation_refs": list(self.recommendation_refs),
            "source_versions": [version.to_dict() for version in self.source_versions],
            "detail_url": self.detail_url,
            "install_urls": list(self.install_urls),
            "lifecycle": self.lifecycle,
            "install_capability": self.install_capability,
            "purposes": list(self.purposes),
            "topics": list(self.topics),
        }


@dataclass(frozen=True)
class UpdateFilters:
    purposes: tuple[str, ...] = field(default_factory=tuple)
    skill_ids: tuple[str, ...] = field(default_factory=tuple)
    change_types: tuple[str, ...] = field(default_factory=tuple)
    lifecycles: tuple[str, ...] = field(default_factory=tuple)
    topics: tuple[str, ...] = field(default_factory=tuple)
