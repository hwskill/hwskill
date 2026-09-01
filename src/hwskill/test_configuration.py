"""Safe user-level configuration for declarative test execution.

Credentials deliberately do not belong to this format.  Hosts resolve them
from their normal login stores or from the process environment at run time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Literal

import yaml

from .hosts import canonical_host


_SCHEMA_VERSION = 1


class TestConfigurationError(ValueError):
    """Raised when a test configuration is unsafe or does not match its schema."""


class _YamlMappingKeyError(yaml.YAMLError):
    pass


class _ConfigurationLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _ConfigurationLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge" or key_node.value == "<<":
            raise _YamlMappingKeyError("YAML merge keys are not allowed")
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise _YamlMappingKeyError("YAML mapping keys must be strings")
        if key in result:
            raise _YamlMappingKeyError(f"duplicate YAML mapping key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_ConfigurationLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class HostModel:
    model: str
    reasoning: str


@dataclass(frozen=True)
class TestConfiguration:
    runner: Literal["docker", "local"]
    default_host: str
    hosts: Mapping[str, HostModel]


def test_config_path(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the XDG user-level test configuration path without touching disk."""
    env = os.environ if environment is None else environment
    base = env.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "hwskill" / "test.yaml"
    return (Path.home() if home is None else Path(home)) / ".config" / "hwskill" / "test.yaml"


def load_test_configuration(*, path: Path | None = None) -> TestConfiguration:
    """Load a strictly validated regular configuration file without following links."""
    config_path = _absolute_config_path(test_config_path() if path is None else Path(path))
    try:
        directory_fd = _open_config_parent(config_path, create=False)
        try:
            descriptor = _open_regular_file(config_path.name, directory_fd)
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as handle:
                text = handle.read()
        finally:
            os.close(directory_fd)
        payload = yaml.load(text, Loader=_ConfigurationLoader)
    except _YamlMappingKeyError as exc:
        raise TestConfigurationError(str(exc)) from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TestConfigurationError(f"cannot read test configuration: {exc}") from exc
    return _parse_configuration(payload)


def write_test_configuration(config: TestConfiguration, *, path: Path | None = None) -> None:
    """Atomically replace one mode-safe regular configuration file.

    The target and all existing parent components are checked with ``lstat``;
    consequently a configuration path can never redirect this write through a
    user-controlled symlink.
    """
    validated = _validate_configuration(config)
    config_path = _absolute_config_path(test_config_path() if path is None else Path(path))
    content = yaml.safe_dump(
        _serialize_configuration(validated), allow_unicode=True, sort_keys=False,
    )
    directory_fd = _open_config_parent(config_path, create=True)
    try:
        _atomic_write(config_path.name, directory_fd, content)
    finally:
        os.close(directory_fd)


def resolve_host_model(
    config: TestConfiguration,
    *,
    host: str | None = None,
) -> tuple[str, HostModel]:
    """Select the configured default host unless a caller explicitly overrides it."""
    validated = _validate_configuration(config)
    selected = validated.default_host if host is None else _canonical_host(host, "host override")
    try:
        return selected, validated.hosts[selected]
    except KeyError as exc:
        raise TestConfigurationError(f"no model is configured for host: {selected}") from exc


def _parse_configuration(value: Any) -> TestConfiguration:
    data = _mapping(value, "test configuration")
    _exact_keys(data, {"schema_version", "runner", "default_host", "hosts"}, "test configuration")
    if data["schema_version"] != _SCHEMA_VERSION or isinstance(data["schema_version"], bool):
        raise TestConfigurationError("test configuration schema_version must be exactly 1")
    hosts_data = _mapping(data["hosts"], "test configuration.hosts")
    hosts: dict[str, HostModel] = {}
    for host, model_data in hosts_data.items():
        canonical = _canonical_host(host, "configured host")
        if canonical != host:
            raise TestConfigurationError(f"configured host must be canonical: {host}")
        item = _mapping(model_data, f"host {host}")
        _exact_keys(item, {"model", "reasoning"}, f"host {host}")
        hosts[host] = HostModel(
            _nonempty_string(item["model"], f"host {host}.model"),
            _nonempty_string(item["reasoning"], f"host {host}.reasoning"),
        )
    return _validate_configuration(TestConfiguration(
        runner=data["runner"], default_host=data["default_host"], hosts=hosts,
    ))


