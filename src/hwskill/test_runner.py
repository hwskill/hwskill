"""Isolated local runner for declarative Skill and Profile test collections."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
from typing import Literal, Protocol

from .test_artifacts import (
    ActionResult,
    CaseResult,
    CollectionResult,
    safe_artifact_id,
    write_json,
    write_text,
)
from .test_manifest import AgentAction, CommandAction, TestAction, TestCase, TestCollection


@dataclass(frozen=True)
class TestEnvironment:
    repo_root: Path
    runner: str
    host: str
    model: str
    reasoning: str
    secret_values: tuple[str, ...] = ()
    environment_variables: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 30.0
    fixtures_dir: Path | None = None


@dataclass(frozen=True)
class ActionContext:
    workspace: Path
    artifact_dir: Path
    environment: TestEnvironment


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    workspace: Path
    artifact_dir: Path
    environment: TestEnvironment
    actions: tuple[ActionResult, ...]

    def write(self, path: Path) -> None:
        """Write only normalized action metadata, never action output or secrets."""
        actions = {
            item.action_id: {
                "status": item.status,
                "exit_code": item.exit_code,
                "artifact_dir": item.artifact_dir.relative_to(self.artifact_dir).as_posix(),
            }
            for item in self.actions
        }
        write_json(path, {
            "case_id": self.case_id,
            "workspace": str(self.workspace),
            "runner": self.environment.runner,
            "host": self.environment.host,
            "model": self.environment.model,
            "reasoning": self.environment.reasoning,
            "actions": actions,
        }, self.environment.secret_values, sort_keys=False)


class ActionExecutor(Protocol):
    def run(self, action: CommandAction | AgentAction, context: ActionContext) -> ActionResult:
        raise NotImplementedError


class CommandExecutor:
    """Execute a command action under bash in a fresh process group."""

    def run(self, action: CommandAction | AgentAction, context: ActionContext) -> ActionResult:
        if not isinstance(action, CommandAction):
            return _blocked_result(action.action_id, context.artifact_dir, "command executor cannot run an Agent action", context.environment)
        try:
            cwd = _resolve_workdir(context.workspace, action.workdir)
        except ValueError as exc:
            return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)
        return _run_command(action, cwd, context)


class UnavailableAgentExecutor:
    """Task 4 replaces this explicitly unavailable executor with host adapters."""

    def run(self, action: CommandAction | AgentAction, context: ActionContext) -> ActionResult:
        return _blocked_result(
            action.action_id,
            context.artifact_dir,
            f"required Agent executor is unavailable for host {context.environment.host}",
            context.environment,
        )


def run_case(case: TestCase, environment: TestEnvironment, artifact_root: Path) -> CaseResult:
    """Run one case in a fresh workspace and retain normalized evidence."""
    case_dir = Path(artifact_root).absolute() / safe_artifact_id(case.case_id)
    case_dir.mkdir(parents=True, exist_ok=False)
    workspace = Path(tempfile.mkdtemp(prefix="hwskill-test-workspace-"))
    actions: list[ActionResult] = []
    try:
        _copy_fixtures(_fixtures_dir(case, environment), workspace)
    except (OSError, ValueError) as exc:
        return _finish_case(case, case_dir, workspace, {}, environment, tuple(actions), "BLOCKED")

    baseline = _workspace_snapshot(workspace)
    command_executor = CommandExecutor()
    agent_executor: ActionExecutor = UnavailableAgentExecutor()

    if case.prepare is not None:
        prepare_result = _execute(
            _with_case_workdir(case.prepare, case.workdir),
            workspace,
            case_dir,
            environment,
            command_executor,
            agent_executor,
            None,
        )
        actions.append(prepare_result)
        if prepare_result.status != "completed":
            return _finish_case(case, case_dir, workspace, baseline, environment, tuple(actions), "BLOCKED")

    for action in case.steps:
        actions.append(_execute(
            _with_case_workdir(action, case.workdir),
            workspace,
            case_dir,
            environment,
            command_executor,
            agent_executor,
            None,
        ))

    pre_post_context = CaseContext(case.case_id, workspace, case_dir, environment, tuple(actions))
    pre_post_context.write(case_dir / "context.json")
    post_result = _execute(
        _with_case_workdir(case.post_check, case.workdir),
        workspace,
        case_dir,
        environment,
        command_executor,
        agent_executor,
        case_dir / "context.json",
    )
    actions.append(post_result)
    if any(item.status == "blocked" for item in actions):
        status: Literal["PASS", "FAIL", "BLOCKED"] = "BLOCKED"
    elif isinstance(case.post_check, CommandAction):
        status = _post_check_status(post_result.exit_code)
    else:
        status = "BLOCKED"
    return _finish_case(case, case_dir, workspace, baseline, environment, tuple(actions), status)


def run_collection(
    collection: TestCollection,
    environment: TestEnvironment,
    artifact_root: Path,
) -> CollectionResult:
    """Run cases in declaration order; a malformed case cannot crash the collection."""
    results: list[CaseResult] = []
    for case in collection.cases:
        try:
            results.append(run_case(
                case,
                replace(environment, fixtures_dir=collection.fixtures_dir),
                artifact_root,
            ))
        except Exception as exc:  # defensive isolation at collection boundary
            results.append(_exception_case_result(case, environment, artifact_root, exc))
    status: Literal["PASS", "FAIL", "BLOCKED"]
    if any(result.status == "BLOCKED" for result in results):
        status = "BLOCKED"
    elif any(result.status == "FAIL" for result in results):
        status = "FAIL"
    else:
        status = "PASS"
    return CollectionResult(collection.target, status, tuple(results))


def _fixtures_dir(case: TestCase, environment: TestEnvironment) -> Path:
    # Cases intentionally do not carry fixture paths. The collection directory is
    # reconstructed from the repository test root and target-independent case API.
    # Task 3 callers use run_collection; direct run_case accepts no fixtures when
    # the conventional sibling directory cannot be determined.
    del case
    if environment.fixtures_dir is not None:
        return environment.fixtures_dir
    candidates = tuple(sorted((environment.repo_root / "tests").glob("**/fixtures")))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return environment.repo_root / "tests" / "fixtures"
    raise ValueError("direct run_case requires exactly one fixtures directory; use run_collection")


def _with_case_workdir(action: TestAction, case_workdir: str | None) -> TestAction:
    """Apply the schema's action > case > workspace precedence once per action."""
    return action if action.workdir is not None else replace(action, workdir=case_workdir)


