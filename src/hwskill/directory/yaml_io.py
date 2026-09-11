from __future__ import annotations

from pathlib import Path
from typing import Any
import math

import yaml


class YamlContractError(ValueError):
    """A YAML feature forbidden by the directory's JSON-compatible contract."""


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge" or key_node.value == "<<":
            raise YamlContractError("YAML merge keys are not permitted")
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise YamlContractError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _construct_timestamp_as_string(loader: _StrictSafeLoader, node: yaml.ScalarNode) -> str:
    """Keep timestamp-looking scalars JSON-compatible for Schema format checks."""
    return loader.construct_scalar(node)


_StrictSafeLoader.add_constructor("tag:yaml.org,2002:timestamp", _construct_timestamp_as_string)


def load_yaml(path: Path) -> dict[str, Any]:
    """Read one JSON-shaped YAML document without aliases that alter mappings."""
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictSafeLoader)
    except YamlContractError:
        raise
    except yaml.YAMLError as exc:
        raise YamlContractError(str(exc)) from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise YamlContractError("YAML document must be an object with string keys")
    _require_json_value(value)
    return value


def _require_json_value(value: Any, depth: int = 0) -> None:
    if depth > 100:
        raise YamlContractError("YAML nesting exceeds the JSON safety limit")
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise YamlContractError("YAML numbers must be finite")
    if isinstance(value, list):
        for item in value:
            _require_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise YamlContractError("YAML objects must use string keys")
        for item in value.values():
            _require_json_value(item, depth + 1)
        return
    raise YamlContractError(f"YAML value type {type(value).__name__} is not JSON-compatible")
