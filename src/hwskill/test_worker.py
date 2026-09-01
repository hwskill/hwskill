"""Strict in-container entry point for standardized test execution."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any, Literal

from .docker_test_runner import CoreCollectionResult, TestRunResult
from .hosts import HOST_SPECS
from .hosts import canonical_host
from . import network_guard
from .test_agent import AgentExecutor
from .test_artifacts import ActionResult, CollectionResult, safe_artifact_id
from .test_configuration import HostModel, TestConfiguration, resolve_host_model
from .test_manifest import TestCollection, TestManifestError, load_test_collection
from .test_runner import TestEnvironment, run_collection
from .test_setup import parse_host_version


_MAX_REQUEST_BYTES = 64 * 1024
_APPROVED_CREDENTIAL_ENVIRONMENT = {
    "codex": frozenset({"CODEX_API_KEY"}),
    "claude-code": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}),
    "opencode": frozenset({"OPENCODE_API_KEY"}),
}


class WorkerRequestError(ValueError):
    """The mounted request is ambiguous, unsafe, or outside the worker schema."""


@dataclass(frozen=True)
class WorkerSelection:
    kind: Literal["core", "skill", "profile"]
    path: str


@dataclass(frozen=True)
class WorkerRequest:
    selections: tuple[WorkerSelection, ...]
    host: str
    model: str
    reasoning: str
    timeout_seconds: float
    credential_environment: tuple[str, ...]


def load_worker_request(path: Path) -> WorkerRequest:
    """Read one no-follow, size-bounded JSON request with duplicate-key rejection."""
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(Path(path), flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise WorkerRequestError("worker request must be a regular file")
        raw = os.read(descriptor, _MAX_REQUEST_BYTES + 1)
        if len(raw) > _MAX_REQUEST_BYTES:
            raise WorkerRequestError("worker request exceeds maximum size")
        if os.read(descriptor, 1):
            raise WorkerRequestError("worker request exceeds maximum size")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except WorkerRequestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkerRequestError("cannot read worker request") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _parse_request(payload)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkerRequestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_request(value: Any) -> WorkerRequest:
    data = _mapping(value, "worker request")
    _exact_keys(data, {
        "schema_version", "selections", "host", "model", "reasoning",
        "timeout_seconds", "credential_environment",
    }, "worker request")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise WorkerRequestError("worker request schema_version must be exactly 1")
    host = _string(data["host"], "host")
    try:
        host = canonical_host(host)
        config = TestConfiguration(
            "docker", host,
            {host: HostModel(_string(data["model"], "model"), _string(data["reasoning"], "reasoning"))},
        )
        _host, model = resolve_host_model(config)
    except ValueError as exc:
        raise WorkerRequestError(str(exc)) from exc
    raw_timeout = data["timeout_seconds"]
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        raise WorkerRequestError("timeout_seconds must be numeric")
    timeout = float(raw_timeout)
    if not 0 < timeout <= 3600:
        raise WorkerRequestError("timeout_seconds is outside the supported range")
    raw_selections = data["selections"]
    if not isinstance(raw_selections, list) or not raw_selections:
        raise WorkerRequestError("selections must be a non-empty list")
    selections = tuple(_parse_selection(item) for item in raw_selections)
    if len({(item.kind, item.path) for item in selections}) != len(selections):
        raise WorkerRequestError("worker selections must be unique")
    raw_names = data["credential_environment"]
    if not isinstance(raw_names, list) or any(not isinstance(name, str) for name in raw_names):
        raise WorkerRequestError("credential_environment must be a string list")
    names = tuple(raw_names)
    if len(set(names)) != len(names) or any(name not in _APPROVED_CREDENTIAL_ENVIRONMENT[host] for name in names):
        raise WorkerRequestError("credential_environment contains an unapproved name")
    return WorkerRequest(selections, host, model.model, model.reasoning, timeout, names)


def _parse_selection(value: Any) -> WorkerSelection:
    data = _mapping(value, "selection")
    _exact_keys(data, {"kind", "path"}, "selection")
    kind = _string(data["kind"], "selection kind")
    path = _string(data["path"], "selection path")
    if kind not in {"core", "skill", "profile"}:
        raise WorkerRequestError("selection kind is unsupported")
    pure = PurePosixPath(path)
    if pure.is_absolute() or "\\" in path or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise WorkerRequestError("selection path must be a safe repository-relative path")
    if kind == "core":
        if pure.parts[:2] != ("tests", "core"):
            raise WorkerRequestError("core selection must be under tests/core")
        if len(pure.parts) > 2 and (not pure.name.startswith("test_") or pure.suffix != ".py"):
            raise WorkerRequestError("core selection must name tests/core or a test_*.py file")
    else:
        plural = kind + "s"
        if len(pure.parts) < 4 or pure.parts[:2] != ("tests", plural) or pure.name != "test.yaml":
            raise WorkerRequestError(f"{kind} selection must name a test.yaml under tests/{plural}")
    return WorkerSelection(kind, path)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise WorkerRequestError(f"{label} must be an object")
    return value


def _exact_keys(data: dict[str, Any], expected: set[str], label: str) -> None:
    if set(data) != expected:
        raise WorkerRequestError(f"{label} keys must be exactly: {', '.join(sorted(expected))}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise WorkerRequestError(f"{label} must be a non-empty single-line string")
    return value


def execute_worker(
    request: WorkerRequest,
    *,
    repo_root: Path = Path("/registry"),
    tests_root: Path = Path("/tests"),
    artifact_root: Path = Path("/artifacts"),
    workspace_root: Path = Path("/workspace"),
    host_version_probe=None,
) -> int:
    """Execute a validated request only within the four fixed mount roots."""
    try:
        repository, tests, artifacts, workspace = _validate_mount_roots(
            repo_root, tests_root, artifact_root, workspace_root,
        )
        version = (host_version_probe or _probe_host_version)(request.host)
        if version != HOST_SPECS[request.host].verified_version:
            result = TestRunResult(
                "BLOCKED", artifacts, (), "docker", request.host, request.model, "unavailable",
                blocked_reason="container Agent host version is unavailable or unsupported",
            )
            _write_worker_result(result, artifacts, request.selections)
            return 3
        selections = _load_selections(request, repository)
        environment_variables, store_available, store_secret_values = _credential_material(request)
        environment = TestEnvironment(
            repository, "docker", request.host, request.model, request.reasoning,
            secret_values=(
                tuple(value for name, value in environment_variables if name in request.credential_environment)
                + store_secret_values
            ),
            environment_variables=environment_variables,
            command_environment_variables=(),
            command_path=f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
            timeout_seconds=request.timeout_seconds,
            workspace_root=workspace,
            command_prefix=(
                sys.executable, str(Path(network_guard.__file__).resolve()),
                "--read-only", str(repository), "--read-only", str(tests),
                "--read-write", str(artifacts), "--read-write", str(workspace), "--",
            ),
        )
        agent_executor = AgentExecutor(
            credential_available=lambda host: host == request.host and (
                any(name in request.credential_environment for name, _value in environment_variables)
                or store_available
            ),
            credential_unavailable_reason=lambda host: f"credentials are unavailable for Agent host {host}",
        )
        results: list[CollectionResult | CoreCollectionResult] = []
        for item in selections:
            if isinstance(item, Path):
                # Import lazily so CLI result rendering remains a consumer, not a worker dependency cycle.
                from .test_cli import _run_core_path
                results.append(_run_core_path(item, repository, artifacts, environment))
            else:
                from .test_artifacts import safe_artifact_id
                collection_root = artifacts / safe_artifact_id(f"{item.target.kind}-{item.target.target_id}")
                results.append(run_collection(item, environment, collection_root, agent_executor=agent_executor))
        status: Literal["PASS", "FAIL", "BLOCKED"]
        if any(item.status == "BLOCKED" for item in results):
            status = "BLOCKED"
        elif any(item.status == "FAIL" for item in results):
            status = "FAIL"
        else:
            status = "PASS"
        result = TestRunResult(status, artifacts, tuple(results), "docker", request.host, request.model, version)
        _write_worker_result(result, artifacts, request.selections, selections)
        return {"PASS": 0, "FAIL": 1, "BLOCKED": 3}[status]
    except (OSError, ValueError, TestManifestError):
        try:
            artifacts = _real_mount_root(artifact_root, "artifact root", writable=True)
            result = TestRunResult(
                "BLOCKED", artifacts, (), "docker", request.host, request.model, "unavailable",
                blocked_reason="container test execution is unavailable",
            )
            _write_worker_result(result, artifacts, request.selections)
        except (OSError, ValueError):
            pass
        return 3


def _validate_mount_roots(
    repo_root: Path, tests_root: Path, artifact_root: Path, workspace_root: Path,
) -> tuple[Path, Path, Path, Path]:
    repository = _real_mount_root(repo_root, "repository")
    tests = _real_mount_root(tests_root, "tests")
    artifacts = _real_mount_root(artifact_root, "artifact root", writable=True)
    workspace = _real_mount_root(workspace_root, "workspace root", writable=True)
    repository_tests = _real_mount_root(repository / "tests", "repository tests")
    repository_identity = os.stat(repository_tests)
    tests_identity = os.stat(tests)
    if (repository_identity.st_dev, repository_identity.st_ino) != (tests_identity.st_dev, tests_identity.st_ino):
        raise WorkerRequestError("tests mount does not match repository tests")
    for writable in (artifacts, workspace):
        if writable == repository or writable.is_relative_to(repository) or repository.is_relative_to(writable):
            raise WorkerRequestError("writable mount must be separate from the repository")
    return repository, tests, artifacts, workspace


def _real_mount_root(path: Path, label: str, *, writable: bool = False) -> Path:
    candidate = Path(path).absolute()
    mode = os.lstat(candidate).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise WorkerRequestError(f"{label} must be a real directory")
    if writable and not os.access(candidate, os.W_OK | os.X_OK):
        raise WorkerRequestError(f"{label} is not writable")
    return candidate


def _load_selections(request: WorkerRequest, repository: Path) -> tuple[object, ...]:
    loaded: list[object] = []
    for selection in request.selections:
        path = repository.joinpath(*PurePosixPath(selection.path).parts)
        if selection.kind == "core":
            from .test_cli import _safe_direct_test_path
            safe = _safe_direct_test_path(selection.path, repository)
            loaded.append(safe.relative_to(repository))
            continue
        collection = load_test_collection(path, repository)
        if collection.target.kind != selection.kind:
            raise WorkerRequestError("selection kind does not match manifest target")
        loaded.append(collection)
    return tuple(loaded)


def _credential_material(
    request: WorkerRequest,
) -> tuple[tuple[tuple[str, str], ...], bool, tuple[str, ...]]:
    values = tuple(
        (name, os.environ[name]) for name in request.credential_environment
        if isinstance(os.environ.get(name), str) and os.environ[name]
    )
    store, runtime = {
        "codex": (Path("/credentials/codex/auth.json"), (("CODEX_HOME", "/credentials/codex"),)),
        "claude-code": (Path("/credentials/claude-code/.credentials.json"), (("CLAUDE_CONFIG_DIR", "/credentials/claude-code"),)),
        "opencode": (Path("/credentials/opencode/auth.json"), (("XDG_DATA_HOME", "/credentials"),)),
    }[request.host]
    store_secrets = _credential_file_secrets(store)
    store_available = store_secrets is not None
    return values + (runtime if store_available else ()), store_available, (() if store_secrets is None else store_secrets)


def _credential_file_secrets(path: Path) -> tuple[str, ...] | None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        raw = os.read(descriptor, 64 * 1024 + 1)
        if len(raw) > 64 * 1024 or os.read(descriptor, 1):
            return None
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        secrets: list[str] = []
        _collect_string_values(value, secrets)
        return tuple(dict.fromkeys(secrets))
    except (OSError, UnicodeError, json.JSONDecodeError, WorkerRequestError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _collect_string_values(value: object, output: list[str]) -> None:
    if isinstance(value, str):
        if len(value) >= 4:
            output.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_string_values(item, output)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_string_values(item, output)


def _probe_host_version(host: str) -> str | None:
    try:
        completed = subprocess.run(
            (HOST_SPECS[host].executable, "--version"), text=True, capture_output=True,
            timeout=10, check=False,
            env={"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None
    version = parse_host_version(host, completed.stdout)
    return version if completed.returncode == 0 else None


def _write_worker_result(
    result: TestRunResult,
    artifact_root: Path,
    selections: tuple[WorkerSelection, ...] = (),
    loaded_selections: tuple[object, ...] = (),
) -> None:
    payload = {
        "schema_version": 1,
        "status": result.status,
        "runner": result.runner,
        "host": result.host,
        "model": result.model,
        "host_version": result.host_version,
        "blocked_reason": result.blocked_reason,
        "collections": [
            _collection_payload(
                item, artifact_root, selection.path,
                loaded_selections[index] if index < len(loaded_selections) else None,
            )
            for index, (item, selection) in enumerate(zip(result.collections, selections))
        ],
    }
    path = artifact_root / "result.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def _collection_payload(collection, root: Path, selection_path: str, selected: object | None = None) -> dict[str, object]:
    core = collection.target.target_id == "core"
    return {
        "kind": "core" if core else collection.target.kind,
        "target_id": "core" if core else collection.target.target_id,
        "selection_path": selection_path,
        "status": collection.status,
        "cases": [
            _case_payload(
                case, root,
                selected.cases[index] if isinstance(selected, TestCollection) and index < len(selected.cases) else None,
            )
            for index, case in enumerate(collection.cases)
        ],
    }


def _case_payload(case, root: Path, selected_case=None) -> dict[str, object]:
    actions = list(case.actions)
    if selected_case is not None:
        declared = (() if selected_case.prepare is None else (selected_case.prepare,)) + selected_case.steps + (selected_case.post_check,)
        observed_ids = tuple(action.action_id for action in actions)
        expected_prefix = tuple(action.action_id for action in declared[:len(actions)])
        if observed_ids != expected_prefix:
            raise WorkerRequestError("executed actions do not match the selected manifest")
        for action in declared[len(actions):]:
            actions.append(ActionResult(
                action.action_id, "blocked", None,
                case.artifact_dir / "actions" / safe_artifact_id(action.action_id),
            ))
    return {
        "case_id": case.case_id,
        "status": case.status,
        "artifact_dir": _relative_artifact(case.artifact_dir, root),
        "actions": [{
            "action_id": action.action_id,
            "status": action.status,
            "exit_code": action.exit_code,
            "artifact_dir": _relative_artifact(action.artifact_dir, root),
        } for action in actions],
    }


def _relative_artifact(path: Path, root: Path) -> str:
    try:
        value = Path(path).absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise WorkerRequestError("result artifact path escapes artifact root") from exc
    if not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise WorkerRequestError("result artifact path is unsafe")
    return value.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hwskill.test_worker")
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    if args.request != "/run/request.json":
        return 3
    try:
        request = load_worker_request(Path(args.request))
    except WorkerRequestError:
        return 3
    return execute_worker(request)


if __name__ == "__main__":
    raise SystemExit(main())