def _execute(
    action: TestAction,
    workspace: Path,
    case_dir: Path,
    environment: TestEnvironment,
    command_executor: ActionExecutor,
    agent_executor: ActionExecutor,
    post_check_context: Path | None,
) -> ActionResult:
    action_dir = case_dir / "actions" / safe_artifact_id(action.action_id)
    action_dir.mkdir(parents=True, exist_ok=False)
    context = ActionContext(workspace, action_dir, environment)
    if post_check_context is not None and isinstance(action, CommandAction):
        return _run_post_check(action, context, post_check_context, case_dir)
    executor = command_executor if isinstance(action, CommandAction) else agent_executor
    return executor.run(action, context)


def _run_post_check(action: CommandAction, context: ActionContext, context_path: Path, case_dir: Path) -> ActionResult:
    try:
        cwd = _resolve_workdir(context.workspace, action.workdir)
    except ValueError as exc:
        return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)
    additions = {
        "HWSKILL_TEST_CONTEXT": str(context_path.absolute()),
        "HWSKILL_TEST_ARTIFACTS": str(case_dir.absolute()),
        "HWSKILL_TEST_WORKSPACE": str(context.workspace.absolute()),
    }
    return _run_command(action, cwd, context, additions)


def _run_command(
    action: CommandAction,
    cwd: Path,
    context: ActionContext,
    extra_environment: dict[str, str] | None = None,
) -> ActionResult:
    command_environment = _command_environment(context.environment, context.workspace, extra_environment)
    payload = {"action_id": action.action_id, "kind": "command", "command": action.command, "workdir": str(cwd)}
    try:
        process = subprocess.Popen(
            ("/bin/bash", "-lc", action.command),
            cwd=cwd,
            env=command_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        try:
            _verify_resolved_workdir(context.workspace, cwd)
        except ValueError:
            _terminate_process_group(process)
            process.communicate()
            raise
        try:
            stdout, stderr = process.communicate(timeout=context.environment.timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            stderr = f"{stderr}\naction timed out after {context.environment.timeout_seconds} seconds\n"
            payload.update({"status": "blocked", "exit_code": None, "reason": "timeout"})
            _persist_action(context.artifact_dir, payload, stdout, stderr, context.environment)
            return ActionResult(action.action_id, "blocked", None, context.artifact_dir)
    except (OSError, ValueError) as exc:
        return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment, payload)
    status: Literal["completed", "failed"] = "completed" if process.returncode == 0 else "failed"
    payload.update({"status": status, "exit_code": process.returncode})
    _persist_action(context.artifact_dir, payload, stdout, stderr, context.environment)
    return ActionResult(action.action_id, status, process.returncode, context.artifact_dir)


def _blocked_result(
    action_id: str,
    artifact_dir: Path,
    reason: str,
    environment: TestEnvironment,
    payload: dict[str, object] | None = None,
) -> ActionResult:
    data = payload or {"action_id": action_id}
    data.update({"status": "blocked", "exit_code": None, "reason": reason})
    _persist_action(artifact_dir, data, "", reason + "\n", environment)
    return ActionResult(action_id, "blocked", None, artifact_dir)


def _persist_action(
    artifact_dir: Path,
    result: dict[str, object],
    stdout: str,
    stderr: str,
    environment: TestEnvironment,
) -> None:
    write_json(artifact_dir / "result.json", result, environment.secret_values)
    write_text(artifact_dir / "stdout.log", stdout, environment.secret_values)
    write_text(artifact_dir / "stderr.log", stderr, environment.secret_values)


def _finish_case(
    case: TestCase,
    case_dir: Path,
    workspace: Path,
    baseline: dict[str, tuple[int, str]],
    environment: TestEnvironment,
    actions: tuple[ActionResult, ...],
    status: Literal["PASS", "FAIL", "BLOCKED"],
) -> CaseResult:
    try:
        _write_workspace_diff(case_dir / "workspace.diff", baseline, workspace, environment.secret_values)
        CaseContext(case.case_id, workspace, case_dir, environment, actions).write(case_dir / "context.json")
        return CaseResult(case.case_id, status, case_dir, actions)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _exception_case_result(case: TestCase, environment: TestEnvironment, artifact_root: Path, _error: Exception) -> CaseResult:
    case_dir = Path(artifact_root).absolute() / safe_artifact_id(case.case_id)
    if case_dir.exists() and not case_dir.is_dir():
        case_dir = case_dir.with_name(case_dir.name + "-blocked")
    case_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="hwskill-test-workspace-"))
    _write_workspace_diff(case_dir / "workspace.diff", {}, workspace, environment.secret_values)
    CaseContext(case.case_id, workspace, case_dir, environment, ()).write(case_dir / "context.json")
    shutil.rmtree(workspace, ignore_errors=True)
    return CaseResult(case.case_id, "BLOCKED", case_dir, ())


