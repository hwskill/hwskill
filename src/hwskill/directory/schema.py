from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


_SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas"
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("uri")
def _is_http_uri(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value and (value.endswith("Z") or "+" in value[10:] or "-" in value[10:])


@lru_cache(maxsize=None)
def validator_for(name: str) -> Draft202012Validator:
    with (_SCHEMA_ROOT / f"{name}.schema.json").open(encoding="utf-8") as handle:
        schema = json.load(handle)
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
