"""CLI-facing routing and rendering for local-debug and standard Docker tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Literal, Protocol, TextIO

from .docker_test_runner import (
    CoreCollectionResult,
    CredentialFile,
    DockerRunnerUnavailable,
    DockerTestRunner,
    TestRunResult,
)
from .core_timeout import core_timeout_seconds
from .hosts import HOST_SPECS, canonical_host
from .pending_verification import VerificationResult, clear_pending_verification
from .test_artifacts import ActionResult, CaseResult, CollectionResult, write_json, write_text
from .test_configuration import HostModel, TestConfiguration, TestConfigurationError, load_test_configuration, resolve_host_model, write_test_configuration
from .test_impact import AffectedTestsBlocked, TestImpactError, select_affected_tests
from .test_manifest import TestManifestError, TestTarget, discover_test_collections, load_test_collection
from .test_runner import TestEnvironment, run_collection
from .test_setup import configure_test_setup, parse_host_version


PASS = 0
FAIL = 1
USAGE = 2
BLOCKED = 3

RunStatus = Literal["PASS", "FAIL", "BLOCKED"]
_CORE_OUTPUT_LIMIT = 64 * 1024
_CREDENTIAL_ENVIRONMENT_NAMES = {
    "codex": ("CODEX_API_KEY",),
    "claude-code": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "opencode": ("OPENCODE_API_KEY",),
}
_CREDENTIAL_STORE_RELATIVE_PATHS = {
    "codex": Path(".codex/auth.json"),
    "claude-code": Path(".claude/.credentials.json"),
    "opencode": Path(".local/share/opencode/auth.json"),
}
_MINIMAX_AUTH_ENVIRONMENT = "HWSKILL_MINIMAX_AUTH_FILE"
_MINIMAX_PROVIDER = "minimax-cn-coding-plan"
_CORE_UNITTEST_RUNNER = r'''
import importlib
import importlib.util
from pathlib import Path
import sys
import unittest

repository = Path(sys.argv[1])
selected = Path(sys.argv[2]) if sys.argv[2] else None
core = repository / "tests" / "core"
sys.path.insert(0, str(repository))
loader = unittest.defaultTestLoader
def load_selected(selected, index):
    source = repository / selected
    package_parts = []
    parent = selected.parent
    while parent != Path(".") and (repository / parent / "__init__.py").is_file():
        package_parts.insert(0, parent.name)
        parent = parent.parent
    if package_parts and parent == Path("."):
        module = importlib.import_module(".".join((*package_parts, selected.stem)))
    else:
        sys.path.insert(0, str(source.parent))
        spec = importlib.util.spec_from_file_location(f"_hwskill_exact_core_test_{index}", source)
        if spec is None or spec.loader is None:
            raise RuntimeError("selected core test cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
if selected is None:
    selected_files = sorted(
        source.relative_to(repository)
        for source in core.rglob("test_*.py")
        if source.is_file()
    )
    suite = unittest.TestSuite(load_selected(source, index) for index, source in enumerate(selected_files))
else:
    suite = load_selected(selected, 0)
if suite.countTestCases() == 0:
    print("selected core test collection contains no tests", file=sys.stderr)
    raise SystemExit(4)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


class TestCliUsageError(ValueError):
    """A test command target is syntactically valid CLI input but unsafe/invalid."""


@dataclass(frozen=True)
class CredentialMaterial:
    """The only credential data allowed into a local disposable test workspace."""

    host: str
    environment_variables: tuple[tuple[str, str], ...] = ()
    source: Literal["environment", "host-store", "minimax-store", "none"] = "none"
    credential_files: tuple[CredentialFile, ...] = ()

    @property
    def secret_values(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for _, value in self.environment_variables if value))

    @property
    def available(self) -> bool:
        return bool(self.environment_variables)

    @property
    def unavailable_reason(self) -> str:
        if self.source == "host-store":
            return "local host-store credentials cannot be safely injected into disposable tests"
        return f"environment credentials are unavailable for Agent host {self.host}"


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

    def __init__(
        self,
        credential_material: CredentialMaterial,
        *,
        host_version: str,
        agent_executor=None,
    ) -> None:
        self._credential_material = credential_material
        self._host_version = host_version
        self._agent_executor = agent_executor

    def run(
        self,
        collections: Sequence[object],
        environment: TestEnvironment,
        artifact_root: Path,
    ) -> TestRunResult:
        results: list[CollectionResult | CoreCollectionResult] = []
        agent_executor = self._agent_executor or _local_agent_executor(self._credential_material)
        try:
            for item in collections:
                if isinstance(item, Path):
                    results.append(_run_core_path(item, environment.repo_root, artifact_root, environment))
                else:
                    collection_root = artifact_root / _safe_artifact_component(
                        f"{item.target.kind}-{item.target.target_id}"
                    )
                    results.append(run_collection(item, environment, collection_root, agent_executor=agent_executor))
        except (OSError, ValueError) as exc:
            del exc
            return TestRunResult(
                "BLOCKED", artifact_root, tuple(results), environment.runner, environment.host,
                environment.model, self._host_version,
                blocked_reason="local test execution is unavailable",
            )
        return TestRunResult(
            _aggregate_status(results), artifact_root, tuple(results), environment.runner, environment.host,
            environment.model, self._host_version,
        )


def run_test_command(
    args,
    repo_root: Path,
    stdout: TextIO,
    stderr: TextIO,
    *,
    execution_boundary: TestExecutionBoundary | None = None,
    credential_material: CredentialMaterial | None = None,
    host_version_probe: Callable[[str], str | None] | None = None,
    setup_prompt: Callable[[str], str] = input,
    setup_configurator: Callable[..., object] | None = None,
    setup_writer: Callable[[TestConfiguration], None] | None = None,
) -> int:
    """Dispatch exactly one positional test target without invoking integrity checks."""
    root = Path(repo_root).resolve()
    try:
        if args.test_target == "setup":
            return _run_setup_check(
                args, root, stdout, stderr, prompt=setup_prompt,
                configurator=setup_configurator or configure_test_setup,
                writer=setup_writer or write_test_configuration,
            )
        selections, affected = _select_collections(args, root)
        _validate_option_scope(args)
        configuration = _configured_test_configuration(args)
        host, model = resolve_host_model(configuration, host=args.host)
    except (TestCliUsageError, TestConfigurationError, TestManifestError, ValueError) as exc:
        print(f"hwskill test: {exc}", file=stderr)
        return USAGE
    except (AffectedTestsBlocked, TestImpactError) as exc:
        result = _blocked_run(root, args, "affected test selection is unavailable")
        _write_run(result, bool(args.json), stdout)
        return BLOCKED

    if not selections:
        result = _blocked_run(
            root, args, "test selection is empty", runner=configuration.runner,
            host=host, model=model.model,
        )
        _write_run(result, bool(args.json), stdout)
        return BLOCKED

    try:
        material = credential_material or _environment_credential_material(host, os.environ)
        if material.host != host:
            raise TestCliUsageError("credential material host does not match selected host")
    except (TestCliUsageError, ValueError) as exc:
        print(f"hwskill test: {exc}", file=stderr)
        return USAGE
    actual_host_version: str | None = None
    if configuration.runner == "local":
        actual_host_version = (host_version_probe or _probe_local_host_version)(host)
        if actual_host_version != HOST_SPECS[host].verified_version:
            result = _blocked_run(
                root, args, "local host CLI version is unavailable or unsupported",
                runner=configuration.runner, host=host, model=model.model, version="unavailable",
            )
            _write_run(result, bool(args.json), stdout)
            return BLOCKED
    artifact_root = Path(tempfile.mkdtemp(prefix="hwskill-test-artifacts-"))
    environment = TestEnvironment(
        repo_root=root, runner=configuration.runner, host=host, model=model.model, reasoning=model.reasoning,
        secret_values=material.secret_values, environment_variables=material.environment_variables,
    )
    boundary = execution_boundary or (
        LocalTestExecutionBoundary(material, host_version=actual_host_version)
        if configuration.runner == "local"
        else DockerTestRunner(
            root, replace(configuration, default_host=host), credential_files=material.credential_files,
        )
    )
    result = boundary.run(selections, environment, artifact_root)
    if actual_host_version is not None:
        result = replace(
            result,
            runner=environment.runner,
            host=environment.host,
            model=environment.model,
            host_version=actual_host_version,
        )
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


def _run_setup_check(
    args,
    repo_root: Path,
    stdout: TextIO,
    stderr: TextIO,
    *,
    prompt: Callable[[str], str],
    configurator: Callable[..., object],
    writer: Callable[[TestConfiguration], None],
) -> int:
    if args.test_id is not None or args.base is not None:
        print("hwskill test setup: setup accepts neither a test ID nor --base", file=stderr)
        return USAGE
    try:
        supplied_model = getattr(args, "model", None)
        supplied_reasoning = getattr(args, "reasoning", None)
        if args.check and (supplied_model is not None or supplied_reasoning is not None):
            raise TestCliUsageError("--check cannot be combined with --model or --reasoning")
        if (supplied_model is None) != (supplied_reasoning is None):
            raise TestCliUsageError("test setup replacement requires both --model and --reasoning")
        bootstrap = False
        try:
            configuration = _configured_test_configuration(args)
        except TestConfigurationError as exc:
            if args.check or not _missing_test_configuration(exc):
                raise
            configuration = _bootstrap_test_configuration(args, prompt)
            bootstrap = True
        if args.host is not None:
            host, _ = resolve_host_model(configuration, host=args.host)
            configuration = replace(configuration, default_host=host)
        host, model = resolve_host_model(configuration)
        replacement = (
            _setup_replacement(configuration, host, args, prompt)
            if not args.check and supplied_model is not None and not bootstrap
            else None
        )
        if bootstrap:
            replacement = configuration
        created_replacement: list[TestConfiguration] = []

        def replacement_factory(current: TestConfiguration) -> TestConfiguration:
            candidate = _setup_replacement(current, host, args, prompt)
            created_replacement.append(candidate)
            return candidate

        image_identity = None
        if configuration.runner == "docker":
            docker_runner = DockerTestRunner(repo_root, configuration)
            try:
                image_identity = docker_runner.inspect_image() if args.check else docker_runner.build()
            except DockerRunnerUnavailable:
                image_identity = None
        report = configurator(
            configuration, runner=_setup_subprocess_runner, check=bool(args.check),
            replacement=replacement, prompt=prompt, write=writer,
            replacement_factory=(None if args.check or bootstrap else replacement_factory),
            image_identity=image_identity,
        )
    except (TestConfigurationError, ValueError, OSError) as exc:
        print(f"hwskill test setup: {exc}", file=stderr)
        return USAGE
    effective_configuration = replacement or (created_replacement[0] if created_replacement else configuration)
    effective_host, effective_model = resolve_host_model(effective_configuration)
    data = {
        "status": report.status,
        "runner": effective_configuration.runner,
        "host": effective_host,
        "model": effective_model.model,
        "reasoning": effective_model.reasoning,
        "version": HOST_SPECS[effective_host].verified_version,
        "checks": [asdict(item) for item in report.checks],
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), file=stdout)
    else:
        print("Test setup", file=stdout)
        print(f"  Status      {report.status}", file=stdout)
        print(f"  Runner      {effective_configuration.runner}", file=stdout)
        print(f"  Host        {effective_host}", file=stdout)
        print(f"  Model       {effective_model.model}", file=stdout)
        print(f"  Version     {HOST_SPECS[effective_host].verified_version}", file=stdout)
        print("  Check                Status   Detail", file=stdout)
        for item in report.checks:
            print(f"  {item.name:<20} {item.status:<8} {item.detail}", file=stdout)
    return PASS if report.status == "READY" else BLOCKED


def _setup_replacement(
    configuration: TestConfiguration,
    host: str,
    args,
    prompt: Callable[[str], str],
) -> TestConfiguration:
    current = configuration.hosts[host]
    supplied_model = getattr(args, "model", None)
    supplied_reasoning = getattr(args, "reasoning", None)
    if (supplied_model is None) != (supplied_reasoning is None):
        raise TestCliUsageError("test setup replacement requires both --model and --reasoning")
    if supplied_model is None:
        model = prompt(f"Replacement model [{current.model}]: ").strip() or current.model
        reasoning = prompt(f"Replacement reasoning [{current.reasoning}]: ").strip() or current.reasoning
    else:
        model, reasoning = supplied_model, supplied_reasoning
    hosts = dict(configuration.hosts)
    hosts[host] = HostModel(model, reasoning)
    return TestConfiguration(configuration.runner, host, hosts)


def _bootstrap_test_configuration(args, prompt: Callable[[str], str]) -> TestConfiguration:
    """Create the first credential-free configuration candidate from user input."""
    runner = args.runner or (prompt("Test runner [docker]: ").strip() or "docker")
    host_value = args.host or (prompt("Test host [codex]: ").strip() or "codex")
    model = getattr(args, "model", None) or prompt("Test model: ").strip()
    reasoning = getattr(args, "reasoning", None) or prompt("Test reasoning: ").strip()
    host = canonical_host(host_value)
    configuration = TestConfiguration(runner, host, {host: HostModel(model, reasoning)})
    resolve_host_model(configuration)
    return configuration


def _missing_test_configuration(error: TestConfigurationError) -> bool:
    return isinstance(error.__cause__, FileNotFoundError) or "No such file or directory" in str(error)


def _setup_subprocess_runner(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)


def _environment_credential_material(
    host: str,
    environment: Mapping[str, str],
    *,
    home: Path | None = None,
) -> CredentialMaterial:
    """Select exact approved values; arbitrary inherited variables are never forwarded."""
    values = tuple(
        (name, environment[name]) for name in _CREDENTIAL_ENVIRONMENT_NAMES[host]
        if isinstance(environment.get(name), str) and environment[name]
    )
    if values:
        return CredentialMaterial(host, values, "environment")
    if host in {"claude-code", "opencode"} and _MINIMAX_AUTH_ENVIRONMENT in environment:
        source = _filtered_minimax_auth_file(environment[_MINIMAX_AUTH_ENVIRONMENT])
        destination = (
            "/credentials/minimax-auth.json"
            if host == "claude-code"
            else "/credentials/opencode/auth.json"
        )
        return CredentialMaterial(host, (), "minimax-store", (CredentialFile(source, destination),))
    store = (Path.home() if home is None else Path(home)) / _CREDENTIAL_STORE_RELATIVE_PATHS[host]
    try:
        if store.is_file() and not store.is_symlink():
            destination = {
                "codex": "/credentials/codex/auth.json",
                "claude-code": "/credentials/claude-code/.credentials.json",
                "opencode": "/credentials/opencode/auth.json",
            }[host]
            return CredentialMaterial(host, (), "host-store", (CredentialFile(store, destination),))
    except OSError:
        pass
    return CredentialMaterial(host)


def _filtered_minimax_auth_file(value: object) -> Path:
    """Accept only the mode-safe, already-filtered temporary file made by a legacy wrapper."""
    if not isinstance(value, str) or not value:
        raise TestCliUsageError(f"{_MINIMAX_AUTH_ENVIRONMENT} must name a credential file")
    path = Path(value)
    if not path.is_absolute():
        raise TestCliUsageError(f"{_MINIMAX_AUTH_ENVIRONMENT} must be an absolute path")
    try:
        if path.is_symlink():
            raise TestCliUsageError("filtered MiniMax credential must be a regular non-symlink file")
    except OSError as exc:
        raise TestCliUsageError("cannot validate filtered MiniMax credential") from exc
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TestCliUsageError("filtered MiniMax credential must be a regular non-symlink file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise TestCliUsageError("filtered MiniMax credential must not be group/world accessible")
        if metadata.st_size > 64 * 1024:
            raise TestCliUsageError("filtered MiniMax credential exceeds maximum size")
        raw = os.read(descriptor, 64 * 1024 + 1)
        if len(raw) > 64 * 1024 or os.read(descriptor, 1):
            raise TestCliUsageError("filtered MiniMax credential exceeds maximum size")
        current = path.lstat()
        if (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode), current.st_size) != (
            metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode), metadata.st_size,
        ):
            raise TestCliUsageError("filtered MiniMax credential changed while validating")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except TestCliUsageError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise TestCliUsageError("cannot validate filtered MiniMax credential") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    provider = payload.get(_MINIMAX_PROVIDER) if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {_MINIMAX_PROVIDER}
        or not isinstance(provider, dict)
        or set(provider) != {"type", "key"}
        or provider.get("type") != "api"
        or not isinstance(provider.get("key"), str)
        or not provider["key"]
    ):
        raise TestCliUsageError("filtered MiniMax credential is malformed")
    return path.absolute()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate credential keys rather than silently selecting one."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _probe_local_host_version(host: str) -> str | None:
    """Return the actual compatible local CLI version without retaining its raw output."""
    try:
        completed = subprocess.run(
            (HOST_SPECS[host].executable, "--version"), text=True, capture_output=True,
            timeout=10, check=False,
            env={"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None
    version = parse_host_version(host, completed.stdout)
    if completed.returncode != 0 or version != HOST_SPECS[host].verified_version:
        return None
    return version


def _local_agent_executor(material: CredentialMaterial):
    from .test_agent import AgentExecutor

    return AgentExecutor(
        credential_available=lambda host: host == material.host and material.available,
        credential_unavailable_reason=lambda _host: material.unavailable_reason,
    )


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
    if path == root / "tests" / "core" or path.is_relative_to(root / "tests" / "core"):
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
    if getattr(args, "model", None) is not None or getattr(args, "reasoning", None) is not None:
        raise TestCliUsageError("--model and --reasoning are only valid with setup")


def _has_core_tests(root: Path) -> bool:
    directory = root / "tests" / "core"
    if not directory.exists():
        return False
    if directory.is_symlink() or not directory.is_dir():
        raise TestCliUsageError("tests/core must be a real directory")
    return True


def _safe_direct_test_path(value: str, root: Path) -> Path:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise TestCliUsageError("test path must be repository-relative under tests/core, tests/skills, or tests/profiles")
    parts = path.parts
    if len(parts) < 2 or parts[:2] not in {("tests", "core"), ("tests", "skills"), ("tests", "profiles")}:
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
    candidate_mode = os.lstat(candidate).st_mode
    if parts == ("tests", "core"):
        if not stat.S_ISDIR(candidate_mode):
            raise TestCliUsageError("tests/core must be a real directory")
        return candidate
    if not stat.S_ISREG(candidate_mode):
        raise TestCliUsageError("test path must be a regular file")
    if parts[:2] == ("tests", "core"):
        if candidate.suffix != ".py" or not candidate.name.startswith("test_"):
            raise TestCliUsageError("core test path must name a test_*.py file")
    elif candidate.name != "test.yaml":
        raise TestCliUsageError("Skill and Profile test paths must name test.yaml")
    return candidate


def _run_core_path(path: Path, root: Path, artifact_root: Path, environment: TestEnvironment) -> CoreCollectionResult:
    relative = Path(path)
    if relative != Path("tests/core"):
        _safe_direct_test_path(relative.as_posix(), root)
    case_id = "core" if relative == Path("tests/core") else relative.as_posix()
    case_dir = artifact_root / "core" / _safe_artifact_component(case_id)
    action_dir = case_dir / "actions" / "unittest"
    action_dir.mkdir(parents=True, exist_ok=False)
    try:
        root_fd, core_fd = _open_anchored_core_directories(root)
        try:
            _after_core_directories_opened(root, root / "tests" / "core")
            _assert_core_directory_unchanged(root, core_fd)
            root_path = f"/proc/self/fd/{root_fd}"
            tracked_paths = _core_snapshot_tracked_paths(root_path, root_fd)
            stage_parent = _core_stage_parent(environment)
            stage_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="hwskill-core-stage-", dir=stage_parent) as stage_directory:
                staged_repository = _stage_core_repository(root_fd, Path(stage_directory), tracked_paths)
                temporary_root = Path(stage_directory)
                (temporary_root / "home").mkdir(mode=0o700)
                core_timeout = core_timeout_seconds(relative, staged_repository, environment.timeout_seconds)
                selected_paths = _core_selected_paths(staged_repository, relative)
                if relative != Path("tests/core") and not (staged_repository / relative).is_file():
                    raise ValueError("selected core test disappeared before staging")
                stdout, stderr, timed_out, returncode = _run_isolated_core_files(
                    selected_paths, staged_repository, root_path, root_fd, core_fd,
                    environment, temporary_root, core_timeout,
                )
        finally:
            os.close(core_fd)
            os.close(root_fd)
        if timed_out:
            action_status, status, exit_code = "blocked", "BLOCKED", None
            payload = {"action_id": "unittest", "kind": "core", "status": "blocked", "exit_code": None, "reason": "timeout"}
            stderr += f"core test timed out after {core_timeout} seconds\n"
        elif returncode == 125 and "hwskill isolation guard unavailable" in stderr:
            action_status, status, exit_code = "blocked", "BLOCKED", None
            payload = {
                "action_id": "unittest", "kind": "core", "status": "blocked", "exit_code": None,
                "reason": "command isolation is unavailable",
            }
        else:
            action_status = "completed" if returncode == 0 else "failed"
            status = "PASS" if returncode == 0 else "FAIL"
            exit_code = returncode
            payload = {"action_id": "unittest", "kind": "core", "status": action_status, "exit_code": exit_code}
    except (OSError, ValueError, subprocess.SubprocessError):
        action_status, status, exit_code = "blocked", "BLOCKED", None
        payload = {"action_id": "unittest", "kind": "core", "status": "blocked", "exit_code": None, "reason": "core test execution is unavailable"}
        stdout, stderr = "", "core test execution is unavailable\n"
    _persist_core_action(action_dir, payload, stdout, stderr, environment)
    action = ActionResult("unittest", action_status, exit_code, action_dir)
    case = CaseResult(case_id, status, case_dir, (action,))
    return CoreCollectionResult(TestTarget("profile", "core"), status, (case,))


def _core_selected_paths(staged_repository: Path, relative: Path) -> tuple[Path, ...]:
    if relative != Path("tests/core"):
        return (relative,)
    core = staged_repository / "tests" / "core"
    return tuple(
        source.relative_to(staged_repository)
        for source in sorted(core.rglob("test_*.py"))
        if source.is_file()
    )


def _run_isolated_core_files(
    selections: tuple[Path, ...], staged_repository: Path, root_path: str, root_fd: int, core_fd: int,
    environment: TestEnvironment, temporary_root: Path, timeout_seconds: float,
) -> tuple[str, str, bool, int]:
    """Run each framework-test module in a fresh guarded process under one deadline."""
    if not selections:
        return "", "selected core test collection contains no tests\n", False, 4
    deadline = time.monotonic() + timeout_seconds
    outputs: list[str] = []
    errors: list[str] = []
    failed = False
    guard_unavailable = False
    for selected in selections:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "".join(outputs), "".join(errors), True, 1
        process = subprocess.Popen(
            _core_command_prefix(environment)
            + (sys.executable, "-u", "-c", _CORE_UNITTEST_RUNNER, str(staged_repository), str(selected)),
            cwd=root_path, env=_core_environment(root_path, environment, temporary_root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=True, pass_fds=(root_fd, core_fd),
        )
        stdout, stderr, timed_out = _capture_core_output(process, remaining)
        outputs.extend((f"\n=== {selected} ===\n", stdout))
        errors.extend((f"\n=== {selected} ===\n", stderr))
        if timed_out:
            return "".join(outputs), "".join(errors), True, 1
        if process.returncode == 125 and "hwskill isolation guard unavailable" in stderr:
            guard_unavailable = True
        if process.returncode != 0:
            failed = True
    if guard_unavailable:
        return "".join(outputs), "".join(errors), False, 125
    return "".join(outputs), "".join(errors), False, 1 if failed else 0


def _open_anchored_core_directories(root: Path) -> tuple[int, int]:
    if not Path("/proc/self/fd").is_dir() or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("descriptor-anchored core tests require /proc/self/fd and O_NOFOLLOW")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, flags)
    try:
        tests_fd = os.open("tests", flags, dir_fd=root_fd)
        try:
            core_fd = os.open("core", flags, dir_fd=tests_fd)
        finally:
            os.close(tests_fd)
        return root_fd, core_fd
    except BaseException:
        os.close(root_fd)
        raise


def _after_core_directories_opened(_root: Path, _core: Path) -> None:
    """Deterministic race-test seam before the anchored unittest child launches."""


def _assert_core_directory_unchanged(root: Path, core_fd: int) -> None:
    try:
        current = os.stat(root / "tests" / "core", follow_symlinks=False)
    except OSError as exc:
        raise ValueError("core test directory changed before launch") from exc
    anchored = os.fstat(core_fd)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (anchored.st_dev, anchored.st_ino):
        raise ValueError("core test directory changed before launch")


_CORE_SNAPSHOT_FILES = (
    ".dockerignore", ".gitignore", "README.md", "install.sh", "pyproject.toml",
)
_CORE_SNAPSHOT_DIRECTORIES = (
    "docker", "docs", "examples", "profiles", "registry", "scripts", "skills-src", "sources", "src", "tests",
)


def _core_snapshot_tracked_paths(root_path: str, root_fd: int) -> frozenset[PurePosixPath] | None:
    """Return Git-indexed paths, retaining modified tracked content but no untracked files."""
    try:
        marker = os.stat(".git", dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        # Core-runner unit fixtures deliberately need not be Git repositories.
        return None
    if stat.S_ISLNK(marker.st_mode) or not (stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode)):
        raise ValueError("core repository Git marker is unsafe")
    try:
        completed = subprocess.run(
            ("git", "-C", root_path, "ls-files", "-z"), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10,
            env={"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
            pass_fds=(root_fd,),
        )
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        raise ValueError("cannot determine tracked core snapshot paths") from exc
    if completed.returncode != 0:
        raise ValueError("cannot determine tracked core snapshot paths")
    paths: set[PurePosixPath] = set()
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            value = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("tracked core snapshot path is invalid") from exc
        path = PurePosixPath(value)
        if not value or path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("tracked core snapshot path is invalid")
        paths.add(path)
    return frozenset(paths)


def _stage_core_repository(
    root_fd: int, stage_root: Path, tracked_paths: frozenset[PurePosixPath] | None,
) -> Path:
    """Create a sealed, writable test-repository snapshot from declared inputs only."""
    staged_repository = stage_root / "repository"
    staged_repository.mkdir(mode=0o700)
    for name in _CORE_SNAPSHOT_FILES:
        if tracked_paths is None or PurePosixPath(name) in tracked_paths:
            _copy_core_file_if_present(root_fd, name, staged_repository)
    for name in _CORE_SNAPSHOT_DIRECTORIES:
        prefix = PurePosixPath(name)
        if tracked_paths is None or _core_snapshot_contains_path(tracked_paths, prefix):
            _copy_core_directory_if_present(root_fd, name, staged_repository, tracked_paths, prefix)
    return staged_repository


def _copy_core_file_if_present(source_fd: int, name: str, destination_root: Path) -> None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_fd)
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("core repository snapshot contains an unsupported file")
        _copy_core_file_descriptor(descriptor, destination_root / name)
    finally:
        os.close(descriptor)


def _copy_core_directory_if_present(
    source_fd: int,
    name: str,
    destination_root: Path,
    tracked_paths: frozenset[PurePosixPath] | None,
    prefix: PurePosixPath,
) -> None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
    except FileNotFoundError:
        return
    try:
        _copy_core_directory(descriptor, destination_root / name, tracked_paths, prefix)
    finally:
        os.close(descriptor)


def _core_snapshot_contains_path(paths: frozenset[PurePosixPath], prefix: PurePosixPath) -> bool:
    return any(path.parts[:len(prefix.parts)] == prefix.parts for path in paths)


def _copy_core_directory(
    source_fd: int,
    destination: Path,
    tracked_paths: frozenset[PurePosixPath] | None,
    prefix: PurePosixPath,
) -> None:
    destination.mkdir(mode=0o700)
    with os.scandir(source_fd) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            if entry.name == "__pycache__" or entry.name.endswith((".pyc", ".pyo")):
                continue
            if entry.is_symlink():
                raise ValueError("core test tree contains a symlink")
            target = destination / entry.name
            relative = prefix / entry.name
            if tracked_paths is not None:
                if entry.is_dir(follow_symlinks=False) and not _core_snapshot_contains_path(tracked_paths, relative):
                    continue
                if entry.is_file(follow_symlinks=False) and relative not in tracked_paths:
                    continue
            if entry.is_dir(follow_symlinks=False):
                child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
                try:
                    _copy_core_directory(child_fd, target, tracked_paths, relative)
                finally:
                    os.close(child_fd)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise ValueError("core test tree contains an unsupported file")
            source_file_fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_fd)
            try:
                if not stat.S_ISREG(os.fstat(source_file_fd).st_mode):
                    raise ValueError("core test tree contains an unsupported file")
                _copy_core_file_descriptor(source_file_fd, target)
            finally:
                os.close(source_file_fd)


def _copy_core_file_descriptor(source_fd: int, destination: Path) -> None:
    mode = stat.S_IMODE(os.fstat(source_fd).st_mode) & 0o777
    destination_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(destination_fd, mode)
        while chunk := os.read(source_fd, 64 * 1024):
            _write_core_snapshot_chunk(destination_fd, chunk)
    finally:
        os.close(destination_fd)


def _write_core_snapshot_chunk(destination_fd: int, chunk: bytes) -> None:
    pending = memoryview(chunk)
    while pending:
        written = os.write(destination_fd, pending)
        if written <= 0:
            raise OSError("core repository snapshot write made no progress")
        pending = pending[written:]


def _core_stage_parent(environment: TestEnvironment) -> Path:
    """Use the worker's executable workspace, never the container's noexec /tmp."""
    return environment.workspace_root if environment.workspace_root is not None else Path(tempfile.gettempdir())


def _core_command_prefix(environment: TestEnvironment) -> tuple[str, ...]:
    """Keep core framework tests behind seccomp without nesting Landlock policy."""
    prefix = environment.command_prefix
    if environment.runner != "docker" or not prefix:
        return prefix
    if prefix[-1] != "--":
        raise ValueError("core isolation prefix must terminate with --")
    launcher = list(prefix[:-1])
    for index, value in enumerate(launcher):
        if value not in {"--allow-network", "--read-only", "--read-write"}:
            continue
        launcher = launcher[:index]
        break
    if not launcher:
        raise ValueError("core isolation prefix must include a guard launcher")
    return (*launcher, "--network-only", "--")


def _core_pending_anchor(temporary_root: Path) -> str:
    absolute = temporary_root.absolute()
    if len(absolute.parts) < 2 or absolute.parts[0] != os.path.sep:
        raise ValueError("core temporary root requires a direct absolute anchor")
    return str(Path(*absolute.parts[:2]))


def _core_environment(root_path: str, environment: TestEnvironment, temporary_root: Path) -> dict[str, str]:
    python_root = str(environment.repo_root / "src") if environment.runner == "docker" else root_path + "/src"
    return {
        "PATH": environment.command_path or os.defpath,
        "LANG": "C.UTF-8",
        "HOME": str(temporary_root / "home"),
        "TMPDIR": str(temporary_root),
        "PYTHONPATH": python_root,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HWSKILL_PENDING_DIRECTORY_ANCHORS": _core_pending_anchor(temporary_root),
    }


def _capture_core_output(process: subprocess.Popen[str], timeout_seconds: float) -> tuple[str, str, bool]:
    assert process.stdout is not None and process.stderr is not None
    output: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def drain(name: str, stream) -> None:
        used = 0
        truncated = False
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            if used < _CORE_OUTPUT_LIMIT:
                allowed = _CORE_OUTPUT_LIMIT - used
                output[name].append(chunk[:allowed])
                used += min(len(chunk), allowed)
                if len(chunk) > allowed:
                    output[name].append("\n[output truncated]\n")
                    truncated = True
            elif not truncated:
                output[name].append("\n[output truncated]\n")
                truncated = True

    threads = (threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
               threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True))
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_core_process_group(process)
        process.wait()
    finally:
        for thread in threads:
            thread.join()
        process.stdout.close()
        process.stderr.close()
    return "".join(output["stdout"]), "".join(output["stderr"]), timed_out


def _terminate_core_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _persist_core_action(
    artifact_dir: Path,
    payload: Mapping[str, object],
    stdout: str,
    stderr: str,
    environment: TestEnvironment,
) -> None:
    write_json(artifact_dir / "result.json", dict(payload), environment.secret_values)
    write_text(artifact_dir / "stdout.log", stdout, environment.secret_values)
    write_text(artifact_dir / "stderr.log", stderr, environment.secret_values)


def _safe_artifact_component(value: str) -> str:
    return value.replace("/", "-").replace("\\", "-")


def _blocked_run(
    root: Path,
    args,
    reason: str,
    *,
    runner: str | None = None,
    host: str = "unresolved",
    model: str = "unresolved",
    version: str | None = None,
) -> TestRunResult:
    return TestRunResult("BLOCKED", root / "artifacts" / "hwskill-test-unavailable", (),
                         runner or args.runner or "configured", host, model,
                         version or (HOST_SPECS[host].verified_version if host in HOST_SPECS else "unavailable"),
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
    print("Test run", file=stdout)
    print(f"  Status      {result.status}", file=stdout)
    print(f"  Runner      {result.runner}", file=stdout)
    print(f"  Host        {result.host}", file=stdout)
    print(f"  Model       {result.model}", file=stdout)
    print(f"  Version     {result.host_version}", file=stdout)
    print(f"  Artifacts   {result.artifact_root}", file=stdout)
    if result.runner == "local":
        print("  Trust       local debugging only; not immutable or security evidence", file=stdout)
    if result.blocked_reason is not None:
        print(f"  Blocked     {result.blocked_reason}", file=stdout)
    for collection in data["collections"]:
        print(f"Collection  {collection['target']:<24} {collection['status']}", file=stdout)
        for case in collection["cases"]:
            print(f"  Case      {case['id']:<22} {case['status']:<8} {case['artifact_dir']}", file=stdout)
            for action in case["actions"]:
                code = "-" if action["exit_code"] is None else str(action["exit_code"])
                print(f"    Action  {action['id']:<20} {action['status']:<9} exit={code:<3} {action['artifact_dir']}", file=stdout)
            post = case["post_check"]
            if post is not None:
                code = "-" if post["exit_code"] is None else str(post["exit_code"])
                print(f"    Post    {post['id']:<22} {post['status']:<9} exit={code:<3} {post['artifact_dir']}", file=stdout)
    if result.pending_cleared:
        print("Pending     cleared", file=stdout)


def _run_data(result: TestRunResult) -> dict[str, object]:
    collections = []
    for collection in result.collections:
        cases = []
        is_core = collection.target.target_id == "core"
        for case in collection.cases:
            rendered_actions = [_action_data(action) for action in case.actions]
            if is_core:
                actions, post = rendered_actions, None
            elif rendered_actions:
                actions, post = rendered_actions[:-1], rendered_actions[-1]
            else:
                actions = []
                post = {"id": "unavailable", "status": "blocked", "exit_code": None, "artifact_dir": str(case.artifact_dir)}
            cases.append({
                "id": case.case_id,
                "status": case.status,
                "artifact_dir": str(case.artifact_dir),
                "actions": actions,
                "post_check": post,
            })
        kind = "core" if is_core else collection.target.kind
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