def _post_check_status(exit_code: int | None) -> Literal["PASS", "FAIL", "BLOCKED"]:
    if exit_code == 0:
        return "PASS"
    if exit_code == 1:
        return "FAIL"
    return "BLOCKED"


def _command_environment(
    environment: TestEnvironment,
    workspace: Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    result = {"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(workspace.absolute())}
    for key, value in environment.environment_variables:
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise ValueError("invalid declared test environment variable")
        result[key] = value
    if extra:
        result.update(extra)
    return result


def _resolve_workdir(workspace: Path, configured: str | None) -> Path:
    candidate = workspace if configured is None else workspace / configured
    return _verify_resolved_workdir(workspace, candidate)


def _verify_resolved_workdir(workspace: Path, candidate: Path) -> Path:
    try:
        root = workspace.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"action workdir is unavailable: {candidate}: {exc}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"action workdir escapes isolated workspace: {candidate}") from exc
    if not resolved.is_dir():
        raise ValueError(f"action workdir is not a directory: {candidate}")
    return resolved


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _copy_fixtures(source: Path, destination: Path) -> None:
    try:
        os.lstat(source)
    except FileNotFoundError:
        return
    _copy_fixture_directory(source, destination, source)


def _copy_fixture_directory(source: Path, destination: Path, root: Path) -> None:
    source_stat = os.lstat(source)
    if not stat.S_ISDIR(source_stat.st_mode):
        raise ValueError(f"fixtures must be a real directory: {source}")
    with os.scandir(source) as entries:
        for entry in sorted(entries, key=lambda value: value.name):
            source_path = Path(entry.path)
            target_path = destination / entry.name
            entry_stat = os.lstat(source_path)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise ValueError(f"fixtures must not contain a symlink: {source_path.relative_to(root)}")
            if stat.S_ISDIR(entry_stat.st_mode):
                target_path.mkdir()
                _copy_fixture_directory(source_path, target_path, root)
            elif stat.S_ISREG(entry_stat.st_mode):
                _copy_regular_fixture(source_path, target_path, entry_stat, root)
            else:
                raise ValueError(f"fixtures contains unsupported fixture: {source_path.relative_to(root)}")


def _copy_regular_fixture(source: Path, destination: Path, expected: os.stat_result, root: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(f"cannot safely open fixture {source.relative_to(root)}: {exc}") from exc
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError(f"fixture changed while copying: {source.relative_to(root)}")
        with os.fdopen(descriptor, "rb", closefd=False) as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
        os.chmod(destination, stat.S_IMODE(expected.st_mode))
    finally:
        os.close(descriptor)
    after = os.lstat(source)
    if (after.st_dev, after.st_ino, after.st_size) != (expected.st_dev, expected.st_ino, expected.st_size):
        raise ValueError(f"fixture changed while copying: {source.relative_to(root)}")


def _workspace_snapshot(workspace: Path) -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for path in _workspace_files(workspace):
        relative = path.relative_to(workspace).as_posix()
        snapshot[relative] = (stat.S_IMODE(os.lstat(path).st_mode), _file_digest(path))
    return snapshot


def _write_workspace_diff(path: Path, before: dict[str, tuple[int, str]], workspace: Path, secrets: tuple[str, ...]) -> None:
    after = _workspace_snapshot(workspace)
    lines: list[str] = []
    for relative in sorted(set(before) | set(after)):
        if relative not in before:
            lines.append(f"A {relative}\n")
        elif relative not in after:
            lines.append(f"D {relative}\n")
        elif before[relative] != after[relative]:
            lines.append(f"M {relative}\n")
    write_text(path, "".join(lines), secrets)


def _workspace_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory, directories, names in os.walk(root, followlinks=False):
        directories.sort()
        for name in sorted(names):
            path = Path(directory) / name
            mode = os.lstat(path).st_mode
            if stat.S_ISREG(mode):
                files.append(path)
            elif stat.S_ISLNK(mode):
                files.append(path)
    return tuple(files)


def _file_digest(path: Path) -> str:
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        return "symlink:" + os.readlink(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
