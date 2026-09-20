from __future__ import annotations

import json
import math
import socket
import ssl
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import FeedSnapshot
from .validation import catalog_url, install_url, stable_digest, validate_snapshot


class FeedReadError(RuntimeError):
    pass


class FeedAuthenticationError(FeedReadError):
    pass


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (parsed.hostname or "").lower(), port


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        try:
            destination = urlsplit(new_url)
            source = urlsplit(request.full_url)
            safe = (
                destination.scheme.lower() in {"http", "https"}
                and destination.hostname is not None
                and destination.username is None
                and destination.password is None
                and _origin(request.full_url) == _origin(new_url)
                and destination.path == source.path
                and not destination.query
                and not destination.fragment
            )
        except ValueError:
            safe = False
        if not safe:
            raise HTTPError(new_url, code, "unsafe cross-origin or non-HTTP redirect", headers, fp)
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        return redirected


class FeedReader:
    def __init__(
        self,
        *,
        private: bool = False,
        token: str | None = None,
        expected_feed_id: str | None = None,
        timeout: float = 10.0,
        max_attempts: int = 3,
        retry_delay: float = 0.25,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_snapshot_bytes: int = 64 * 1024 * 1024,
        max_records: int = 10_000,
        max_events: int = 50_000,
        max_requests: int = 100_000,
        opener=None,
        sleep=time.sleep,
    ):
        if private and not token:
            self._configuration_error = "private feed mode requires a token"
        else:
            self._configuration_error = None
        numeric_limits = {
            "timeout": timeout,
            "max_attempts": max_attempts,
            "retry_delay": retry_delay,
            "max_response_bytes": max_response_bytes,
            "max_snapshot_bytes": max_snapshot_bytes,
            "max_records": max_records,
            "max_events": max_events,
            "max_requests": max_requests,
        }
        for name, value in numeric_limits.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive")
        for name, value in (("max_attempts", max_attempts), ("max_response_bytes", max_response_bytes), ("max_snapshot_bytes", max_snapshot_bytes), ("max_records", max_records), ("max_events", max_events), ("max_requests", max_requests)):
            if not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if max_attempts > 10:
            raise ValueError("max_attempts cannot exceed 10")
        self.private = private
        self.token = token
        self.expected_feed_id = expected_feed_id
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_response_bytes = max_response_bytes
        self.max_snapshot_bytes = max_snapshot_bytes
        self.max_records = max_records
        self.max_events = max_events
        self.max_requests = max_requests
        self.opener = opener or build_opener(_SafeRedirectHandler())
        self.sleep = sleep
        self._feed_origin: tuple[str, str, int | None] | None = None
        self._request_count = 0
        self._snapshot_bytes = 0

    def _headers(self, url: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.private and self.token and _origin(url) == self._feed_origin:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _read_bytes(self, url: str) -> bytes:
        try:
            parsed = urlsplit(url)
            safe_url = (
                parsed.scheme.lower() in {"http", "https"}
                and parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
            )
        except ValueError:
            safe_url = False
        if not safe_url:
            raise FeedReadError(f"unsupported feed URL scheme for {url}")
        for attempt in range(self.max_attempts):
            if self._request_count >= self.max_requests:
                raise FeedReadError("request limit exceeded while reading feed snapshot")
            self._request_count += 1
            request = Request(url, headers=self._headers(url), method="GET")
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read(self.max_response_bytes + 1)
                    if len(body) > self.max_response_bytes:
                        raise FeedReadError(f"response exceeds {self.max_response_bytes} bytes at {url}")
                    return body
            except HTTPError as exc:
                transient = exc.code == 429 or exc.code in {500, 502, 503, 504}
                if not transient or attempt + 1 == self.max_attempts:
                    raise FeedReadError(f"HTTP {exc.code} while reading {url}") from exc
            except (socket.timeout, TimeoutError) as exc:
                if attempt + 1 == self.max_attempts:
                    raise FeedReadError(f"temporary failure while reading {url}") from exc
            except URLError as exc:
                reason = exc.reason
                deterministic_tls = isinstance(reason, (ssl.SSLError, ssl.CertificateError))
                transient = not deterministic_tls and isinstance(
                    reason, (socket.timeout, TimeoutError, ConnectionError, socket.gaierror)
                )
                if not transient or attempt + 1 == self.max_attempts:
                    raise FeedReadError(f"failure while reading {url}") from exc
            self.sleep(self.retry_delay * (2**attempt))
        raise AssertionError("unreachable retry state")

    def _read_json(self, url: str) -> Mapping[str, Any]:
        body = self._read_bytes(url)
        self._snapshot_bytes += len(body)
        if self._snapshot_bytes > self.max_snapshot_bytes:
            raise FeedReadError(f"feed snapshot exceeds {self.max_snapshot_bytes} bytes")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeedReadError(f"invalid JSON at {url}") from exc
        if not isinstance(value, Mapping):
            raise FeedReadError(f"JSON object required at {url}")
        return value

    def read_snapshot(self, feed_url: str) -> FeedSnapshot:
        if self._configuration_error:
            raise FeedAuthenticationError(self._configuration_error)
        feed_root = feed_url if feed_url.endswith("/") else feed_url + "/"
        try:
            parsed_feed = urlsplit(feed_root)
            if (
                parsed_feed.scheme.lower() not in {"http", "https"}
                or parsed_feed.hostname is None
                or parsed_feed.username is not None
                or parsed_feed.password is not None
                or parsed_feed.query
                or parsed_feed.fragment
                or not parsed_feed.path.rstrip("/").endswith("/feed")
            ):
                raise ValueError
            self._feed_origin = _origin(feed_root)
        except ValueError as exc:
            raise FeedReadError("feed URL must be an HTTP(S) URL with a host") from exc
        self._request_count = 0
        self._snapshot_bytes = 0
        head = self._read_json(urljoin(feed_root, "head.json"))
        head_sequence = head.get("sequence")
        if isinstance(head_sequence, bool) or not isinstance(head_sequence, int):
            exc = TypeError("sequence is not an integer")
            raise FeedReadError("feed head has no integer sequence") from exc
        if head_sequence > self.max_records:
            raise FeedReadError(f"feed head sequence exceeds record limit {self.max_records}")
        acquired_records: list[Mapping[str, Any]] = []
        event_count = 0
        for sequence in range(1, head_sequence + 1):
            record = self._read_json(urljoin(feed_root, f"records/{sequence:020d}.json"))
            acquired_records.append(record)
            events = record.get("events")
            if isinstance(events, list):
                event_count += len(events)
                if event_count > self.max_events:
                    raise FeedReadError(f"feed event count exceeds limit {self.max_events}")
        records = tuple(acquired_records)
        feed_ids = {
            event.get("feed_id")
            for record in records
            for event in record.get("events", [])
            if isinstance(event, Mapping) and isinstance(event.get("feed_id"), str)
        }
        feed_id = self.expected_feed_id or (next(iter(feed_ids)) if len(feed_ids) == 1 else "")
        snapshot_version = head.get("schema_version")
        provisional = FeedSnapshot(snapshot_version, feed_url.rstrip("/"), feed_id, head, records, {}, "")
        validate_snapshot(provisional, validate_subjects=False)

        urls: set[str] = set()
        for record in records:
            for event in record["events"]:
                machine_url = str(event["machine_url"])
                urls.add(machine_url)
                urls.add(catalog_url(machine_url))
                for skill_id in event["skill_ids"]:
                    urls.add(install_url(machine_url, skill_id))
        documents = {url: self._read_json(url) for url in sorted(urls)}
        identity = stable_digest({"head": head, "records": records, "documents": documents})
        snapshot = FeedSnapshot(snapshot_version, feed_url.rstrip("/"), feed_id, head, records, documents, identity)
        validate_snapshot(snapshot)
        return snapshot
