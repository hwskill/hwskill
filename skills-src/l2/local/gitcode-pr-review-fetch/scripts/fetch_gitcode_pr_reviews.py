#!/usr/bin/env python3

"""Fetch complete GitCode pull-request comments and nested replies."""

import argparse
import copy
import json
import os
import pathlib
import sys
import urllib.parse
from typing import Dict, List, Mapping, Optional, Sequence

from gitcode_pr_common import (
    DEFAULT_API_BASE,
    DEFAULT_TOKEN_ENV,
    GitCodeApi,
    GitCodeApiError,
    fetch_changed_files,
    fetch_pull_request,
    parse_target,
    pull_paths,
    write_text_atomic,
)


def _is_diff_comment(comment: Mapping[str, object]) -> bool:
    for field in ("comment_type", "type", "noteable_type"):
        value = comment.get(field)
        if isinstance(value, str):
            normalized = value.lower().replace("_", "").replace("-", "")
            if normalized in {"diffcomment", "diffnote"}:
                return True
    return isinstance(comment.get("position") or comment.get("diff_position"), Mapping)


def normalize_comment(comment: Mapping[str, object]) -> Dict[str, object]:
    """Preserve GitCode fields while exposing stable position and replies keys."""

    normalized = copy.deepcopy(dict(comment))
    if _is_diff_comment(normalized):
        source_comment_type = normalized.get("comment_type")
        if source_comment_type not in {None, "diff_comment"}:
            normalized["source_comment_type"] = source_comment_type
        normalized["comment_type"] = "diff_comment"
    position = normalized.get("position") or normalized.get("diff_position")
    if position is not None:
        normalized["position"] = copy.deepcopy(position)
    replies = normalized.pop("reply", normalized.get("replies", []))
    normalized["replies"] = copy.deepcopy(replies or [])
    return normalized


def fetch_report(
    api: GitCodeApi,
    owner: str,
    repo: str,
    number: int,
    include_files: bool,
) -> Dict[str, object]:
    """Fetch PR metadata, all top-level comments, replies, and optional changed files."""

    repository_path, pull_path = pull_paths(owner, repo, number)
    pull_request = fetch_pull_request(api, pull_path)

    raw_comments = api.get_paginated(
        f"{pull_path}/comments",
        {"direction": "asc", "per_page": getattr(api, "per_page", 100)},
    )
    comments = []
    for raw_comment in raw_comments:
        comment = raw_comment
        if _is_diff_comment(raw_comment) and raw_comment.get("id") is not None:
            comment_id = urllib.parse.quote(str(raw_comment["id"]), safe="")
            detail, _ = api.get_json(f"{repository_path}/pulls/comments/{comment_id}")
            if not isinstance(detail, dict):
                raise GitCodeApiError(
                    f"GET {repository_path}/pulls/comments/{comment_id} returned a non-object comment"
                )
            comment = _merge_comment_detail(raw_comment, detail)
        comments.append(normalize_comment(comment))
    files: List[Dict[str, object]] = []
    if include_files:
        files = fetch_changed_files(api, pull_path)

    review_comments = [comment for comment in comments if comment.get("comment_type") == "diff_comment"]
    reply_count = sum(len(_as_list(comment.get("replies"))) for comment in comments)
    unresolved_count = sum(comment.get("resolved") is False for comment in review_comments)

    return {
        "repository": {
            "owner": owner,
            "repo": repo,
            "pull_number": number,
        },
        "pull_request": pull_request,
        "summary": {
            "comment_count": len(comments),
            "review_comment_count": len(review_comments),
            "reply_count": reply_count,
            "unresolved_count": unresolved_count,
        },
        "comments": comments,
        "files": files,
    }


