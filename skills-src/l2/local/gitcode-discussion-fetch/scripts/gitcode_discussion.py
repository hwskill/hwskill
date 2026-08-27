#!/usr/bin/env python3
"""GitCode Discussion archive retrieval and Markdown rendering helpers."""

import json
import os
import pathlib
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Tuple


API_BASE = "https://api.gitcode.com/api/v5"
DISCUSSION_URL_PATTERN = re.compile(
    r"^https?://gitcode\.com/([^/]+)/([^/]+)/discussions/(\d+)/?$"
)
REPOSITORY_PATTERN = re.compile(r"^([^/\s]+)/([^/\s]+)$")
HEADING_PATTERN = re.compile(r"^( {0,3})(#{1,6})(?=[ \t]|$)")
FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class GitCodeApiError(RuntimeError):
    """Raised when GitCode cannot provide a complete, stable archive."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GitCodeApi:
    def __init__(
        self,
        token: Optional[str] = None,
        api_base: str = API_BASE,
        opener: Optional[Any] = None,
        timeout_seconds: int = 30,
        max_retries: int = 2,
        per_page: int = 100,
    ):
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        self.token = token if token is not None else os.environ.get("GITCODE_TOKEN")
        self.api_base = api_base.rstrip("/")
        self.opener = opener if opener is not None else urllib.request.build_opener()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.per_page = per_page

    def get_json(
        self, path: str, params: Optional[Mapping[str, object]] = None
    ) -> Tuple[object, Dict[str, str]]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        headers = {"Accept": "application/json", "User-Agent": "gitcode-discussion-fetch/1"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body), _normalize_headers(response.headers)
            except urllib.error.HTTPError as error:
                body = _read_http_error_body(error)
                message = (
                    f"GET {self._redact(error.geturl() or url)} failed with HTTP "
                    f"{error.code}: {self._redact(body)}"
                )
                if _is_retryable_status(error.code) and attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise GitCodeApiError(message, error.code) from error
            except (urllib.error.URLError, json.JSONDecodeError) as error:
                message = f"GET {self._redact(url)} failed: {self._redact(str(error))}"
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise GitCodeApiError(message) from error

        raise GitCodeApiError(f"GET {self._redact(url)} failed after retries")

    def get_paginated(
        self, path: str, params: Optional[Mapping[str, object]] = None
    ) -> List[Dict[str, object]]:
        base_params: Dict[str, object] = dict(params or {})
        per_page = int(base_params.get("per_page", self.per_page))
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        base_params["per_page"] = per_page

        results: List[Dict[str, object]] = []
        seen_ids = set()
        for page in range(1, 10001):
            page_params = dict(base_params)
            page_params["page"] = page
            payload, headers = self.get_json(path, page_params)
            if not isinstance(payload, list):
                raise GitCodeApiError(f"GET {path} returned a non-list paginated response")
            for item in payload:
                if not isinstance(item, dict):
                    raise GitCodeApiError(f"GET {path} returned a non-object list item")
                item_id = item.get("id")
                if item_id is not None and item_id in seen_ids:
                    continue
                if item_id is not None:
                    seen_ids.add(item_id)
                results.append(item)

            total_page = _positive_int(headers.get("total_page"))
            if total_page is not None and page >= total_page:
                return results
            if total_page is None and len(payload) < per_page:
                return results
        raise GitCodeApiError(f"GET {path} exceeded the pagination safety limit")

    def _redact(self, text: str) -> str:
        redacted = str(text)
        if not self.token:
            return redacted
        for token_form in {
            self.token,
            urllib.parse.quote(self.token, safe=""),
            urllib.parse.quote_plus(self.token),
        }:
            if token_form:
                redacted = redacted.replace(token_form, "***")
        return redacted


def parse_target(target: str, number: Optional[str]) -> Tuple[str, str, int]:
    url_match = DISCUSSION_URL_PATTERN.fullmatch(target)
    if url_match:
        if number is not None:
            raise ValueError("Discussion URL must not be combined with a separate number")
        return url_match.group(1), url_match.group(2), int(url_match.group(3))

    repository_match = REPOSITORY_PATTERN.fullmatch(target)
    if not repository_match:
        raise ValueError(
            "Expected a GitCode Discussion URL or an owner/repo repository target"
        )
    if number is None:
        raise ValueError("Discussion number is required with an owner/repo target")
    try:
        discussion_number = int(number)
    except (TypeError, ValueError) as error:
        raise ValueError("Discussion number must be a positive integer") from error
    if discussion_number <= 0:
        raise ValueError("Discussion number must be a positive integer")
    return repository_match.group(1), repository_match.group(2), discussion_number


def discussion_paths(owner: str, repo: str, number: int) -> Tuple[str, str]:
    owner_path = urllib.parse.quote(owner, safe="")
    repo_path = urllib.parse.quote(repo, safe="")
    root = f"/repos/{owner_path}/{repo_path}/discuss/{number}"
    return root, f"{root}/comment"


def fetch_discussion_archive(
    api: GitCodeApi, owner: str, repo: str, number: int
) -> Dict[str, object]:
    discussion_path, comments_path = discussion_paths(owner, repo, number)
    before = _fetch_discussion(api, discussion_path)
    if before.get("number") != number:
        raise GitCodeApiError("Discussion detail number does not match the requested number")

    comments = api.get_paginated(comments_path, {"per_page": api.per_page})
    normalized_comments = []
    reply_count = 0
    for comment in comments:
        normalized_comment = dict(comment)
        comment_id = comment.get("id")
        if comment_id is None:
            raise GitCodeApiError("Discussion comment is missing its id")
        reply_total = _nonnegative_int(comment.get("reply_total"))
        replies: List[Dict[str, object]] = []
        if reply_total is None or reply_total > 0:
            reply_path = f"{comments_path}/{urllib.parse.quote(str(comment_id), safe='')}/reply"
            replies = api.get_paginated(reply_path, {"per_page": api.per_page})
            if reply_total is not None and len(replies) != reply_total:
                raise GitCodeApiError(
                    f"Comment {comment_id} reply count does not match reply_total"
                )
        normalized_comment["replies"] = replies
        normalized_comments.append(normalized_comment)
        reply_count += len(replies)

    after = _fetch_discussion(api, discussion_path)
    if not _same_snapshot(before, after):
        raise GitCodeApiError("Discussion changed while fetching; retry for a consistent archive")
    expected_comment_count = _nonnegative_int(after.get("comment_total"))
    if expected_comment_count is not None and expected_comment_count != len(comments):
        raise GitCodeApiError(
            "Discussion comment count does not match comment_total; retry for a consistent archive"
        )

    return {
        "owner": owner,
        "repo": repo,
        "number": number,
        "discussion": after,
        "comments": normalized_comments,
        "summary": {"comment_count": len(comments), "reply_count": reply_count},
    }


def render_markdown(archive: Mapping[str, object]) -> str:
    discussion = _as_mapping(archive.get("discussion"), "discussion")
    owner = str(archive["owner"])
    repo = str(archive["repo"])
    number = int(archive["number"])
    summary = _as_mapping(archive.get("summary"), "summary")
    title = _markdown_text(discussion.get("title")) or f"Discussion #{number}"
    frontmatter = {
        "source": "gitcode",
        "repository": f"{owner}/{repo}",
        "discussion_number": number,
        "discussion_id": discussion.get("id"),
        "url": f"https://gitcode.com/{owner}/{repo}/discussions/{number}",
        "title": title,
        "author": _author_name(discussion.get("author")),
        "created_at": discussion.get("created_at"),
        "updated_at": discussion.get("updated_at"),
        "comments": summary.get("comment_count", 0),
        "replies": summary.get("reply_count", 0),
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        if value is not None:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", "", f"# {title}", "", "## 正文", ""])
    lines.extend(_body_lines(discussion.get("md_content"), 2))

    comments = archive.get("comments", [])
    if not isinstance(comments, list):
        raise ValueError("archive comments must be a list")
    for index, comment in enumerate(comments, start=1):
        comment_mapping = _as_mapping(comment, "comment")
        lines.extend(["", f"## 评论 {index}", ""])
        lines.extend(_metadata_lines(comment_mapping))
        lines.append("")
        lines.extend(_body_lines(comment_mapping.get("md_content"), 2))
        replies = comment_mapping.get("replies", [])
        if not isinstance(replies, list):
            raise ValueError("comment replies must be a list")
        for reply_index, reply in enumerate(replies, start=1):
            reply_mapping = _as_mapping(reply, "reply")
            lines.extend(["", f"### 回复 {index}.{reply_index}", ""])
            lines.extend(_metadata_lines(reply_mapping))
            lines.append("")
            lines.extend(_body_lines(reply_mapping.get("md_content"), 3))
    return "\n".join(lines).rstrip() + "\n"


def rebase_headings(markdown: str, base_level: int) -> str:
    lines = str(markdown or "").splitlines()
    rebased = []
    active_fence = None
    for line in lines:
        fence = FENCE_PATTERN.match(line)
        if fence:
            marker = fence.group(1)
            if active_fence is None:
                active_fence = marker
            elif (
                marker[0] == active_fence[0]
                and len(marker) >= len(active_fence)
                and not fence.group(2).strip()
            ):
                active_fence = None
            rebased.append(line)
            continue
        if active_fence is None:
            heading = HEADING_PATTERN.match(line)
            if heading:
                level = min(6, len(heading.group(2)) + base_level)
                line = f"{heading.group(1)}{'#' * level}{line[heading.end():]}"
        rebased.append(line)
    return "\n".join(rebased)


def write_text_atomic(path: pathlib.Path, output: str) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as temporary_file:
            temporary_path = pathlib.Path(temporary_file.name)
            temporary_file.write(output)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        raise


def _fetch_discussion(api: GitCodeApi, path: str) -> Dict[str, object]:
    payload, _ = api.get_json(path)
    if not isinstance(payload, dict):
        raise GitCodeApiError(f"GET {path} returned a non-object Discussion")
    return payload


def _same_snapshot(before: Mapping[str, object], after: Mapping[str, object]) -> bool:
    return all(
        before.get(key) == after.get(key)
        for key in ("id", "number", "updated_at", "comment_total")
    )


def _metadata_lines(item: Mapping[str, object]) -> List[str]:
    lines = []
    author = _author_name(item.get("author"))
    if author:
        lines.append(f"- 作者：{author}")
    if item.get("created_at"):
        lines.append(f"- 创建时间：{item['created_at']}")
    if item.get("updated_at"):
        lines.append(f"- 更新时间：{item['updated_at']}")
    return lines


def _body_lines(value: object, base_level: int) -> List[str]:
    body = rebase_headings(_markdown_text(value), base_level).strip()
    return body.splitlines() if body else ["_无正文_"]


def _author_name(author: object) -> Optional[str]:
    if isinstance(author, Mapping):
        for key in ("login", "name", "username"):
            if author.get(key):
                return str(author[key])
    return None


def _markdown_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"archive {name} must be an object")
    return value


def _normalize_headers(headers: Any) -> Dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(key).lower(): str(value) for key, value in items}


def _read_http_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read()
    except Exception:
        return str(error.reason or "")
    return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _positive_int(value: object) -> Optional[int]:
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_int(value: object) -> Optional[int]:
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed >= 0 else None
