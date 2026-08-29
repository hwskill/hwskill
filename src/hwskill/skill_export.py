from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Literal, Sequence

from .digest import content_digest
from .models import SkillRecord
from .profiles import list_profiles, parse_csv


@dataclass(frozen=True)
class ExportResult:
    skill_id: str
    target: Path
    status: Literal["CREATED", "UNCHANGED"]


class ExportError(ValueError):
    pass


def resolve_skill_selectors(
    value: str,
    records: Sequence[SkillRecord],
) -> tuple[SkillRecord, ...]:
    by_id = {record.skill_id: record for record in records}
    by_name: dict[str, list[SkillRecord]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record)
    selected: dict[str, SkillRecord] = {}
    for selector in parse_csv(value, "skill"):
        record = by_id.get(selector)
        if record is None:
            matches = by_name.get(selector, [])
            if not matches:
                raise ExportError(f"unknown skill selector: {selector}")
            if len(matches) > 1:
                candidates = ", ".join(item.skill_id for item in matches)
                raise ExportError(
                    f"ambiguous skill name: {selector}; matches: {candidates}"
                )
            record = matches[0]
        selected.setdefault(record.skill_id, record)
    return tuple(selected.values())


def skills_for_profiles(
    value: str,
    registry_root: Path,
    records: Sequence[SkillRecord],
) -> tuple[SkillRecord, ...]:
    definitions = {item.profile_id: item for item in list_profiles(registry_root)}
    by_id = {item.skill_id: item for item in records}
    selected: dict[str, SkillRecord] = {}
    for profile_id in parse_csv(value, "profile"):
        definition = definitions.get(profile_id)
        if definition is None:
            raise ExportError(f"unknown profile: {profile_id}")
        for skill_id in definition.skill_ids:
            record = by_id.get(skill_id)
            if record is None:
                raise ExportError(f"unknown skill in profile: {skill_id}")
            selected.setdefault(skill_id, record)
    return tuple(selected.values())


def _payload_manifest(
    root: Path,
    *,
    exclude_root_governance: bool,
) -> tuple[tuple[str, str, str], ...]:
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if exclude_root_governance and relative == "skill.yaml":
            continue
        if path.is_dir():
            entries.append(("directory", relative, ""))
        elif path.is_file():
            entries.append(("file", relative, hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            entries.append(("other", relative, ""))
    return tuple(entries)


def export_skills(
    records: Sequence[SkillRecord],
    destination: Path,
) -> tuple[ExportResult, ...]:
    targets: dict[str, SkillRecord] = {}
    statuses: dict[str, Literal["CREATED", "UNCHANGED"]] = {}
    for record in records:
        if record.name in targets:
            other = targets[record.name]
            raise ExportError(
                f"target name collision: {record.name}; "
                f"matches: {other.skill_id}, {record.skill_id}"
            )
        targets[record.name] = record
        actual_digest = content_digest(record.path)
        if actual_digest != record.content_digest:
            raise ExportError(
                f"source digest mismatch for {record.skill_id}: "
                f"{record.content_digest} != {actual_digest}"
            )
        target = destination / record.name
        if not target.exists():
            statuses[record.skill_id] = "CREATED"
            continue
        if not target.is_dir() or _payload_manifest(
            record.path, exclude_root_governance=True
        ) != _payload_manifest(target, exclude_root_governance=False):
            raise ExportError(f"refusing to overwrite changed destination: {target}")
        statuses[record.skill_id] = "UNCHANGED"

    if any(status == "CREATED" for status in statuses.values()):
        destination.mkdir(parents=True, exist_ok=True)
    results = []
    for record in records:
        target = destination / record.name
        status = statuses[record.skill_id]
        if status == "UNCHANGED":
            results.append(ExportResult(record.skill_id, target, status))
            continue
        staging = Path(tempfile.mkdtemp(prefix=".hwskill-export-", dir=destination))
        try:
            for child in record.path.iterdir():
                if child.name == "skill.yaml":
                    continue
                staged_child = staging / child.name
                if child.is_dir():
                    shutil.copytree(child, staged_child)
                else:
                    shutil.copy2(child, staged_child)
            staged_digest = content_digest(staging)
            if staged_digest != record.content_digest:
                raise ExportError(
                    f"staged digest mismatch for {record.skill_id}: "
                    f"{record.content_digest} != {staged_digest}"
                )
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        results.append(ExportResult(record.skill_id, target, status))
    return tuple(results)