def render_markdown(report: Mapping[str, object]) -> str:
    """Render a complete human-readable review transcript."""

    pull_request = _as_dict(report.get("pull_request"))
    summary = _as_dict(report.get("summary"))
    number = pull_request.get("number", _as_dict(report.get("repository")).get("pull_number", "?"))
    title = pull_request.get("title", "")
    lines = [f"# GitCode PR #{number} {title}".rstrip(), ""]
    lines.extend(
        [
            f"- 状态：`{pull_request.get('state', 'unknown')}`",
            f"- Base SHA：`{_nested_value(pull_request, 'base', 'sha') or 'unknown'}`",
            f"- Head SHA：`{_nested_value(pull_request, 'head', 'sha') or 'unknown'}`",
            f"- 评论：{summary.get('comment_count', 0)}",
            f"- 行级检视意见：{summary.get('review_comment_count', 0)}",
            f"- 回复：{summary.get('reply_count', 0)}",
            f"- 未解决检视意见：{summary.get('unresolved_count', 0)}",
            "",
        ]
    )

    comments = _as_list(report.get("comments"))
    for index, raw_comment in enumerate(comments, start=1):
        comment = _as_dict(raw_comment)
        user = _user_name(comment.get("user"))
        comment_type = comment.get("comment_type", "comment")
        lines.extend(
            [
                f"## {index}. `{comment_type}` · @{user}",
                "",
                f"- Comment ID：`{comment.get('id', 'unknown')}`",
                f"- Discussion ID：`{comment.get('discussion_id', 'unknown')}`",
            ]
        )
        if comment.get("created_at"):
            lines.append(f"- 时间：`{comment['created_at']}`")
        if comment_type == "diff_comment":
            resolved = comment.get("resolved")
            resolved_text = "已解决" if resolved is True else "未解决" if resolved is False else "未知"
            lines.append(f"- 状态：{resolved_text}")
        position_text = _format_position(comment.get("position"))
        if position_text:
            lines.append(f"- 位置：`{position_text}`")
        lines.extend(["", str(comment.get("body", "")), ""])

        replies = _as_list(comment.get("replies"))
        if replies:
            lines.extend(["### 回复", ""])
            for reply_index, raw_reply in enumerate(replies, start=1):
                reply = _as_dict(raw_reply)
                reply_user = _user_name(reply.get("user"))
                reply_time = f" · `{reply['created_at']}`" if reply.get("created_at") else ""
                lines.extend(
                    [
                        f"{reply_index}. @{reply_user}{reply_time}",
                        "",
                        f"   {str(reply.get('body', '')).replace(chr(10), chr(10) + '   ')}",
                        "",
                    ]
                )

    return "\n".join(lines).rstrip() + "\n"


def _merge_comment_detail(
    listed_comment: Mapping[str, object],
    detailed_comment: Mapping[str, object],
) -> Dict[str, object]:
    merged = copy.deepcopy(dict(listed_comment))
    for key, value in detailed_comment.items():
        if value is not None:
            merged[key] = copy.deepcopy(value)
    return merged


def _as_dict(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> List[object]:
    return value if isinstance(value, list) else []


def _nested_value(mapping: Mapping[str, object], first: str, second: str) -> object:
    return _as_dict(mapping.get(first)).get(second)


def _user_name(user: object) -> str:
    user_mapping = _as_dict(user)
    return str(user_mapping.get("login") or user_mapping.get("name") or "unknown")


def _format_position(position: object) -> str:
    position_mapping = _as_dict(position)
    path = position_mapping.get("new_path") or position_mapping.get("old_path")
    line = position_mapping.get("new_line") or position_mapping.get("old_line")
    if path and line is not None:
        return f"{path}:{line}"
    if path:
        return str(path)
    return ""


def _positive_int_argument(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _per_page_argument(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 100:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch all GitCode PR comments, line reviews, resolution states, and replies."
    )
    parser.add_argument("target", help="GitCode PR URL or owner/repo")
    parser.add_argument("number", nargs="?", help="PR number when target is owner/repo")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument(
        "--include-files",
        action="store_true",
        help=(
            "Include changed files in JSON output only; Markdown does not "
            "display files."
        ),
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--per-page", type=_per_page_argument, default=100)
    parser.add_argument("--timeout", type=_positive_int_argument, default=30)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        owner, repo, number = parse_target(args.target, args.number)
        token = os.environ.get(DEFAULT_TOKEN_ENV)
        api = GitCodeApi(
            api_base=args.api_base,
            token=token,
            timeout=args.timeout,
            per_page=args.per_page,
        )
        report = fetch_report(api, owner, repo, number, args.include_files)
        if args.format == "markdown":
            output = render_markdown(report)
        else:
            output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            write_text_atomic(args.output, output)
        else:
            sys.stdout.write(output)
        return 0
    except (GitCodeApiError, ValueError, OSError) as error:
        message = str(error)
        if isinstance(error, GitCodeApiError) and error.status_code == 401 and not os.environ.get(DEFAULT_TOKEN_ENV):
            message += f"; set {DEFAULT_TOKEN_ENV} for a private repository"
        print(f"error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
