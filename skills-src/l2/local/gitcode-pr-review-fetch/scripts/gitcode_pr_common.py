#!/usr/bin/env python3

"""Shared GitCode pull-request API helpers."""

import json
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Tuple


DEFAULT_API_BASE = "https://api.gitcode.com/api/v5"
DEFAULT_TOKEN_ENV = "GITCODE_TOKEN"


class GitCodeApiError(RuntimeError):
    """Raised when GitCode returns an unusable response."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def parse_target(target: str, number: Optional[str]) -> Tuple[str, str, int]:
    """Parse a GitCode PR URL or an owner/repo plus PR number."""

    parsed = urllib.parse.urlparse(target)
    if parsed.scheme or parsed.netloc:
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"gitcode.com", "www.gitcode.com"}:
            raise ValueError("Expected a GitCode PR URL on gitcode.com")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 4 or parts[2] not in {"pull", "pulls"}:
            raise ValueError("Expected a GitCode PR URL ending in /pull/<number> or /pulls/<number>")
        owner, repo, _, parsed_number = parts
        if number is not None:
            raise ValueError("Do not pass a separate PR number with a GitCode PR URL")
        return owner, repo, _parse_pr_number(parsed_number)

    parts = [part for part in target.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("Expected TARGET as a GitCode PR URL or owner/repo")
    if number is None:
        raise ValueError("PR number is required when TARGET is owner/repo")
    return parts[0], parts[1], _parse_pr_number(number)


def _parse_pr_number(value: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("PR number must be a positive integer") from error
    if number <= 0:
        raise ValueError("PR number must be a positive integer")
    return number


class GitCodeApi:
    """Small read-only client for the GitCode v5 REST API."""

    def __init__(
        self,
        api_base: str = DEFAULT_API_BASE,
        token: Optional[str] = None,
        timeout: float = 30.0,
        per_page: int = 100,
        opener: Optional[Any] = None,
        max_retries: int = 2,
    ):
        if per_page < 1 or per_page > 100:
            raise ValueError("per_page must be between 1 and 100")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.per_page = per_page
        self.opener = opener or urllib.request.build_opener()
        self.max_retries = max_retries

    def get_json(
        self,
        path: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> Tuple[Any, Mapping[str, str]]:
        """GET one JSON response and return its normalized response headers."""

        request_params: Dict[str, object] = dict(params or {})
        if self.token:
            request_params["access_token"] = self.token
        query = urllib.parse.urlencode(request_params, doseq=True)
        url = f"{self.api_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "gitcode-pr-review-fetch/1",
            },
            method="GET",
        )

        for attempt in range(self.max_retries + 1):
            try:
                response = self.opener.open(request, timeout=self.timeout)
                try:
                    body = response.read()
                    headers = _normalize_headers(response.headers)
                finally:
                    close = getattr(response, "close", None)
                    if close is not None:
                        close()
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise GitCodeApiError(
                        f"GET {self._redact(url)} returned invalid JSON"
                    ) from error
                return payload, headers
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
            except urllib.error.URLError as error:
                message = f"GET {self._redact(url)} failed: {self._redact(str(error.reason))}"
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise GitCodeApiError(message) from error

        raise GitCodeApiError(f"GET {self._redact(url)} failed after retries")

    def get_paginated(
        self,
        path: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> List[Dict[str, object]]:
        """Fetch all pages using GitCode total_page headers with a short-page fallback."""

        base_params: Dict[str, object] = dict(params or {})
        per_page = int(base_params.get("per_page", getattr(self, "per_page", 100)))
        if per_page < 1 or per_page > 100:
            raise ValueError("per_page must be between 1 and 100")
        base_params["per_page"] = per_page

        results: List[Dict[str, object]] = []
        seen_ids = set()
        page = 1
        while page <= 10000:
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
            if total_page is not None:
                if page >= total_page:
                    return results
            elif len(payload) < per_page:
                return results
            page += 1

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


def pull_paths(owner: str, repo: str, number: int) -> Tuple[str, str]:
    owner_path = urllib.parse.quote(owner, safe="")
    repo_path = urllib.parse.quote(repo, safe="")
    repository_path = f"/repos/{owner_path}/{repo_path}"
    return repository_path, f"{repository_path}/pulls/{number}"


def fetch_pull_request(api: GitCodeApi, pull_path: str) -> Dict[str, object]:
    pull_request, _ = api.get_json(pull_path)
    if not isinstance(pull_request, dict):
        raise GitCodeApiError(f"GET {pull_path} returned a non-object pull request")
    return pull_request


def fetch_changed_files(api: GitCodeApi, pull_path: str) -> List[Dict[str, object]]:
    return api.get_paginated(
        f"{pull_path}/files",
        {"per_page": getattr(api, "per_page", 100)},
    )


def write_text_atomic(path: pathlib.Path, output: str) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            delete=False,
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


def _normalize_headers(headers: Any) -> Dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(key).lower(): str(value) for key, value in items}


def _read_http_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read()
    except Exception:
        return error.reason or ""
    try:
        return body.decode("utf-8", errors="replace")
    except AttributeError:
        return str(body)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _positive_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
