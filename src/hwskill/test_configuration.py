"""Safe user-level configuration for declarative test execution.

Credentials deliberately do not belong to this format.  Hosts resolve them
from their normal login stores or from the process environment at run time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Callable, Literal

import yaml

from .hosts import canonical_host


_SCHEMA_VERSION = 1
_MAX_CONFIG_BYTES = 64 * 1024
SUPPORTED_REASONING_EFFORTS = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
})
_MODEL_IDENTIFIER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._:/@+-]*[A-Za-z0-9])?\Z")


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


@dataclass(frozen=True)
class AtomicFileOperations:
    """The failure-prone atomic publication operations, injectable in tests."""

    replace: Callable[..., None] = os.replace
    chmod: Callable[..., None] = os.chmod
    fsync: Callable[[int], None] = os.fsync
    unlink: Callable[..., None] = os.unlink


def test_config_path(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the XDG user-level test configuration path without touching disk."""
    env = os.environ if environment is None else environment
    base = env.get("XDG_CONFIG_HOME")
    if base:
        candidate = Path(base)
        if not candidate.is_absolute():
            raise TestConfigurationError("XDG_CONFIG_HOME must be an absolute path")
        return candidate / "hwskill" / "test.yaml"
    return (Path.home() if home is None else Path(home)) / ".config" / "hwskill" / "test.yaml"


def load_test_configuration(*, path: Path | None = None) -> TestConfiguration:
    """Load a strictly validated regular configuration file without following links."""
    config_path = _absolute_config_path(test_config_path() if path is None else Path(path))
    try:
        directory_fd = _open_config_parent(config_path, create=False)
        try:
            descriptor = _open_regular_file(config_path.name, directory_fd)
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read(_MAX_CONFIG_BYTES + 1)
            if len(raw) > _MAX_CONFIG_BYTES:
                raise TestConfigurationError("test configuration exceeds maximum size")
            text = raw.decode("utf-8")
        finally:
            os.close(directory_fd)
        _reject_yaml_references(text)
        payload = yaml.load(text, Loader=_ConfigurationLoader)
    except _YamlMappingKeyError as exc:
        raise TestConfigurationError(str(exc)) from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TestConfigurationError(f"cannot read test configuration: {exc}") from exc
    return _parse_configuration(payload)


def write_test_configuration(
    config: TestConfiguration,
    *,
    path: Path | None = None,
    operations: AtomicFileOperations | None = None,
) -> None:
    """Atomically replace one mode-safe regular configuration file.

    Descriptor-anchored parent traversal and ``O_NOFOLLOW`` prevent a
    configuration path from redirecting this write through a user-controlled
    symlink.
    """
    validated = _validate_configuration(config)
    config_path = _absolute_config_path(test_config_path() if path is None else Path(path))
    content = yaml.safe_dump(
        _serialize_configuration(validated), allow_unicode=True, sort_keys=False,
    )
    if len(content.encode("utf-8")) > _MAX_CONFIG_BYTES:
        raise TestConfigurationError("test configuration exceeds maximum size")
    directory_fd = _open_config_parent(config_path, create=True)
    try:
        _atomic_write(config_path.name, directory_fd, content, operations or AtomicFileOperations())
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
            _model(item["model"], f"host {host}.model"),
            _reasoning(item["reasoning"], f"host {host}.reasoning"),
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
            _model(model.model, f"host {host}.model"),
            _reasoning(model.reasoning, f"host {host}.reasoning"),
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


def _reasoning(value: Any, label: str) -> str:
    reasoning = _nonempty_string(value, label)
    if reasoning not in SUPPORTED_REASONING_EFFORTS:
        raise TestConfigurationError(f"{label} must be a supported reasoning effort")
    return reasoning


def _model(value: Any, label: str) -> str:
    model = _nonempty_string(value, label)
    if _MODEL_IDENTIFIER.fullmatch(model) is None:
        raise TestConfigurationError(f"{label} must be a safe single CLI argument")
    return model


def _reject_yaml_references(text: str) -> None:
    try:
        for event in yaml.parse(text, Loader=_ConfigurationLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise TestConfigurationError("YAML anchors and aliases are not allowed")
    except TestConfigurationError:
        raise
    except yaml.YAMLError as exc:
        raise TestConfigurationError(f"cannot parse test configuration: {exc}") from exc


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
    try:
        return _open_config_parent_from_anchor(Path("/"), path.parent.parts[1:], create=create)
    except PermissionError:
        pass
    # Reuse the pending-state anchor grammar: the runner supplies a trusted,
    # first-level mount and every descendant remains O_NOFOLLOW-opened.
    from .pending_verification import _permitted_directory_anchors

    for anchor in _permitted_directory_anchors():
        try:
            relative = path.parent.relative_to(anchor)
        except ValueError:
            continue
        try:
            return _open_config_parent_from_anchor(anchor, relative.parts, create=create)
        except PermissionError:
            continue
    raise TestConfigurationError("cannot safely open test configuration parent")


def _open_config_parent_from_anchor(anchor: Path, components: tuple[str, ...], *, create: bool) -> int:
    descriptor = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in components:
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
    if metadata.st_size > _MAX_CONFIG_BYTES:
        os.close(descriptor)
        raise TestConfigurationError("test configuration exceeds maximum size")
    return descriptor


def _atomic_write(
    name: str,
    directory_fd: int,
    content: str,
    operations: AtomicFileOperations,
) -> None:
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
            operations.chmod(temporary_name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
            operations.fsync(handle.fileno())
        operations.replace(temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        operations.fsync(directory_fd)
    except Exception:
        try:
            operations.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
