from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


@dataclass(frozen=True)
class AuditEvent:
    event: str
    result: str
    session_id: str | None = None
    cwd: str | None = None
    profile_ids: tuple[str, ...] = ()
    catalog_digest: str | None = None
    skill_id: str | None = None
    revision: str | None = None
    content_digest: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None


class AuditWriter:
    def __init__(self, path: Path):
        self.path = path

    def write(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **asdict(event)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
