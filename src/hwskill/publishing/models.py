from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any


class CandidateState(str, Enum):
    PREPARED = "prepared"
    UPLOADED = "uploaded"
    CHECKED = "checked"
    ACTIVATED = "activated"
    COMMITTED = "committed"


def stable_digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class ReleaseRequest:
    source_commit: str
    catalog_digest: str
    snapshot_digest: str
    build_config_identity: str
    artifact_dir: Path

    @property
    def release_id(self) -> str:
        digest = stable_digest(
            {
                "source_commit": self.source_commit,
                "catalog_digest": self.catalog_digest,
                "snapshot_digest": self.snapshot_digest,
                "build_config_identity": self.build_config_identity,
            }
        )
        return "release-" + digest.removeprefix("sha256:")


@dataclass(frozen=True)
class Candidate:
    release_id: str
    state: CandidateState
    source_commit: str
    catalog_digest: str
    snapshot_digest: str
    build_config_identity: str
    artifact_snapshot_path: Path
    artifact_snapshot_digest: str
    publication_time: str
    committed_at: str | None
    sequence: int
    previous_sequence: int | None
    events: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "release_id": self.release_id,
            "state": self.state.value,
            "source_commit": self.source_commit,
            "catalog_digest": self.catalog_digest,
            "snapshot_digest": self.snapshot_digest,
            "build_config_identity": self.build_config_identity,
            "artifact_snapshot_path": str(self.artifact_snapshot_path),
            "artifact_snapshot_digest": self.artifact_snapshot_digest,
            "publication_time": self.publication_time,
            "committed_at": self.committed_at,
            "sequence": self.sequence,
            "previous_sequence": self.previous_sequence,
            "events": list(self.events),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Candidate":
        expected = {
            "schema_version",
            "release_id",
            "state",
            "source_commit",
            "catalog_digest",
            "snapshot_digest",
            "build_config_identity",
            "artifact_snapshot_path",
            "artifact_snapshot_digest",
            "publication_time",
            "committed_at",
            "sequence",
            "previous_sequence",
            "events",
        }
        if set(value) != expected or value.get("schema_version") != 1:
            raise ValueError("candidate state has an unknown schema or fields")
        return cls(
            release_id=value["release_id"],
            state=CandidateState(value["state"]),
            source_commit=value["source_commit"],
            catalog_digest=value["catalog_digest"],
            snapshot_digest=value["snapshot_digest"],
            build_config_identity=value["build_config_identity"],
            artifact_snapshot_path=Path(value["artifact_snapshot_path"]),
            artifact_snapshot_digest=value["artifact_snapshot_digest"],
            publication_time=value["publication_time"],
            committed_at=value["committed_at"],
            sequence=value["sequence"],
            previous_sequence=value.get("previous_sequence"),
            events=tuple(value.get("events", [])),
        )

    def advance(self, state: CandidateState) -> "Candidate":
        return Candidate(**{**self.__dict__, "state": state})

    def freeze_committed_at(self, committed_at: str) -> "Candidate":
        if self.committed_at is not None and self.committed_at != committed_at:
            raise ValueError("candidate committed_at is already frozen")
        return Candidate(**{**self.__dict__, "committed_at": committed_at})

    @property
    def artifact_digest(self) -> str:
        return self.artifact_snapshot_digest
