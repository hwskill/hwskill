"""Formatting and dispatch for the repository-wide integrity command."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
from typing import TextIO

from .integrity import IntegrityReport, check_integrity


def register_integrity_command(commands) -> None:
    parser = commands.add_parser(
        "integrity-check",
        help="Validate repository source integrity",
        description="Validate repository source integrity",
    )
    parser.add_argument("--repo-root")
    parser.add_argument("--json", action="store_true")


def run_integrity_command(
    args,
    repo_root: Path,
    output: TextIO,
) -> int | None:
    if args.command != "integrity-check":
        return None
    report = check_integrity(repo_root)
    output.write(format_integrity_report(report, json_output=args.json))
    return 0 if report.ok else 1


def format_integrity_report(report: IntegrityReport, *, json_output: bool) -> str:
    if json_output:
        payload = {
            "status": "clean" if report.ok else "failed",
            "counts": {
                "skills": report.skill_count,
                "sources": report.source_count,
                "profiles": report.profile_count,
                "issues": len(report.issues),
            },
            "issues": [
                asdict(issue)
                for issue in sorted(report.issues, key=lambda item: (item.code, item.path, item.message))
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    lines = [
        "Integrity check",
        f"Status  {'clean' if report.ok else 'failed'}",
        "",
        "Counts",
        f"  Skills    {report.skill_count}",
        f"  Sources   {report.source_count}",
        f"  Profiles  {report.profile_count}",
        f"  Issues    {len(report.issues)}",
    ]
    if report.issues:
        groups: dict[str, list] = defaultdict(list)
        for issue in report.issues:
            groups[_top_level_path(issue.path)].append(issue)
        lines.extend(("", "Issues"))
        for group in sorted(groups):
            lines.append(group)
            for issue in sorted(groups[group], key=lambda item: (item.path, item.code, item.message)):
                lines.append(f"  {issue.path}: [{issue.code}] {issue.message}")
    return "\n".join(lines) + "\n"


def _top_level_path(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if parts else "."
