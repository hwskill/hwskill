from __future__ import annotations

import json

from .maintenance_transaction import MaintenancePlan


def plan_data(plan: MaintenancePlan, *, status: str = "success") -> dict[str, object]:
    summary = plan.summary
    return {
        "status": status,
        "operation": summary.operation,
        "sources": list(summary.source_details),
        "skills": {
            "added": list(summary.added_skill_ids),
            "updated": list(summary.updated_skill_ids),
            "removed": list(summary.removed_skill_ids),
            "manualized": list(summary.manualized_skill_ids),
        },
        "affected_profiles": list(summary.affected_profile_ids),
        "affected_tests": list(summary.affected_test_paths),
    }


def format_maintenance_plan(plan: MaintenancePlan, color: bool = False) -> str:
    """Render a deliberately plain, non-TTY-safe maintenance summary."""
    data = plan_data(plan)
    summary = plan.summary
    title = summary.operation.replace("-", " ").capitalize()
    lines = [f"{title}"]
    if summary.source_ids:
        lines.extend(["", "Source"])
        for source in data["sources"]:
            lines.append(f"  Source       {source['source_id']}")
            if source.get("repository"): lines.append(f"  Repository   {source['repository']}")
            if source.get("track"): lines.append(f"  Track        {source['track']}")
            if source.get("new_revision") is not None: lines.append(f"  Revision     {source.get('old_revision')} → {source['new_revision']}")
    lines.extend(["", "Skills"])
    for label, values in data["skills"].items():
        lines.append(f"  {label.capitalize():<12} {len(values)}")
    lines.extend(["", "Profiles", f"  Affected     {len(summary.affected_profile_ids)}", "", "Tests", f"  Affected     {len(summary.affected_test_paths)}"])
    return "\n".join(lines) + "\n"


def format_json(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
