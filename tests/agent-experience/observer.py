#!/usr/bin/env python3
"""Clean-agent evaluation observer.

The observer deliberately ignores prose emitted by the evaluated agent.  A pass
is derived from the filesystem delta, schemas, Git objects, the real host
binary, and machine-readable outputs only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, FormatChecker


HERE = Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"
TASK_SCHEMA = TASKS_DIR / "task.schema.json"
OBSERVATION_SCHEMA = TASKS_DIR / "observation.schema.json"
EVALUATION_SCHEMA = TASKS_DIR / "evaluation.schema.json"
_NO_METRICS = {
    "attempts": 0,
    "success_rate": None,
    "tool_calls": None,
    "human_interventions": None,
    "duration_ms": None,
    "token_usage": None,
}
_VERSION = re.compile(r"(?<![0-9A-Za-z])v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PUBLIC_PATHS = (
    "CONTRIBUTING.md",
    "pyproject.toml",
    "docs/guides",
    "schemas",
    "templates",
    "entries",
    "recommendations",
    "curation",
    "skills-src",
    "site/.generated/directory",
    "scripts/directory",
    "scripts/verification",
    "src/hwskill/__init__.py",
    "src/hwskill/directory",
    "src/hwskill/verification",
)


class ObservationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        data = _canonical_json(value)
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _safe_relative(value: object, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ObservationError(f"{field} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ObservationError(f"{field} is not a safe relative path: {value!r}")
    return path


def _path_under(root: Path, relative: object, *, field: str, must_exist: bool = False) -> Path:
    rel = _safe_relative(relative, field=field)
    if root.is_symlink() or not root.is_dir():
        raise ObservationError(f"workspace root is unavailable or a symlink: {root}")
    current = root
    for part in rel.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ObservationError(f"{field} contains a symlink: {rel.as_posix()}")
            if current != root / Path(*rel.parts) and not stat.S_ISDIR(mode):
                raise ObservationError(f"{field} has a non-directory ancestor: {rel.as_posix()}")
        elif must_exist:
            raise ObservationError(f"{field} does not exist: {rel.as_posix()}")
    return current


def _hash_descriptor(descriptor: int, label: object) -> str:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ObservationError(f"not a private regular file: {label}")
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ObservationError(f"file changed while being observed: {label}")
    return "sha256:" + digest.hexdigest()


def _read_regular(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ObservationError(f"not a private regular file: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ObservationError(f"file changed while being observed: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}

    def visit(directory_fd: int, prefix: PurePosixPath | None = None) -> None:
        for name in sorted(os.listdir(directory_fd)):
            relative = PurePosixPath(name) if prefix is None else prefix / name
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise ObservationError(f"directory changed while being observed: {relative.as_posix()}")
                    visit(child_fd, relative)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(metadata.st_mode):
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise ObservationError(f"file changed while being observed: {relative.as_posix()}")
                    result[relative.as_posix()] = _hash_descriptor(file_fd, relative.as_posix())
                finally:
                    os.close(file_fd)
            else:
                raise ObservationError(f"non-regular filesystem node: {relative.as_posix()}")

    root_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
    return result


def _load_json_file(path: Path) -> Any:
    try:
        body = _read_regular(path)
        return json.loads(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError(f"invalid UTF-8 JSON: {path}: {exc}") from exc


def _validate(value: object, schema_path: Path) -> None:
    schema = _load_json_file(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/" + "/".join(str(part) for part in first.absolute_path)
        raise ObservationError(f"schema validation failed at {location}: {first.message}")


def _load_task(path: Path) -> dict[str, Any]:
    value = _load_json_file(path)
    _validate(value, TASK_SCHEMA)
    return dict(value)


def _check(name: str, category: str, function) -> dict[str, Any]:
    try:
        evidence = function()
        return {"name": name, "category": category, "status": "pass", "reason": "deterministic check passed", "evidence": evidence or {}}
    except (ObservationError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"name": name, "category": category, "status": "fail", "reason": str(exc), "evidence": {}}


def _public_inputs(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    checked: list[str] = []
    for value in task["public_inputs"]:
        path = _path_under(workspace, value, field="public input", must_exist=True)
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ObservationError(f"public input is not a regular file or directory: {value}")
        checked.append(value)
    return {"paths": checked}


def _file_scope(task: Mapping[str, Any], workspace: Path, baseline_path: Path) -> dict[str, Any]:
    baseline = _load_json_file(baseline_path)
    if not isinstance(baseline, Mapping) or baseline.get("schema_version") != 1 or not isinstance(baseline.get("files"), Mapping):
        raise ObservationError("baseline manifest has an invalid shape")
    before = {str(key): str(value) for key, value in baseline["files"].items()}
    after = _manifest(workspace)
    changes = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    unexpected = [
        path
        for path in changes
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in task["allowed_changes"])
    ]
    if unexpected:
        error = ObservationError("files outside the declared task scope changed")
        error.evidence = {"changes": changes, "unexpected_changes": unexpected}  # type: ignore[attr-defined]
        raise error
    return {"changes": changes, "unexpected_changes": []}


def _check_with_evidence(name: str, category: str, function) -> dict[str, Any]:
    try:
        evidence = function()
        return {"name": name, "category": category, "status": "pass", "reason": "deterministic check passed", "evidence": evidence or {}}
    except (ObservationError, OSError, ValueError, subprocess.SubprocessError) as exc:
        evidence = getattr(exc, "evidence", {})
        return {"name": name, "category": category, "status": "fail", "reason": str(exc), "evidence": evidence}


def _required_paths(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    checked: list[str] = []
    for item in task["required_paths"]:
        path = _path_under(workspace, item["path"], field="required path", must_exist=True)
        mode = path.lstat().st_mode
        expected = item["kind"]
        if expected == "file" and not stat.S_ISREG(mode):
            raise ObservationError(f"required regular file is missing: {item['path']}")
        if expected == "directory" and not stat.S_ISDIR(mode):
            raise ObservationError(f"required directory is missing: {item['path']}")
        checked.append(item["path"])
    return {"paths": checked}


def _schema_checks(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    checked: list[str] = []
    for item in task["schema_checks"]:
        document_path = _path_under(workspace, item["path"], field="schema document", must_exist=True)
        schema_path = _path_under(workspace, item["schema"], field="schema", must_exist=True)
        _validate(_load_json_file(document_path), schema_path)
        checked.append(item["path"])
    return {"documents": checked}


def _directory_checks(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    for item in task["directory_checks"]:
        actual = _path_under(workspace, item["actual"], field="installed directory", must_exist=True)
        expected = _path_under(workspace, item["expected"], field="reviewed directory", must_exist=True)
        actual_manifest = _manifest(actual)
        expected_manifest = _manifest(expected)
        if actual_manifest != expected_manifest:
            raise ObservationError(f"installed directory is incomplete or differs from reviewed content: {item['actual']}")
        checked.append({"actual": item["actual"], "expected": item["expected"], "files": len(actual_manifest)})
    return {"directories": checked}


def _repository_validation(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    if not task["repository_validation"]:
        return {"applicable": False}
    from hwskill.directory.entries import validate_repository

    report = validate_repository(workspace)
    if report.result != "pass":
        issues = [
            {"file": issue.file, "field": issue.field, "code": issue.code, "severity": issue.severity}
            for issue in report.issues
        ]
        error = ObservationError("repository validation did not pass")
        error.evidence = {"issues": issues}  # type: ignore[attr-defined]
        raise error
    return {"applicable": True, "result": report.result, "input_digest": report.input_digest}


def _probe_host(
    executable: Path,
    *,
    forbidden_roots: Iterable[Path] = (),
    allow_test: bool = False,
) -> dict[str, Any]:
    if not executable.is_absolute():
        raise ObservationError("host executable path must be absolute")
    try:
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise ObservationError("host executable is unavailable") from exc
    for root in forbidden_roots:
        try:
            forbidden = root.resolve(strict=False)
        except OSError:
            forbidden = root.absolute()
        if resolved == forbidden or forbidden in resolved.parents:
            raise ObservationError(f"host executable is inside a writable or evaluated tree: {forbidden}")
    if not allow_test:
        allowlist = {Path("/usr/bin/codex"), Path("/usr/local/bin/codex"), Path("/opt/codex/bin/codex")}
        if executable not in allowlist and resolved not in allowlist:
            raise ObservationError("host executable is not in the fixed production allowlist")
        for component in (resolved, *resolved.parents):
            metadata = component.stat()
            if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ObservationError("production host executable or an ancestor is not root-owned and non-writable")
            if component == Path("/"):
                break
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return _probe_host_descriptor(descriptor, resolved)
    finally:
        os.close(descriptor)


def _probe_host_descriptor(descriptor: int, path: Path) -> dict[str, Any]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & stat.S_IXUSR:
        raise ObservationError("host executable is not an executable regular file")
    completed = subprocess.run(
        [f"/proc/self/fd/{descriptor}", "--version"],
        text=True,
        capture_output=True,
        check=False,
        pass_fds=(descriptor,),
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    match = _VERSION.search(f"{completed.stdout}\n{completed.stderr}")
    if completed.returncode != 0 or match is None:
        raise ObservationError("cannot identify the Codex host version")
    after = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != (after.st_dev, after.st_ino):
        raise ObservationError("host executable changed during its version probe")
    return {
        "path": str(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "version": match.group(1),
    }


def _probe_production_host_fd(descriptor: int, executable: Path, forbidden_roots: Iterable[Path]) -> dict[str, Any]:
    proof = _probe_host(executable, forbidden_roots=forbidden_roots, allow_test=False)
    opened = _probe_host_descriptor(descriptor, Path(proof["path"]))
    if (opened["device"], opened["inode"], opened["version"]) != (
        proof["device"], proof["inode"], proof["version"]
    ):
        raise ObservationError("opened host descriptor differs from the trusted path probe")
    return opened


def _host_check(task: Mapping[str, Any], workspace: Path, metadata_path: Path | None) -> dict[str, Any]:
    expectation = task["host_expectation"]
    if expectation is None:
        return {"applicable": False}
    if metadata_path is None:
        raise ObservationError("host run metadata is required")
    metadata = _load_json_file(metadata_path)
    required = {
        "schema_version", "host", "host_version", "host_executable", "model", "reasoning_effort",
        "host_identity", "invocation", "started_at", "completed_at", "exit_code", "metrics",
    }
    if not isinstance(metadata, Mapping) or not required.issubset(metadata):
        raise ObservationError("host run metadata is incomplete")
    for field in ("host", "model", "reasoning_effort"):
        if metadata[field] != expectation[field]:
            raise ObservationError(f"run used unexpected {field}: {metadata[field]!r}")
    if metadata["exit_code"] != 0:
        raise ObservationError(f"Luna runner exited with status {metadata['exit_code']}")
    invocation = metadata["invocation"]
    if not isinstance(invocation, list) or not all(isinstance(value, str) for value in invocation):
        raise ObservationError("host invocation is not a string array")
    required_flags = {"--ephemeral", "--ignore-user-config", "--ignore-rules"}
    if not required_flags.issubset(invocation):
        raise ObservationError("host invocation did not disable persisted sessions, user config, and rules")
    try:
        model_index = invocation.index("--model")
        config_index = invocation.index("-c")
    except ValueError as exc:
        raise ObservationError("host invocation did not pin model and reasoning effort") from exc
    if invocation[model_index + 1:model_index + 2] != ["gpt-5.6-luna"] or invocation[config_index + 1:config_index + 2] != ['model_reasoning_effort="medium"']:
        raise ObservationError("host invocation did not pin Luna / medium")
    configuration_values = {invocation[index + 1] for index, value in enumerate(invocation[:-1]) if value == "-c"}
    if 'shell_environment_policy.inherit="none"' not in configuration_values:
        raise ObservationError("host tool environment may inherit the API credential")
    executable = Path(str(metadata["host_executable"]))
    proof = _probe_host(executable, forbidden_roots=(workspace,), allow_test=True)
    resolved_executable = Path(proof["path"])
    workspace_resolved = workspace.resolve(strict=True)
    if resolved_executable == workspace_resolved or workspace_resolved in resolved_executable.parents:
        raise ObservationError("host executable came from the evaluated workspace")
    recorded_identity = metadata["host_identity"]
    if not isinstance(recorded_identity, Mapping) or {
        "device", "inode"
    } - set(recorded_identity):
        raise ObservationError("recorded host inode identity is absent")
    if (recorded_identity["device"], recorded_identity["inode"]) != (proof["device"], proof["inode"]):
        raise ObservationError("recorded host executable inode changed")
    if proof["version"] != metadata["host_version"]:
        raise ObservationError("recorded host version does not match the executable")
    return {"host": metadata["host"], "host_version": proof["version"], "model": metadata["model"], "reasoning_effort": metadata["reasoning_effort"]}


def _json_pointer(value: object, pointer: str) -> object:
    current = value
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ObservationError(f"JSON pointer is absent: {pointer}")
    return current


def _normalise_repository(value: object) -> str:
    if not isinstance(value, str):
        raise ObservationError("install repository is missing")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ObservationError("install repository URL is unsafe")
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    host = parsed.hostname.lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def _git(checkout: Path, *arguments: str) -> str:
    with tempfile.TemporaryDirectory(prefix="hwskill-observer-git-") as config_root:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-c", "core.fsmonitor=false",
                "-c", "core.hooksPath=/dev/null",
                "-c", "credential.helper=",
                "-C", str(checkout),
                *arguments,
            ],
            text=True,
            capture_output=True,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": config_root,
                "XDG_CONFIG_HOME": config_root,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    if completed.returncode != 0:
        raise ObservationError(f"Git check failed: {' '.join(arguments)}")
    return completed.stdout.strip()


def _source_checks(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    checked: list[dict[str, str]] = []
    for item in task["source_revision_checks"]:
        checkout = _path_under(workspace, item["checkout"], field="source checkout", must_exist=True)
        install = _load_json_file(_path_under(workspace, item["install"], field="install JSON", must_exist=True))
        report = _load_json_file(_path_under(workspace, item["report"], field="verification report", must_exist=True))
        source = install.get("source") if isinstance(install, Mapping) else None
        if not isinstance(source, Mapping):
            raise ObservationError("install JSON has no source identity")
        requested_ref = source.get("requested_ref")
        if not isinstance(requested_ref, str) or not requested_ref or requested_ref.startswith("-"):
            raise ObservationError("install requested_ref is absent or unsafe")
        head = _git(checkout, "rev-parse", "HEAD")
        requested = _git(checkout, "rev-parse", "--verify", f"{requested_ref}^{{commit}}")
        if head != requested:
            raise ObservationError("checkout HEAD does not match the requested revision")
        if _git(checkout, "status", "--porcelain", "--untracked-files=all"):
            raise ObservationError("source checkout is not clean")
        expected_repository = _normalise_repository(source.get("repository"))
        actual_repository = _normalise_repository(_git(checkout, "remote", "get-url", "origin"))
        if actual_repository != expected_repository:
            raise ObservationError("checkout origin does not match the install source")
        reported = _json_pointer(report, "/source_identity/resolved_revision")
        if reported != head:
            raise ObservationError("verification report revision does not match the acquired Git object")
        checked.append({"checkout": item["checkout"], "resolved_revision": head, "repository": actual_repository})
    return {"sources": checked}


def _json_outputs(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    checked: list[str] = []
    for item in task["json_outputs"]:
        value = _load_json_file(_path_under(workspace, item["path"], field="JSON output", must_exist=True))
        for requirement in item["required_values"]:
            actual = _json_pointer(value, requirement["pointer"])
            if actual != requirement["equals"]:
                raise ObservationError(
                    f"JSON output value differs at {requirement['pointer']}: expected {requirement['equals']!r}, got {actual!r}"
                )
        checked.append(item["path"])
    return {"outputs": checked}


def _yaml_outputs(task: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    from hwskill.directory.yaml_io import load_yaml

    checked: list[str] = []
    for item in task["yaml_outputs"]:
        path = _path_under(workspace, item["path"], field="YAML output", must_exist=True)
        value = load_yaml(path)
        for requirement in item["required_values"]:
            actual = _json_pointer(value, requirement["pointer"])
            if actual != requirement["equals"]:
                raise ObservationError(
                    f"YAML output value differs at {requirement['pointer']}: expected {requirement['equals']!r}, got {actual!r}"
                )
        checked.append(item["path"])
    return {"outputs": checked}


def _metrics(metadata_path: Path | None) -> dict[str, int | float | None]:
    if metadata_path is None:
        return dict(_NO_METRICS)
    metadata = _load_json_file(metadata_path)
    metrics = metadata.get("metrics") if isinstance(metadata, Mapping) else None
    if not isinstance(metrics, Mapping):
        raise ObservationError("run metrics are absent")
    result: dict[str, int | float | None] = dict(_NO_METRICS)
    result["attempts"] = 1
    for field in ("tool_calls", "human_interventions", "duration_ms", "token_usage"):
        value = metrics.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise ObservationError(f"run metric {field} is invalid")
        result[field] = value
    return result


def observe(task_path: Path, workspace: Path, baseline: Path, run_metadata: Path | None) -> dict[str, Any]:
    task = _load_task(task_path)
    checks = [
        {"name": "task definition", "category": "task_definition", "status": "pass", "reason": "task schema passed", "evidence": {"task": task["id"]}},
        _check_with_evidence("public inputs", "public_inputs", lambda: _public_inputs(task, workspace)),
        _check_with_evidence("actual file scope", "file_scope", lambda: _file_scope(task, workspace, baseline)),
        _check_with_evidence("required paths", "directory_completeness", lambda: _required_paths(task, workspace)),
        _check_with_evidence("JSON schemas", "schema", lambda: _schema_checks(task, workspace)),
        _check_with_evidence("complete installed directories", "directory_completeness", lambda: _directory_checks(task, workspace)),
        _check_with_evidence("repository validator", "repository_validation", lambda: _repository_validation(task, workspace)),
        _check_with_evidence("real host and pinned model", "host_identity", lambda: _host_check(task, workspace, run_metadata)),
        _check_with_evidence("source Git revision", "source_revision", lambda: _source_checks(task, workspace)),
        _check_with_evidence("machine JSON values", "output_json", lambda: _json_outputs(task, workspace)),
        _check_with_evidence("machine YAML values", "output_json", lambda: _yaml_outputs(task, workspace)),
    ]
    status = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    measured_metrics = _metrics(run_metadata)
    if run_metadata is not None:
        measured_metrics["success_rate"] = 1.0 if status == "pass" else 0.0
    result = {
        "schema_version": 1,
        "task_id": task["id"],
        "workflow": task["workflow"],
        "tuning": task["tuning"],
        "status": status,
        "observed_at": _utc_now(),
        "checks": checks,
        "metrics": measured_metrics,
    }
    _validate(result, OBSERVATION_SCHEMA)
    return result


def _blocked(tasks_dir: Path, repo_root: Path, isolation_path: Path, reason: str) -> dict[str, Any]:
    isolation = _load_json_file(isolation_path)
    tasks: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        task = _load_task(path)
        checks = [
            {"name": "task definition", "category": "task_definition", "status": "pass", "reason": "task schema passed", "evidence": {"path": path.name}},
            _check_with_evidence("public inputs", "public_inputs", lambda task=task: _public_inputs(task, repo_root)),
        ]
        for category in ("file_scope", "schema", "directory_completeness", "repository_validation", "host_identity", "source_revision", "output_json"):
            checks.append({"name": category.replace("_", " "), "category": category, "status": "not_run", "reason": reason, "evidence": {}})
        tasks.append(
            {
                "task_id": task["id"],
                "workflow": task["workflow"],
                "tuning": task["tuning"],
                "status": "not_run",
                "checks": checks,
                "metrics": dict(_NO_METRICS),
            }
        )
    task_ids = {task["task_id"] for task in tasks}
    if len(tasks) != 5 or len(task_ids) != 5:
        raise ObservationError("evaluation definition must contain exactly five unique tasks")
    if not any(task["tuning"] == "holdout" for task in tasks):
        raise ObservationError("at least one holdout task must remain untuned")
    preflight_failed = any(any(check["status"] == "fail" for check in task["checks"]) for task in tasks)
    result = {
        "schema_version": 1,
        "evaluation_id": "clean-luna-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "status": "fail" if preflight_failed else "blocked",
        "reason": "public input preflight failed" if preflight_failed else reason,
        "generated_at": _utc_now(),
        "isolation": isolation,
        "tasks": tasks,
        "summary": {
            "total": len(tasks), "passed": 0, "failed": 0, "blocked": 0,
            "not_run": len(tasks), "success_rate": None,
        },
    }
    _validate(result, EVALUATION_SCHEMA)
    return result


def _snapshot(workspace: Path) -> dict[str, Any]:
    return {"schema_version": 1, "created_at": _utc_now(), "files": _manifest(workspace)}


def _copy_regular(source: Path, destination: Path) -> None:
    descriptor_in = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor_in)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ObservationError(f"public input is not a regular file: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor_out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, stat.S_IMODE(metadata.st_mode))
        try:
            while chunk := os.read(descriptor_in, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(descriptor_out, view):]
        finally:
            os.close(descriptor_out)
    finally:
            os.close(descriptor_in)


def _copy_tree_fd(source_fd: int, destination_fd: int, prefix: PurePosixPath | None = None) -> None:
    for name in sorted(os.listdir(source_fd)):
        relative = PurePosixPath(name) if prefix is None else prefix / name
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child_source = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            try:
                opened = os.fstat(child_source)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise ObservationError(f"directory changed while being frozen: {relative.as_posix()}")
                os.mkdir(name, 0o700, dir_fd=destination_fd)
                child_destination = os.open(name, _DIRECTORY_FLAGS, dir_fd=destination_fd)
                try:
                    _copy_tree_fd(child_source, child_destination, relative)
                    os.fsync(child_destination)
                finally:
                    os.close(child_destination)
            finally:
                os.close(child_source)
        elif stat.S_ISREG(metadata.st_mode):
            source_file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_fd)
            try:
                opened = os.fstat(source_file)
                if opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise ObservationError(f"file changed while being frozen: {relative.as_posix()}")
                destination_file = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    stat.S_IMODE(metadata.st_mode) & 0o755,
                    dir_fd=destination_fd,
                )
                try:
                    before = os.fstat(source_file)
                    while chunk := os.read(source_file, 1024 * 1024):
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(destination_file, view):]
                    os.fsync(destination_file)
                    after = os.fstat(source_file)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise ObservationError(f"file changed while being frozen: {relative.as_posix()}")
                finally:
                    os.close(destination_file)
            finally:
                os.close(source_file)
        else:
            raise ObservationError(f"workspace contains a symlink or special file: {relative.as_posix()}")


def _freeze_workspace(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise ObservationError("frozen workspace destination must not already exist")
    source_fd = os.open(source, _DIRECTORY_FLAGS)
    try:
        destination.mkdir(mode=0o700, parents=True)
        destination_fd = os.open(destination, _DIRECTORY_FLAGS)
        try:
            _copy_tree_fd(source_fd, destination_fd)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    except Exception:
        if destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise
    finally:
        os.close(source_fd)
    return {"schema_version": 1, "files": len(_manifest(destination))}


def _copy_public_path(source: Path, destination: Path) -> None:
    metadata = source.lstat()
    if stat.S_ISREG(metadata.st_mode):
        _copy_regular(source, destination)
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise ObservationError(f"public input contains a symlink or special file: {source}")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with os.scandir(source) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            _copy_public_path(Path(entry.path), destination / entry.name)


def _prepare_task(repo_root: Path, task_path: Path, destination: Path) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise ObservationError("evaluation project destination must not already exist")
    task = _load_task(task_path)
    destination.mkdir(mode=0o700, parents=True)
    copied: list[str] = []
    try:
        for relative in _PUBLIC_PATHS:
            source = _path_under(repo_root, relative, field="public evaluation input", must_exist=True)
            _copy_public_path(source, destination / Path(*PurePosixPath(relative).parts))
            copied.append(relative)
        package = {
            "schema_version": 1,
            "id": task["id"],
            "workflow": task["workflow"],
            "prompt": task["prompt"],
            "public_inputs": list(task["public_inputs"]),
        }
        _atomic_write_json(destination / ".evaluation/task.json", package)
    except Exception:
        shutil.rmtree(destination)
        raise
    return {"schema_version": 1, "copied": copied, "files": len(_manifest(destination))}


def _task_prompt(path: Path) -> str:
    value = _load_json_file(path)
    expected = {"schema_version", "id", "workflow", "prompt", "public_inputs"}
    if not isinstance(value, Mapping) or set(value) != expected or value.get("schema_version") != 1:
        raise ObservationError("sanitised task package has unexpected fields")
    if not isinstance(value.get("prompt"), str) or not value["prompt"]:
        raise ObservationError("sanitised task package has no prompt")
    return value["prompt"]


def _duration_ms(started_at: str, completed_at: str) -> int:
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    duration = parse(completed_at) - parse(started_at)
    return max(0, int(duration.total_seconds() * 1000))


def _transcript_metrics(path: Path) -> tuple[int, int | None]:
    tool_calls = 0
    token_usage: int | None = None
    if not path.is_file() or path.is_symlink():
        return tool_calls, token_usage
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        item = event.get("item")
        item_type = item.get("type") if isinstance(item, Mapping) else None
        if event.get("type") == "item.completed" and item_type in {"command_execution", "file_change", "mcp_tool_call", "web_search"}:
            tool_calls += 1
        usage = event.get("usage")
        if isinstance(usage, Mapping):
            total = usage.get("total_tokens")
            if type(total) is int and total >= 0:
                token_usage = max(token_usage or 0, total)
            elif type(usage.get("input_tokens")) is int and type(usage.get("output_tokens")) is int:
                measured = usage["input_tokens"] + usage["output_tokens"]
                if measured >= 0:
                    token_usage = max(token_usage or 0, measured)
    return tool_calls, token_usage


def _record_run(
    transcript: Path,
    host_executable: Path,
    host_fd: int | None,
    host_proof: Path | None,
    started_at: str,
    completed_at: str,
    exit_code: int,
    invocation: list[str],
) -> dict[str, Any]:
    if (host_fd is None) != (host_proof is None):
        raise ObservationError("host fd and host proof must be supplied together")
    if host_fd is None:
        proof = _probe_host(host_executable, allow_test=True)
    else:
        expected = _load_json_file(host_proof)
        if not isinstance(expected, Mapping) or set(expected) != {"path", "device", "inode", "version"}:
            raise ObservationError("host proof has an invalid shape")
        proof = _probe_host_descriptor(host_fd, Path(str(expected["path"])))
        if proof != expected:
            raise ObservationError("host fd no longer matches its verified host proof")
    tool_calls, token_usage = _transcript_metrics(transcript)
    return {
        "schema_version": 1,
        "host": "codex",
        "host_version": proof["version"],
        "host_executable": proof["path"],
        "host_identity": {"device": proof["device"], "inode": proof["inode"]},
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "invocation": invocation,
        "started_at": started_at,
        "completed_at": completed_at,
        "exit_code": exit_code,
        "metrics": {
            "attempts": 1,
            "success_rate": None,
            "tool_calls": tool_calls,
            "human_interventions": 0,
            "duration_ms": _duration_ms(started_at, completed_at),
            "token_usage": token_usage,
        },
    }


def _aggregate(observations_dir: Path, isolation_path: Path, tasks_dir: Path) -> dict[str, Any]:
    isolation = _load_json_file(isolation_path)
    expected_tasks = {
        _load_task(path)["id"]
        for path in sorted(tasks_dir.glob("*.json"))
        if not path.name.endswith(".schema.json")
    }
    if len(expected_tasks) != 5:
        raise ObservationError("evaluation definition must contain exactly five unique tasks")
    observations: list[dict[str, Any]] = []
    observed_ids: set[str] = set()
    for path in sorted(observations_dir.glob("*.json")):
        value = _load_json_file(path)
        _validate(value, OBSERVATION_SCHEMA)
        if value["task_id"] in observed_ids:
            raise ObservationError(f"duplicate observation for task {value['task_id']}")
        observed_ids.add(value["task_id"])
        observations.append(dict(value))
    if observed_ids != expected_tasks:
        missing = sorted(expected_tasks - observed_ids)
        unexpected = sorted(observed_ids - expected_tasks)
        raise ObservationError(f"observation set does not cover the five tasks; missing={missing}, unexpected={unexpected}")
    passed = sum(value["status"] == "pass" for value in observations)
    failed = sum(value["status"] == "fail" for value in observations)
    blocked = sum(value["status"] == "blocked" for value in observations)
    not_run = sum(value["status"] == "not_run" for value in observations)
    attempted = passed + failed
    status = "fail" if failed else "blocked" if blocked or not_run else "pass"
    result = {
        "schema_version": 1,
        "evaluation_id": "clean-luna-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "status": status,
        "reason": "one or more deterministic observations failed" if status == "fail" else "all deterministic observations passed",
        "generated_at": _utc_now(),
        "isolation": isolation,
        "tasks": [
            {
                "task_id": value["task_id"], "workflow": value["workflow"], "tuning": value["tuning"],
                "status": value["status"], "checks": value["checks"], "metrics": value["metrics"],
            }
            for value in observations
        ],
        "summary": {
            "total": len(observations), "passed": passed, "failed": failed, "blocked": blocked,
            "not_run": not_run, "success_rate": passed / attempted if attempted else None,
        },
    }
    _validate(result, EVALUATION_SCHEMA)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministically observe clean Luna evaluation artifacts.")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--workspace", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    observed = commands.add_parser("observe")
    observed.add_argument("--task", type=Path, required=True)
    observed.add_argument("--workspace", type=Path, required=True)
    observed.add_argument("--baseline", type=Path, required=True)
    observed.add_argument("--run-metadata", type=Path)
    observed.add_argument("--output", type=Path, required=True)
    blocked = commands.add_parser("blocked")
    blocked.add_argument("--tasks-dir", type=Path, required=True)
    blocked.add_argument("--repo-root", type=Path, required=True)
    blocked.add_argument("--isolation-metadata", type=Path, required=True)
    blocked.add_argument("--reason", required=True)
    blocked.add_argument("--output", type=Path, required=True)
    validate_result = commands.add_parser("validate-result")
    validate_result.add_argument("--input", type=Path, required=True)
    prepare = commands.add_parser("prepare-task")
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--task", type=Path, required=True)
    prepare.add_argument("--destination", type=Path, required=True)
    freeze = commands.add_parser("freeze-workspace")
    freeze.add_argument("--workspace", type=Path, required=True)
    freeze.add_argument("--destination", type=Path, required=True)
    prompt = commands.add_parser("task-prompt")
    prompt.add_argument("--task", type=Path, required=True)
    probe = commands.add_parser("probe-host")
    probe.add_argument("--host-executable", type=Path, required=True)
    probe.add_argument("--forbidden-root", type=Path, action="append", default=[])
    probe.add_argument("--allow-test-host", action="store_true")
    probe.add_argument("--output", type=Path, required=True)
    probe_fd = commands.add_parser("probe-host-fd")
    probe_fd.add_argument("--host-executable", type=Path, required=True)
    probe_fd.add_argument("--host-fd", type=int, required=True)
    probe_fd.add_argument("--forbidden-root", type=Path, action="append", default=[])
    probe_fd.add_argument("--output", type=Path, required=True)
    record = commands.add_parser("record-run")
    record.add_argument("--transcript", type=Path, required=True)
    record.add_argument("--host-executable", type=Path, required=True)
    record.add_argument("--host-fd", type=int)
    record.add_argument("--host-proof", type=Path)
    record.add_argument("--started-at", required=True)
    record.add_argument("--completed-at", required=True)
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--invocation-argument", action="append", default=[])
    record.add_argument("--output", type=Path, required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--observations-dir", type=Path, required=True)
    aggregate.add_argument("--isolation-metadata", type=Path, required=True)
    aggregate.add_argument("--tasks-dir", type=Path, required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "snapshot":
            _atomic_write_json(args.output, _snapshot(args.workspace))
            return 0
        if args.command == "observe":
            result = observe(args.task, args.workspace, args.baseline, args.run_metadata)
            _atomic_write_json(args.output, result)
            return 0 if result["status"] == "pass" else 1
        if args.command == "blocked":
            result = _blocked(args.tasks_dir, args.repo_root, args.isolation_metadata, args.reason)
            _atomic_write_json(args.output, result)
            return 1 if result["status"] == "fail" else 2
        if args.command == "prepare-task":
            print(json.dumps(_prepare_task(args.repo_root, args.task, args.destination), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "freeze-workspace":
            print(json.dumps(_freeze_workspace(args.workspace, args.destination), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "task-prompt":
            print(_task_prompt(args.task))
            return 0
        if args.command == "probe-host":
            proof = _probe_host(
                args.host_executable,
                forbidden_roots=args.forbidden_root,
                allow_test=args.allow_test_host,
            )
            _atomic_write_json(args.output, proof)
            return 0
        if args.command == "probe-host-fd":
            proof = _probe_production_host_fd(args.host_fd, args.host_executable, args.forbidden_root)
            _atomic_write_json(args.output, proof)
            return 0
        if args.command == "record-run":
            value = _record_run(
                args.transcript,
                args.host_executable,
                args.host_fd,
                args.host_proof,
                args.started_at,
                args.completed_at,
                args.exit_code,
                args.invocation_argument,
            )
            _atomic_write_json(args.output, value)
            return 0
        if args.command == "aggregate":
            value = _aggregate(args.observations_dir, args.isolation_metadata, args.tasks_dir)
            _atomic_write_json(args.output, value)
            return 0 if value["status"] == "pass" else 1 if value["status"] == "fail" else 2
        value = _load_json_file(args.input)
        schema = EVALUATION_SCHEMA if isinstance(value, Mapping) and "tasks" in value else OBSERVATION_SCHEMA
        _validate(value, schema)
        return 0
    except (ObservationError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