def _validate_configuration(value: TestConfiguration) -> TestConfiguration:
    if not isinstance(value, TestConfiguration):
        raise TestConfigurationError("configuration must be a TestConfiguration")
    if value.runner not in {"docker", "local"}:
        raise TestConfigurationError("runner must be docker or local")
    default_host = _canonical_host(value.default_host, "default_host")
    if default_host != value.default_host:
        raise TestConfigurationError("default_host must be canonical")
    if not isinstance(value.hosts, Mapping) or not value.hosts:
        raise TestConfigurationError("hosts must be a non-empty mapping")
    hosts: dict[str, HostModel] = {}
    for host, model in value.hosts.items():
        if not isinstance(host, str) or _canonical_host(host, "configured host") != host:
            raise TestConfigurationError("configured host must be a supported canonical host")
        if not isinstance(model, HostModel):
            raise TestConfigurationError(f"host {host} must contain a HostModel")
        hosts[host] = HostModel(
            _nonempty_string(model.model, f"host {host}.model"),
            _nonempty_string(model.reasoning, f"host {host}.reasoning"),
        )
    if default_host not in hosts:
        raise TestConfigurationError("default_host must have a configured model")
    return TestConfiguration(value.runner, default_host, hosts)


def _serialize_configuration(config: TestConfiguration) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "runner": config.runner,
        "default_host": config.default_host,
        "hosts": {
            host: {"model": item.model, "reasoning": item.reasoning}
            for host, item in sorted(config.hosts.items())
        },
    }


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TestConfigurationError(f"{label} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise TestConfigurationError(f"{label} keys must be strings")
    return value


def _exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(data)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        detail = ", ".join(([f"unknown keys: {','.join(unknown)}"] if unknown else []) + ([f"missing keys: {','.join(missing)}"] if missing else []))
        raise TestConfigurationError(f"{label} has invalid keys ({detail})")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > 512:
        raise TestConfigurationError(f"{label} must be a non-empty safe string")
    return value


def _canonical_host(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TestConfigurationError(f"{label} must be a supported host")
    try:
        return canonical_host(value)
    except ValueError as exc:
        raise TestConfigurationError(str(exc)) from exc


def _absolute_config_path(path: Path) -> Path:
    candidate = path.absolute()
    if not candidate.name or candidate.name in {".", ".."}:
        raise TestConfigurationError("test configuration must name a file")
    return candidate


def _open_config_parent(path: Path, *, create: bool) -> int:
    """Anchor every parent directory, refusing symlink swaps on the way."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parent.parts[1:]:
            descriptor = _open_or_create_directory(component, descriptor, create=create)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_or_create_directory(component: str, parent_fd: int, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        child = os.open(component, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise TestConfigurationError(f"test configuration parent does not exist: {component}")
        try:
            os.mkdir(component, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            child = os.open(component, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise TestConfigurationError(f"unsafe test configuration parent: {component}") from exc
    except OSError as exc:
        raise TestConfigurationError(f"unsafe test configuration parent: {component}") from exc
    os.close(parent_fd)
    return child


def _open_regular_file(name: str, directory_fd: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        raise TestConfigurationError(f"test configuration does not exist: {name}") from exc
    except OSError as exc:
        raise TestConfigurationError(f"test configuration must be a regular non-symlink file: {name}") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise TestConfigurationError(f"test configuration must be a regular non-symlink file: {name}")
    return descriptor


def _atomic_write(name: str, directory_fd: int, content: str) -> None:
    try:
        existing = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None and (stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)):
        raise TestConfigurationError(f"test configuration must be a regular non-symlink file: {name}")
    temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.chmod(name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
        try:
            os.fsync(directory_fd)
        finally:
            pass
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
