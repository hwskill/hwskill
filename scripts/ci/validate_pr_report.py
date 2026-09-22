from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Sequence


REPORT_HEADING = "## 技能验证报告"
REPORT_FIELDS = (
    "验证环境",
    "获取的上游 commit",
    "安装目标",
    "安装步骤",
    "完整性检查",
    "使用任务",
    "实际结果",
    "未验证项",
    "已知限制",
)
_REPORT_ROOTS = {"entries", "translations", "skills-src"}
_FIELD_LINE = re.compile(r"^- ([^：\n]+)：[ \t]*(.*)$")
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)


def _requires_report(changed_paths: Sequence[str]) -> bool:
    for value in changed_paths:
        parts = PurePosixPath(value.removeprefix("./")).parts
        if parts and parts[0] in _REPORT_ROOTS:
            return True
    return False


def _report_section(body: str) -> list[str] | None:
    lines = body.splitlines()
    in_fence = False
    fence_marker = ""
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        if stripped == REPORT_HEADING:
            start = index + 1
            break
    if start is None:
        return None

    section: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in lines[start:]:
        stripped = line.strip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            section.append(line)
            continue
        if not in_fence and stripped.startswith("## "):
            break
        section.append(line)
    return section


def _field_values(lines: Sequence[str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = _FIELD_LINE.fullmatch(line)
        if match:
            label, first_value = match.groups()
            current = label if label in REPORT_FIELDS else None
            if current is not None:
                values.setdefault(current, []).append(first_value)
            continue
        if current is not None:
            values[current].append(line)
    return values


def validate_pr_report(event_path: Path, changed_paths: Sequence[str]) -> list[str]:
    """Validate the PR-body report required by skill source changes."""
    if not _requires_report(changed_paths):
        return []

    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["无法读取 GitHub Pull Request 事件。"]
    body = _HTML_COMMENT.sub("", event.get("pull_request", {}).get("body") or "")
    section = _report_section(body)
    if section is None:
        return [f"缺少“{REPORT_HEADING}”章节。"]

    values = _field_values(section)
    errors: list[str] = []
    for field in REPORT_FIELDS:
        if field not in values:
            errors.append(f"技能验证报告缺少字段：{field}")
            continue
        value = "\n".join(values[field]).strip()
        if not value:
            errors.append(f"技能验证报告字段为空：{field}")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the skill report in a GitHub Pull Request body.")
    parser.add_argument("--event-path", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--changed-paths-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.event_path is None:
        parser.error("--event-path or GITHUB_EVENT_PATH is required")
    try:
        changed_paths = [
            value.decode("utf-8", errors="surrogateescape")
            for value in args.changed_paths_file.read_bytes().split(b"\0")
            if value
        ]
    except OSError:
        print("无法读取变更路径列表。", file=sys.stderr)
        return 2
    errors = validate_pr_report(Path(args.event_path), changed_paths)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
