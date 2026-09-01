"""Host-side orchestration for the standardized Docker test environment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from typing import Literal

from .core_timeout import core_timeout_seconds
from .hosts import HOST_SPECS
from .test_artifacts import ActionResult, CaseResult, CollectionResult, safe_artifact_id
from .test_configuration import TestConfiguration
from .test_manifest import AgentAction, TestCollection, TestTarget


RunStatus = Literal["PASS", "FAIL", "BLOCKED"]
NetworkMode = Literal["none", "agent"]
_IMAGE_NAME = "hwskill-test:0.1.0"
_IMAGE_VERSION = "0.1.0"
_IMAGE_LABELS = {
    "org.hwskill.test.schema": "1",
    "org.opencontainers.image.version": _IMAGE_VERSION,
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FIXED_CREDENTIAL_DESTINATIONS = frozenset({
    "/credentials/codex/auth.json",
    "/credentials/claude-code/.credentials.json",
    "/credentials/opencode/auth.json",
    "/credentials/minimax-auth.json",
})
_HOST_CREDENTIAL_DESTINATIONS = {
    "codex": frozenset({"/credentials/codex/auth.json"}),
    "claude-code": frozenset({
        "/credentials/claude-code/.credentials.json",
        "/credentials/minimax-auth.json",
    }),
    "opencode": frozenset({"/credentials/opencode/auth.json"}),
}
_DOCKER_CLIENT_ENVIRONMENT_NAMES = (
    "DOCKER_CONFIG",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
)
_BUILD_CONTEXT_INPUTS = (
    "README.md", "install.sh", "pyproject.toml", ".gitignore", ".dockerignore",
    "examples", "profiles", "registry", "scripts", "skills-src", "sources", "src", "tests", "docker/test",
)
_CONTAINER_NAME = re.compile(r"hwskill-(?:run|preflight)-[0-9a-f]{32}\Z")


class DockerRunnerUnavailable(RuntimeError):
    """The standard Docker runner cannot be used or verified."""


@dataclass(frozen=True)
class ImageInfo:
    image: str
    digest: str


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


@dataclass(frozen=True)
class CredentialFile:
    source: Path
    destination: str


@dataclass(frozen=True)
class DockerTestRequest:
    request_path: Path
    artifact_root: Path
    network: NetworkMode
    environment_variables: tuple[tuple[str, str], ...] = ()
    credential_files: tuple[CredentialFile, ...] = ()


@dataclass(frozen=True)
class _ExpectedCase:
    case_id: str
    action_ids: tuple[str, ...]
    command_post_check: bool


@dataclass(frozen=True)
class _ExpectedCollection:
    selection_path: str
    kind: str
    target_id: str
    cases: tuple[_ExpectedCase, ...]


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class DockerTestRunner:
    """Build, verify, and execute the repository's standard test image."""

    def __init__(
        self,
        repo_root: Path,
        config: TestConfiguration,
        *,
        image: ImageInfo | None = None,
        command_runner: CommandRunner = subprocess.run,
        uid: int | None = None,
        gid: int | None = None,
        credential_files: tuple[CredentialFile, ...] = (),
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self._image = image
        self._command_runner = command_runner
        self._uid = os.getuid() if uid is None else uid
        self._gid = os.getgid() if gid is None else gid
        self._credential_files = credential_files

    def build(self) -> ImageInfo:
        with _private_build_context(self.repo_root) as context:
            dockerfile = context / "docker" / "test" / "Dockerfile"
            completed = self._invoke((
                "docker", "build", "--pull=false", "--file", str(dockerfile),
                "--tag", _IMAGE_NAME, str(context),
            ), timeout=1800)
        if completed.returncode != 0:
            raise DockerRunnerUnavailable("standard test image build failed")
        self._image = self.inspect_image()
        return self._image

    def inspect_image(self) -> ImageInfo:
        completed = self._invoke(("docker", "image", "inspect", _IMAGE_NAME), timeout=20)
        if completed.returncode != 0:
            raise DockerRunnerUnavailable("standard test image is unavailable")
        try:
            payload = json.loads(completed.stdout)
            item = payload[0]
            digest = item["Id"]
            labels = item["Config"]["Labels"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise DockerRunnerUnavailable("standard test image metadata is invalid") from exc
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise DockerRunnerUnavailable("standard test image digest is invalid")
        if not isinstance(labels, Mapping) or any(labels.get(key) != value for key, value in _IMAGE_LABELS.items()):
            raise DockerRunnerUnavailable("standard test image labels are unsupported")
        return ImageInfo(_IMAGE_NAME, digest)

    def build_run_command(self, request: DockerTestRequest, *, container_name: str | None = None) -> tuple[str, ...]:
        identity = self._image
        if identity is None or _DIGEST.fullmatch(identity.digest) is None:
            raise DockerRunnerUnavailable("a verified standard image identity is required")
        request_path = _regular_file(request.request_path, "worker request")
        repository = _real_directory(self.repo_root, "repository")
        tests = _real_directory(repository / "tests", "tests")
        artifacts = _real_directory(request.artifact_root, "artifact root")
        if request.network not in {"none", "agent"}:
            raise DockerRunnerUnavailable("unsupported test network mode")
        argv: list[str] = [
            "docker", "run", "--rm", "--init", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "512", "--network", "none" if request.network == "none" else "bridge",
            "--user", f"{self._uid}:{self._gid}",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m",
            "--tmpfs", f"/artifacts:rw,nosuid,nodev,noexec,size=256m,mode=0700,uid={self._uid},gid={self._gid}",
            "--tmpfs", f"/workspace:rw,nosuid,nodev,exec,size=256m,mode=0700,uid={self._uid},gid={self._gid}",
            "--mount", _mount(repository, "/registry", readonly=True),
            "--mount", _mount(tests, "/tests", readonly=True),
            "--mount", _mount(artifacts, "/export"),
            "--mount", _mount(request_path, "/run/request.json", readonly=True),
            "--env", "HOME=/workspace/home",
            "--env", "HWSKILL_REGISTRY_ROOT=/registry",
        ]
        if container_name is not None:
            if _CONTAINER_NAME.fullmatch(container_name) is None:
                raise DockerRunnerUnavailable("Docker container name is invalid")
            argv[2:2] = ("--name", container_name)
        seen_names: set[str] = set()
        for name, _value in request.environment_variables:
            if name in seen_names or not _approved_environment_name(self.config.default_host, name):
                raise DockerRunnerUnavailable("unapproved credential environment variable")
            seen_names.add(name)
            argv.extend(("--env", name))
        seen_destinations: set[str] = set()
        for credential in request.credential_files:
            source = _regular_file(credential.source, "credential file")
            if (
                credential.destination not in _FIXED_CREDENTIAL_DESTINATIONS
                or credential.destination not in _HOST_CREDENTIAL_DESTINATIONS[self.config.default_host]
                or credential.destination in seen_destinations
            ):
                raise DockerRunnerUnavailable("unsupported credential file destination")
            seen_destinations.add(credential.destination)
            argv.extend(("--mount", _mount(source, credential.destination, readonly=True)))
        argv.extend((identity.digest, "python", "-m", "hwskill.test_worker", "--request", "/run/request.json"))
        return tuple(argv)

    def build_preflight_command(self, *, container_name: str) -> tuple[str, ...]:
        identity = self._image
        if identity is None or _DIGEST.fullmatch(identity.digest) is None:
            raise DockerRunnerUnavailable("a verified standard image identity is required")
        if _CONTAINER_NAME.fullmatch(container_name) is None or not container_name.startswith("hwskill-preflight-"):
            raise DockerRunnerUnavailable("Docker preflight container name is invalid")
        return (
            "docker", "run", "--name", container_name, "--rm", "--init", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--network", "none", "--user", f"{self._uid}:{self._gid}",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid={self._uid},gid={self._gid}",
            "--env", "HOME=/tmp", identity.digest, "preflight",
        )

    def run(
        self,
        collections: Sequence[object],
        environment,
        artifact_root: Path,
    ) -> TestRunResult:
        """Execute one immutable selection in Docker, with no local fallback."""
        try:
            artifacts = _real_directory(artifact_root, "artifact root")
            if self._image is None:
                self._image = self.inspect_image()
            preflight_name = "hwskill-preflight-" + secrets.token_hex(16)
            preflight = self._run_container(
                self.build_preflight_command(container_name=preflight_name), (), 60.0, preflight_name,
            )
            if preflight.returncode != 0:
                raise DockerRunnerUnavailable("standard test image preflight failed")
            payload = _worker_request_payload(collections, environment, self.repo_root)
            expectations = _result_expectations(collections, self.repo_root)
            with tempfile.TemporaryDirectory(prefix="hwskill-docker-request-") as request_directory:
                os.chmod(request_directory, 0o700)
                request_path = Path(request_directory) / "request.json"
                descriptor = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                request = DockerTestRequest(
                    request_path, artifacts,
                    selection_network_mode(collections),
                    tuple(environment.environment_variables), self._credential_files,
                )
                container_name = "hwskill-run-" + secrets.token_hex(16)
                argv = self.build_run_command(request, container_name=container_name)
                completed = self._run_container(
                    argv, environment.environment_variables,
                    _selection_timeout_budget(collections, environment.timeout_seconds, self.repo_root), container_name,
                )
            result_path = artifacts / "result.json"
            if not result_path.is_file() or result_path.is_symlink():
                raise DockerRunnerUnavailable("Docker worker did not produce a result")
            result = _load_result(result_path, artifacts, expectations)
            expected_code = {"PASS": 0, "FAIL": 1, "BLOCKED": 3}[result.status]
            if completed.returncode != expected_code:
                raise DockerRunnerUnavailable("Docker worker exit status does not match its result")
            if (result.runner, result.host, result.model) != (
                "docker", environment.host, environment.model,
            ):
                raise DockerRunnerUnavailable("Docker worker result identity does not match the request")
            if result.host_version != HOST_SPECS[environment.host].verified_version:
                raise DockerRunnerUnavailable("Docker worker did not report the verified Agent host version")
            return result
        except (DockerRunnerUnavailable, OSError, ValueError, TypeError):
            return TestRunResult(
                "BLOCKED", Path(artifact_root), (), "docker", environment.host, environment.model,
                "unavailable", blocked_reason="standard Docker test execution is unavailable",
            )

    def _run_container(
        self,
        argv: Sequence[str],
        environment_variables: Sequence[tuple[str, str]],
        timeout_seconds: float,
        container_name: str,
    ) -> subprocess.CompletedProcess[str]:
        child_environment = _docker_client_environment(environment_variables)
        try:
            return self._command_runner(
                tuple(argv), text=True, capture_output=True,
                timeout=timeout_seconds, check=False,
                env=child_environment,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            self._cleanup_container(container_name)
            raise DockerRunnerUnavailable("Docker worker is unavailable") from exc

    def _cleanup_container(self, container_name: str) -> None:
        if _CONTAINER_NAME.fullmatch(container_name) is None:
            raise DockerRunnerUnavailable("refusing unsafe Docker cleanup target")
        environment = _docker_client_environment()
        for argv in (
            ("docker", "stop", "--time", "5", container_name),
            ("docker", "rm", "--force", container_name),
        ):
            try:
                self._command_runner(
                    argv, text=True, capture_output=True, timeout=10, check=False, env=environment,
                )
            except (OSError, subprocess.SubprocessError, TimeoutError):
                pass
        try:
            inspected = self._command_runner(
                ("docker", "container", "ls", "--all", "--quiet", "--filter", f"name=^/{container_name}$"),
                text=True, capture_output=True,
                timeout=10, check=False, env=environment,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            raise DockerRunnerUnavailable("Docker container cleanup could not be confirmed") from exc
        if inspected.returncode != 0 or inspected.stdout.strip():
            raise DockerRunnerUnavailable("Docker container cleanup failed")

    def _invoke(self, argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return self._command_runner(
                tuple(argv), text=True, capture_output=True, timeout=timeout, check=False,
                env=_docker_client_environment(),
            )
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            raise DockerRunnerUnavailable("Docker command is unavailable") from exc


def standard_image_name() -> str:
    return _IMAGE_NAME


@contextmanager
def _private_build_context(repo_root: Path):
    repository = _real_directory(repo_root, "repository")
    completed = subprocess.run(
        ("git", "ls-files", "-z", "--", *_BUILD_CONTEXT_INPUTS), cwd=repository,
        capture_output=True, check=False, env={"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
    )
    if completed.returncode != 0:
        raise DockerRunnerUnavailable("cannot enumerate tracked Docker build inputs")
    raw_paths = completed.stdout.split(b"\0")
    try:
        tracked = tuple(item.decode("utf-8") for item in raw_paths if item)
    except UnicodeError as exc:
        raise DockerRunnerUnavailable("tracked Docker build input path is invalid") from exc
    directory = Path(tempfile.mkdtemp(prefix="hwskill-build-context-"))
    os.chmod(directory, 0o700)
    try:
        for declared in _BUILD_CONTEXT_INPUTS:
            if "/" not in declared and (repository / declared).is_dir():
                (directory / declared).mkdir(mode=0o700, parents=True, exist_ok=True)
        repository_fd = os.open(repository, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            for item in tracked:
                _copy_tracked_build_file(repository_fd, item, directory)
        finally:
            os.close(repository_fd)
        if not (directory / "docker/test/Dockerfile").is_file():
            raise DockerRunnerUnavailable("tracked Dockerfile is unavailable")
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _copy_tracked_build_file(repository_fd: int, relative: str, destination_root: Path) -> None:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
        or not any(pure == PurePosixPath(item) or pure.is_relative_to(PurePosixPath(item)) for item in _BUILD_CONTEXT_INPUTS)
    ):
        raise DockerRunnerUnavailable("tracked Docker build input path is unsafe")
    descriptors = [repository_fd]
    try:
        parent = repository_fd
        for component in pure.parts[:-1]:
            parent = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent,
            )
            descriptors.append(parent)
        source = os.open(
            pure.parts[-1], os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent,
        )
        descriptors.append(source)
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode):
            raise DockerRunnerUnavailable("Docker build input must be a regular file")
        destination = destination_root.joinpath(*pure.parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700 if before.st_mode & 0o111 else 0o600)
        try:
            while block := os.read(source, 1024 * 1024):
                remaining = memoryview(block)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError(errno.EIO, "short Docker build context write")
                    remaining = remaining[written:]
        finally:
            os.close(descriptor)
        after = os.fstat(source)
        identity = lambda value: (
            value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise DockerRunnerUnavailable("Docker build input changed while copying")
    except (OSError, ValueError) as exc:
        if isinstance(exc, DockerRunnerUnavailable):
            raise
        raise DockerRunnerUnavailable("cannot safely copy Docker build input") from exc
    finally:
        for descriptor in reversed(descriptors[1:]):
            os.close(descriptor)


def _selection_timeout_budget(
    collections: Sequence[object], action_timeout: float, repo_root: Path | None = None,
) -> float:
    budget = 0.0
    for item in collections:
        if isinstance(item, Path):
            budget += (
                core_timeout_seconds(item, repo_root, action_timeout)
                if repo_root is not None else float(action_timeout)
            )
            continue
        if not isinstance(item, TestCollection):
            raise DockerRunnerUnavailable("unsupported Docker test selection")
        for case in item.cases:
            action_count = len(case.steps) + 1 + (1 if case.prepare is not None else 0)
            budget += float(action_timeout) * (1 + action_count)
    return max(budget + 20.0, 30.0)


def _docker_client_environment(
    extra: Sequence[tuple[str, str]] = (),
) -> dict[str, str]:
    """Return the minimum host environment needed to contact Docker."""
    environment = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}
    for name in _DOCKER_CLIENT_ENVIRONMENT_NAMES:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(extra)
    return environment


def selection_network_mode(collections: Sequence[object]) -> NetworkMode:
    for item in collections:
        if not isinstance(item, TestCollection):
            continue
        for case in item.cases:
            actions = (() if case.prepare is None else (case.prepare,)) + case.steps + (case.post_check,)
            if any(isinstance(action, AgentAction) for action in actions):
                return "agent"
    return "none"


def _worker_request_payload(collections: Sequence[object], environment, repo_root: Path) -> dict[str, object]:
    selections: list[dict[str, str]] = []
    for item in collections:
        if isinstance(item, Path):
            path = PurePosixPath(item.as_posix())
            selections.append({"kind": "core", "path": path.as_posix()})
            continue
        if not isinstance(item, TestCollection):
            raise DockerRunnerUnavailable("unsupported Docker test selection")
        try:
            relative = item.manifest_path.relative_to(repo_root)
        except ValueError as exc:
            raise DockerRunnerUnavailable("test manifest is outside the repository") from exc
        selections.append({"kind": item.target.kind, "path": relative.as_posix()})
    if not selections:
        raise DockerRunnerUnavailable("Docker test selection is empty")
    names = [name for name, _value in environment.environment_variables]
    return {
        "schema_version": 1,
        "selections": selections,
        "host": environment.host,
        "host_version": HOST_SPECS[environment.host].verified_version,
        "model": environment.model,
        "reasoning": environment.reasoning,
        "timeout_seconds": environment.timeout_seconds,
        "credential_environment": names,
    }


def _result_expectations(collections: Sequence[object], repo_root: Path) -> tuple[_ExpectedCollection, ...]:
    expected: list[_ExpectedCollection] = []
    for item in collections:
        if isinstance(item, Path):
            selection_path = item.as_posix()
            case_id = "core" if item == Path("tests/core") else selection_path
            expected.append(_ExpectedCollection(selection_path, "core", "core", (_ExpectedCase(case_id, ("unittest",), True),)))
            continue
        if not isinstance(item, TestCollection):
            raise DockerRunnerUnavailable("unsupported Docker test selection")
        try:
            selection_path = item.manifest_path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise DockerRunnerUnavailable("test manifest is outside the repository") from exc
        cases: list[_ExpectedCase] = []
        for case in item.cases:
            actions = (() if case.prepare is None else (case.prepare,)) + case.steps + (case.post_check,)
            cases.append(_ExpectedCase(
                case.case_id, tuple(action.action_id for action in actions),
                not isinstance(case.post_check, AgentAction),
            ))
        expected.append(_ExpectedCollection(selection_path, item.target.kind, item.target.target_id, tuple(cases)))
    return tuple(expected)


def _load_result(
    path: Path,
    artifact_root: Path,
    expected: tuple[_ExpectedCollection, ...] | None = None,
) -> TestRunResult:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DockerRunnerUnavailable("Docker worker result must be a regular file")
        raw = os.read(descriptor, 1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or os.read(descriptor, 1):
            raise DockerRunnerUnavailable("Docker worker result exceeds maximum size")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DockerRunnerUnavailable("Docker worker result is invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _result_keys(data, {
        "schema_version", "status", "runner", "host", "model", "host_version",
        "blocked_reason", "collections",
    }, "result")
    if data["schema_version"] != 1:
        raise DockerRunnerUnavailable("Docker worker result schema is unsupported")
    status = _status(data["status"])
    blocked_reason = data["blocked_reason"]
    if blocked_reason is not None and not isinstance(blocked_reason, str):
        raise DockerRunnerUnavailable("Docker worker blocked_reason is invalid")
    raw_collections = _list(data["collections"], "collections")
    collections = tuple(_parse_collection_result(item, artifact_root) for item in raw_collections)
    if status != "BLOCKED" and not collections:
        raise DockerRunnerUnavailable("Docker worker returned no executed collections")
    if collections:
        aggregate: RunStatus = (
            "BLOCKED" if any(item.status == "BLOCKED" for item in collections)
            else "FAIL" if any(item.status == "FAIL" for item in collections)
            else "PASS"
        )
        if aggregate != status:
            raise DockerRunnerUnavailable("Docker worker aggregate status is inconsistent")
    if expected is not None:
        _validate_result_binding(raw_collections, collections, status, expected, artifact_root)
    return TestRunResult(
        status, artifact_root, collections, _single_line(data["runner"], "runner"),
        _single_line(data["host"], "host"), _single_line(data["model"], "model"),
        _single_line(data["host_version"], "host_version"), blocked_reason=blocked_reason,
    )


def _parse_collection_result(value: object, root: Path) -> CollectionResult | CoreCollectionResult:
    data = _object(value, "collection")
    allowed = {"kind", "target_id", "status", "cases", "selection_path"}
    if set(data) != allowed:
        raise DockerRunnerUnavailable("Docker worker collection keys are invalid")
    _safe_result_selection_path(data["selection_path"])
    kind = _single_line(data["kind"], "collection kind")
    target_id = _single_line(data["target_id"], "collection target")
    if kind not in {"core", "skill", "profile"}:
        raise DockerRunnerUnavailable("Docker worker collection kind is invalid")
    cases = tuple(_parse_case_result(item, root) for item in _list(data["cases"], "cases"))
    status = _status(data["status"])
    if kind == "core":
        if target_id != "core":
            raise DockerRunnerUnavailable("Docker worker core target is invalid")
        return CoreCollectionResult(TestTarget("profile", "core"), status, cases)
    return CollectionResult(TestTarget(kind, target_id), status, cases)


def _safe_result_selection_path(value: object) -> str:
    text = _single_line(value, "selection path")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DockerRunnerUnavailable("Docker worker selection path is unsafe")
    return text


def _validate_result_binding(
    raw_collections: list[object],
    collections: tuple[CollectionResult | CoreCollectionResult, ...],
    status: RunStatus,
    expected: tuple[_ExpectedCollection, ...],
    artifact_root: Path,
) -> None:
    if status == "BLOCKED" and not collections:
        return
    if len(collections) != len(expected):
        raise DockerRunnerUnavailable("Docker worker result selection count does not match request")
    for raw, collection, wanted in zip(raw_collections, collections, expected):
        data = _object(raw, "collection")
        if data.get("selection_path") != wanted.selection_path:
            raise DockerRunnerUnavailable("Docker worker result selection path does not match request")
        actual_kind = "core" if isinstance(collection, CoreCollectionResult) else collection.target.kind
        actual_id = "core" if isinstance(collection, CoreCollectionResult) else collection.target.target_id
        if (actual_kind, actual_id) != (wanted.kind, wanted.target_id):
            raise DockerRunnerUnavailable("Docker worker result target does not match request")
        if tuple(case.case_id for case in collection.cases) != tuple(case.case_id for case in wanted.cases):
            raise DockerRunnerUnavailable("Docker worker result cases do not match request")
        for case, wanted_case in zip(collection.cases, wanted.cases):
            if tuple(action.action_id for action in case.actions) != wanted_case.action_ids:
                raise DockerRunnerUnavailable("Docker worker result actions do not match request")
            for action in case.actions:
                if (
                    (action.status == "completed" and action.exit_code != 0)
                    or (action.status == "failed" and (action.exit_code is None or action.exit_code == 0))
                    or (action.status == "blocked" and action.exit_code is not None)
                ):
                    raise DockerRunnerUnavailable("Docker worker action status is inconsistent")
            derived = _expected_case_status(case.actions, wanted_case.command_post_check, case.artifact_dir, artifact_root)
            if derived is not None and case.status != derived:
                raise DockerRunnerUnavailable("Docker worker case status is inconsistent")
        collection_status: RunStatus = (
            "BLOCKED" if any(case.status == "BLOCKED" for case in collection.cases)
            else "FAIL" if any(case.status == "FAIL" for case in collection.cases)
            else "PASS"
        )
        if collection.status != collection_status:
            raise DockerRunnerUnavailable("Docker worker collection status is inconsistent")


def _expected_case_status(
    actions: tuple[ActionResult, ...],
    command_post_check: bool,
    case_artifact_dir: Path,
    artifact_root: Path,
) -> RunStatus:
    if not actions or any(action.status == "blocked" for action in actions):
        return "BLOCKED"
    post = actions[-1]
    if command_post_check:
        if post.exit_code == 0:
            return "PASS"
        if post.exit_code == 1:
            return "FAIL"
        return "BLOCKED"
    if post.status != "completed":
        return "BLOCKED"
    if post.artifact_dir != case_artifact_dir / "actions" / safe_artifact_id(post.action_id):
        return "BLOCKED"
    return _read_agent_post_check(post.artifact_dir, artifact_root)


def _read_agent_post_check(action_artifact_dir: Path, artifact_root: Path) -> RunStatus:
    try:
        relative = action_artifact_dir.relative_to(artifact_root)
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            return "BLOCKED"
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(artifact_root, flags)
        descriptors = [root_fd]
        try:
            parent_fd = root_fd
            for component in relative.parts:
                parent_fd = os.open(component, flags, dir_fd=parent_fd)
                descriptors.append(parent_fd)
            response_fd = os.open(
                "final-response.md", os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
            )
            descriptors.append(response_fd)
            if not stat.S_ISREG(os.fstat(response_fd).st_mode):
                return "BLOCKED"
            raw = os.read(response_fd, 64 * 1024 + 1)
            if len(raw) > 64 * 1024 or os.read(response_fd, 1):
                return "BLOCKED"
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        from .test_agent import parse_agent_post_check
        return parse_agent_post_check(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return "BLOCKED"


def _parse_case_result(value: object, root: Path) -> CaseResult:
    data = _object(value, "case")
    _result_keys(data, {"case_id", "status", "artifact_dir", "actions"}, "case")
    actions = tuple(_parse_action_result(item, root) for item in _list(data["actions"], "actions"))
    return CaseResult(
        _single_line(data["case_id"], "case id"), _status(data["status"]),
        _artifact_path(data["artifact_dir"], root), actions,
    )


def _parse_action_result(value: object, root: Path) -> ActionResult:
    data = _object(value, "action")
    _result_keys(data, {"action_id", "status", "exit_code", "artifact_dir"}, "action")
    status = data["status"]
    if status not in {"completed", "failed", "blocked"}:
        raise DockerRunnerUnavailable("Docker worker action status is invalid")
    exit_code = data["exit_code"]
    if exit_code is not None and (type(exit_code) is not int or not -(2**31) <= exit_code < 2**31):
        raise DockerRunnerUnavailable("Docker worker action exit_code is invalid")
    return ActionResult(
        _single_line(data["action_id"], "action id"), status, exit_code,
        _artifact_path(data["artifact_dir"], root),
    )


def _artifact_path(value: object, root: Path) -> Path:
    text = _single_line(value, "artifact path")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise DockerRunnerUnavailable("Docker worker artifact path is unsafe")
    return root.joinpath(*pure.parts)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DockerRunnerUnavailable(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise DockerRunnerUnavailable(f"Docker worker {label} is invalid")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise DockerRunnerUnavailable(f"Docker worker {label} is invalid")
    return value


def _result_keys(data: dict[str, object], keys: set[str], label: str) -> None:
    if set(data) != keys:
        raise DockerRunnerUnavailable(f"Docker worker {label} keys are invalid")


def _single_line(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\x00" in value:
        raise DockerRunnerUnavailable(f"Docker worker {label} is invalid")
    return value


def _status(value: object) -> RunStatus:
    if value not in {"PASS", "FAIL", "BLOCKED"}:
        raise DockerRunnerUnavailable("Docker worker status is invalid")
    return value


def _mount(source: Path, destination: str, *, readonly: bool = False) -> str:
    source_text = _safe_mount_field(str(source))
    destination_text = _safe_mount_field(destination)
    value = f"type=bind,src={source_text},dst={destination_text}"
    return value + (",readonly" if readonly else "")


def _safe_mount_field(value: str) -> str:
    if not value or "," in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DockerRunnerUnavailable("Docker mount path contains unsupported characters")
    return value


def _real_directory(path: Path, label: str) -> Path:
    candidate = Path(path).absolute()
    try:
        mode = os.lstat(candidate).st_mode
    except (OSError, ValueError) as exc:
        raise DockerRunnerUnavailable(f"{label} is unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise DockerRunnerUnavailable(f"{label} must be a real directory")
    return candidate


def _regular_file(path: Path, label: str) -> Path:
    candidate = Path(path).absolute()
    try:
        mode = os.lstat(candidate).st_mode
    except (OSError, ValueError) as exc:
        raise DockerRunnerUnavailable(f"{label} is unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise DockerRunnerUnavailable(f"{label} must be a regular file")
    return candidate


def _approved_environment_name(host: str, name: str) -> bool:
    return name in {
        "codex": {"CODEX_API_KEY"},
        "claude-code": {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"},
        "opencode": {"OPENCODE_API_KEY"},
    }.get(host, set())
