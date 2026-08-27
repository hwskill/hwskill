from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class SourceSkill:
    name: str
    layer: str


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    kind: str
    root: Path
    namespace: str
    revision: str
    license: str
    skills: tuple[SourceSkill, ...]
    upstream_url: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SourceSpec":
        return cls(
            source_id=str(data["source_id"]),
            kind=str(data["kind"]),
            root=Path(str(data["root"])).expanduser(),
            namespace=str(data["namespace"]),
            revision=str(data["revision"]),
            license=str(data["license"]),
            skills=tuple(
                SourceSkill(name=str(item["name"]), layer=str(item["layer"]))
                for item in data["skills"]
            ),
            upstream_url=str(data["upstream_url"]) if data.get("upstream_url") else None,
        )


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    layer: str
    source_id: str
    revision: str
    license: str
    content_digest: str
    path: Path
