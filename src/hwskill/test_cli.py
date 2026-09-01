"""CLI-facing routing and rendering for declarative test collections.

The standard Docker executor intentionally lives behind this module's execution
boundary.  Task 8 provides that implementation; until then Docker requests are
reported as BLOCKED instead of being silently run on the host.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
from typing import Literal, Protocol, TextIO

from .hosts import HOST_SPECS
from .pending_verification import VerificationResult, clear_pending_verification
from .test_artifacts import ActionResult, CaseResult, CollectionResult
from .test_configuration import TestConfiguration, TestConfigurationError, load_test_configuration, resolve_host_model
from .test_impact import AffectedTestsBlocked, TestImpactError, select_affected_tests
from .test_manifest import TestManifestError, TestTarget, discover_test_collections, load_test_collection
from .test_runner import TestEnvironment, run_collection
from .test_setup import inspect_test_setup


PASS = 0
FAIL = 1
USAGE = 2
BLOCKED = 3

RunStatus = Literal["PASS", "FAIL", "BLOCKED"]
class TestCliUsageError(ValueError):
    """A test command target is syntactically valid CLI input but unsafe/invalid."""


@dataclass(frozen=True)
class CoreCollectionResult:
    target: TestTarget
    status: RunStatus
    cases: tuple[CaseResult, ...]


@dataclass(frozen=True)
class TestRunResult:
    status: RunStatus
    artifact_root: Path
    collections: tuple[CollectionResult | CoreCollectionResult, ...]
    runner: str
    host: str
    model: str
    host_version: str
    pending_cleared: bool = False
    blocked_reason: str | None = None


class TestExecutionBoundary(Protocol):
    """Execution seam for the local debugger and the future standard runner."""

    def run(
        self,
        collections: Sequence[object],
        environment: TestEnvironment,
        artifact_root: Path,
    ) -> TestRunResult:
        raise NotImplementedError


class LocalTestExecutionBoundary:
    """Explicit host-local debugging runner; not the standard Docker path."""

    def run(
        self,
        collections: Sequence[object],
        environment: TestEnvironment,
        artifact_root: Path,
    ) -> TestRunResult:
        results: list[CollectionResult | CoreCollectionResult] = []
        try:
            for item in collections:
                if isinstance(item, Path):
                    results.append(_run_core_path(item, environment.repo_root, artifact_root, environment))
                else:
                    collection_root = artifact_root / _safe_artifact_component(
                        f"{item.target.kind}-{item.target.target_id}"
                    )
                    results.append(run_collection(item, environment, collection_root))
        except (OSError, ValueError) as exc:
            return TestRunResult(
                "BLOCKED", artifact_root, tuple(results), environment.runner, environment.host,
                environment.model, HOST_SPECS[environment.host].verified_version,
                blocked_reason=str(exc),
            )
        return TestRunResult(
            _aggregate_status(results), artifact_root, tuple(results), environment.runner, environment.host,
            environment.model, HOST_SPECS[environment.host].verified_version,
        )


class UnavailableDockerExecutionBoundary:
    """Task-7 placeholder that refuses to represent host-local work as Docker work."""

    def run(
        self,
        collections: Sequence[object],
        environment: TestEnvironment,
        artifact_root: Path,
    ) -> TestRunResult:
        del collections
        return TestRunResult(
            "BLOCKED", artifact_root, (), environment.runner, environment.host, environment.model,
            HOST_SPECS[environment.host].verified_version,
            blocked_reason="standard Docker test runner is unavailable; Task 8 has not provided an implementation",
        )


def run_test_command(
    args,
    repo_root: Path,
    stdout: TextIO,
    stderr: TextIO,
    *,
    execution_boundary: TestExecutionBoundary | None = None,
) -> int:
    """Dispatch exactly one positional test target without invoking integrity checks."""
    root = Path(repo_root).resolve()
    try:
        if args.test_target == "setup":
            return _run_setup_check(args, stdout, stderr)
        selections, affected = _select_collections(args, root)
        _validate_option_scope(args)
        configuration = _configured_test_configuration(args)
        host, model = resolve_host_model(configuration, host=args.host)
    except (TestCliUsageError, TestConfigurationError, TestManifestError, ValueError) as exc:
        print(f"hwskill test: {exc}", file=stderr)
        return USAGE
    except (AffectedTestsBlocked, TestImpactError) as exc:
        result = _blocked_run(root, args, str(exc))
        _write_run(result, bool(args.json), stdout)
        return BLOCKED

    artifact_root = Path(tempfile.mkdtemp(prefix="hwskill-test-artifacts-"))
    environment = TestEnvironment(
        repo_root=root, runner=configuration.runner, host=host, model=model.model, reasoning=model.reasoning,
    )
    boundary = execution_boundary or (
        LocalTestExecutionBoundary() if configuration.runner == "local" else UnavailableDockerExecutionBoundary()
    )
    result = boundary.run(selections, environment, artifact_root)
    if affected and result.status == "PASS":
        selection = args._affected_selection
        evidence = VerificationResult(
            selection, dict(selection.digests),
            tuple((_collection_case_id(item), item.status) for item in result.collections),
        )
        result = replace(result, pending_cleared=clear_pending_verification(root, evidence))
    _write_run(result, bool(args.json), stdout)
    return _exit_code(result.status)


def _configured_test_configuration(args) -> TestConfiguration:
    configuration = load_test_configuration()
    if args.runner is not None:
        configuration = replace(configuration, runner=args.runner)
    return configuration


def _run_setup_check(args, stdout: TextIO, stderr: TextIO) -> int:
    if args.test_id is not None or args.base is not None:
        print("hwskill test setup: setup accepts neither a test ID nor --base", file=stderr)
        return USAGE
    if not args.check:
        result = _blocked_run(
            Path(args.repo_root or Path.cwd()), args,
            "test setup without --check requires the standard Docker runner from Task 8",
        )
        _write_run(result, bool(args.json), stdout)
        return BLOCKED
    try:
        configuration = _configured_test_configuration(args)
        if args.host is not None:
            host, _ = resolve_host_model(configuration, host=args.host)
            configuration = replace(configuration, default_host=host)
        host, model = resolve_host_model(configuration)
        report = inspect_test_setup(configuration, _setup_subprocess_runner)
    except (TestConfigurationError, ValueError, OSError) as exc:
        print(f"hwskill test setup: {exc}", file=stderr)
        return USAGE
    data = {
        "status": report.status,
        "runner": configuration.runner,
        "host": host,
        "model": model.model,
        "reasoning": model.reasoning,
        "version": HOST_SPECS[host].verified_version,
        "checks": [asdict(item) for item in report.checks],
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), file=stdout)
    else:
        print(f"STATUS      {report.status}", file=stdout)
        print(f"RUNNER      {configuration.runner}", file=stdout)
        print(f"HOST        {host}", file=stdout)
        print(f"MODEL       {model.model}", file=stdout)
        print(f"VERSION     {HOST_SPECS[host].verified_version}", file=stdout)
        print("CHECK                 STATUS   DETAIL", file=stdout)
        for item in report.checks:
            print(f"{item.name:<21} {item.status:<8} {item.detail}", file=stdout)
    return PASS if report.status == "READY" else BLOCKED


def _setup_subprocess_runner(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)


def _select_collections(args, root: Path) -> tuple[tuple[object, ...], bool]:
    target = args.test_target
    selector = args.test_id
    if target == "all":
        _reject_selector(target, selector)
        collections: list[object] = list(discover_test_collections(root))
        if _has_core_tests(root):
            collections.insert(0, Path("tests/core"))
        return tuple(collections), False
    if target in {"skills", "profiles"}:
        collections = tuple(
            collection for collection in discover_test_collections(root)
            if collection.target.kind == target[:-1]
        )
        if selector is None:
            return collections, False
        selected = tuple(collection for collection in collections if collection.target.target_id == selector)
        if not selected:
            raise TestCliUsageError(f"no {target[:-1]} test collection exists for: {selector}")
        return selected, False
    if target == "affected":
        _reject_selector(target, selector)
        selection = select_affected_tests(root, args.base)
        args._affected_selection = selection
        entries: list[object] = []
        if selection.core:
            entries.append(Path("tests/core"))
        for path in selection.collection_paths:
            entries.append(load_test_collection(_safe_direct_test_path(path.as_posix(), root), root))
        return tuple(entries), True
    if selector is not None:
        raise TestCliUsageError(f"{target} accepts no test ID")
    path = _safe_direct_test_path(target, root)
    if path.parts[-3:-1] == ("tests", "core") or path.is_relative_to(root / "tests" / "core"):
        return (path.relative_to(root),), False
    return (load_test_collection(path, root),), False


def _reject_selector(target: str, selector: str | None) -> None:
    if selector is not None:
        raise TestCliUsageError(f"{target} accepts no test ID")


def _validate_option_scope(args) -> None:
    if args.base is not None and args.test_target != "affected":
        raise TestCliUsageError("--base is only valid with affected")
    if args.check:
        raise TestCliUsageError("--check is only valid with setup")


def _has_core_tests(root: Path) -> bool:
    directory = root / "tests" / "core"
    if not directory.exists():
        return False
    if directory.is_symlink() or not directory.is_dir():
        raise TestCliUsageError("tests/core must be a real directory")
    return any(item.is_file() and not item.is_symlink() and item.name.startswith("test_") and item.suffix == ".py"
               for item in directory.iterdir())


def _safe_direct_test_path(value: str, root: Path) -> Path:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise TestCliUsageError("test path must be repository-relative under tests/core, tests/skills, or tests/profiles")
    parts = path.parts
    if len(parts) < 3 or parts[:2] not in {("tests", "core"), ("tests", "skills"), ("tests", "profiles")}:
        raise TestCliUsageError("test path must be under tests/core, tests/skills, or tests/profiles")
    candidate = root.joinpath(*parts)
    current = root
    for part in parts:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except OSError as exc:
            raise TestCliUsageError(f"cannot inspect test path: {value}") from exc
        if stat.S_ISLNK(mode):
            raise TestCliUsageError("test path must not include a symlink")
    if not stat.S_ISREG(os.lstat(candidate).st_mode):
        raise TestCliUsageError("test path must be a regular file")
    if parts[:2] == ("tests", "core"):
        if candidate.suffix != ".py" or not candidate.name.startswith("test_"):
            raise TestCliUsageError("core test path must name a test_*.py file")
    elif candidate.name != "test.yaml":
        raise TestCliUsageError("Skill and Profile test paths must name test.yaml")
    return candidate


def _run_core_path(path: Path, root: Path, artifact_root: Path, environment: TestEnvironment) -> CoreCollectionResult:
    relative = Path(path)
    if relative == Path("tests/core"):
        arguments = (sys.executable, "-m", "unittest", "discover", "-s", "tests/core", "-t", ".")
        case_id = "core"
    else:
        full = _safe_direct_test_path(relative.as_posix(), root)
        module = ".".join(full.relative_to(root).with_suffix("").parts)
        arguments = (sys.executable, "-m", "unittest", module)
        case_id = relative.as_posix()
    case_dir = artifact_root / "core" / _safe_artifact_component(case_id)
    action_dir = case_dir / "actions" / "unittest"
    action_dir.mkdir(parents=True, exist_ok=False)
    try:
        completed = subprocess.run(arguments, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=environment.timeout_seconds, check=False)
        action_status = "completed" if completed.returncode == 0 else "failed"
        status: RunStatus = "PASS" if completed.returncode == 0 else "FAIL"
        exit_code: int | None = completed.returncode
    except (OSError, subprocess.SubprocessError, TimeoutError):
        action_status, status, exit_code = "blocked", "BLOCKED", None
    action = ActionResult("unittest", action_status, exit_code, action_dir)
    case = CaseResult(case_id, status, case_dir, (action,))
    return CoreCollectionResult(TestTarget("profile", "core"), status, (case,))


def _safe_artifact_component(value: str) -> str:
    return value.replace("/", "-").replace("\\", "-")


def _blocked_run(root: Path, args, reason: str, *, host: str = "unresolved", model: str = "unresolved") -> TestRunResult:
    return TestRunResult("BLOCKED", root / "artifacts" / "hwskill-test-unavailable", (),
                         args.runner or "configured", host, model,
                         HOST_SPECS[host].verified_version if host in HOST_SPECS else "unavailable",
                         blocked_reason=reason)


def _aggregate_status(items: Sequence[CollectionResult | CoreCollectionResult]) -> RunStatus:
    if any(item.status == "BLOCKED" for item in items):
        return "BLOCKED"
    if any(item.status == "FAIL" for item in items):
        return "FAIL"
    return "PASS"


def _collection_case_id(item: CollectionResult | CoreCollectionResult) -> str:
    if item.target.target_id == "core":
        return "core"
    return f"{item.target.kind}:{item.target.target_id}"


def _exit_code(status: RunStatus) -> int:
    return {"PASS": PASS, "FAIL": FAIL, "BLOCKED": BLOCKED}[status]


def _write_run(result: TestRunResult, json_output: bool, stdout: TextIO) -> None:
    data = _run_data(result)
    if json_output:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), file=stdout)
        return
    print(f"STATUS      {result.status}", file=stdout)
    print(f"RUNNER      {result.runner}", file=stdout)
    print(f"HOST        {result.host}", file=stdout)
    print(f"MODEL       {result.model}", file=stdout)
    print(f"VERSION     {result.host_version}", file=stdout)
    print(f"ARTIFACTS   {result.artifact_root}", file=stdout)
    if result.blocked_reason is not None:
        print(f"BLOCKED     {result.blocked_reason}", file=stdout)
    for collection in data["collections"]:
        print(f"COLLECTION  {collection['target']:<24} {collection['status']}", file=stdout)
        for case in collection["cases"]:
            print(f"  CASE      {case['id']:<22} {case['status']:<8} {case['artifact_dir']}", file=stdout)
            for action in case["actions"]:
                code = "-" if action["exit_code"] is None else str(action["exit_code"])
                print(f"    ACTION  {action['id']:<20} {action['status']:<9} exit={code:<3} {action['artifact_dir']}", file=stdout)
            post = case["post_check"]
            code = "-" if post["exit_code"] is None else str(post["exit_code"])
            print(f"    POST    {post['id']:<22} {post['status']:<9} exit={code:<3} {post['artifact_dir']}", file=stdout)
    if result.pending_cleared:
        print("PENDING     CLEARED", file=stdout)


def _run_data(result: TestRunResult) -> dict[str, object]:
    collections = []
    for collection in result.collections:
        cases = []
        for case in collection.cases:
            actions = [_action_data(action) for action in case.actions]
            post = actions[-1] if actions else {"id": "unavailable", "status": "blocked", "exit_code": None, "artifact_dir": str(case.artifact_dir)}
            cases.append({
                "id": case.case_id,
                "status": case.status,
                "artifact_dir": str(case.artifact_dir),
                "actions": actions,
                "post_check": post,
            })
        kind = "core" if collection.target.target_id == "core" else collection.target.kind
        target = "core" if kind == "core" else f"{kind}:{collection.target.target_id}"
        collections.append({"target": target, "status": collection.status, "cases": cases})
    return {
        "status": result.status,
        "runner": result.runner,
        "host": result.host,
        "model": result.model,
        "version": result.host_version,
        "artifact_root": str(result.artifact_root),
        "collections": collections,
        "pending_cleared": result.pending_cleared,
        **({"blocked_reason": result.blocked_reason} if result.blocked_reason is not None else {}),
    }


def _action_data(action: ActionResult) -> dict[str, object]:
    return {
        "id": action.action_id,
        "status": action.status,
        "exit_code": action.exit_code,
        "artifact_dir": str(action.artifact_dir),
    }
