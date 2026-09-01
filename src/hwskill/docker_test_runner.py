"""Host-side orchestration for the standardized Docker test environment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from typing import Literal

from .hosts import HOST_SPECS
from .test_artifacts import ActionResult, CaseResult, CollectionResult
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
})
_DOCKER_CLIENT_ENVIRONMENT_NAMES = (
    "DOCKER_CONFIG",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
)


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
    workspace_root: Path
    network: NetworkMode
    environment_variables: tuple[tuple[str, str], ...] = ()
    credential_files: tuple[CredentialFile, ...] = ()


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
        dockerfile = self.repo_root / "docker" / "test" / "Dockerfile"
        completed = self._invoke((
            "docker", "build", "--pull=false", "--file", str(dockerfile),
            "--tag", _IMAGE_NAME, str(self.repo_root),
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

    def build_run_command(self, request: DockerTestRequest) -> tuple[str, ...]:
        identity = self._image
        if identity is None or _DIGEST.fullmatch(identity.digest) is None:
            raise DockerRunnerUnavailable("a verified standard image identity is required")
        request_path = _regular_file(request.request_path, "worker request")
        repository = _real_directory(self.repo_root, "repository")
        tests = _real_directory(repository / "tests", "tests")
        artifacts = _real_directory(request.artifact_root, "artifact root")
        workspace = _real_directory(request.workspace_root, "workspace root")
        if request.network not in {"none", "agent"}:
            raise DockerRunnerUnavailable("unsupported test network mode")
        argv: list[str] = [
            "docker", "run", "--rm", "--init", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "512", "--network", "none" if request.network == "none" else "bridge",
            "--user", f"{self._uid}:{self._gid}",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m",
            "--mount", _mount(repository, "/registry", readonly=True),
            "--mount", _mount(tests, "/tests", readonly=True),
            "--mount", _mount(artifacts, "/artifacts"),
            "--mount", _mount(workspace, "/workspace"),
            "--mount", _mount(request_path, "/run/request.json", readonly=True),
            "--env", "HOME=/workspace/home",
            "--env", "HWSKILL_REGISTRY_ROOT=/registry",
        ]
        seen_names: set[str] = set()
        for name, _value in request.environment_variables:
            if name in seen_names or not _approved_environment_name(self.config.default_host, name):
                raise DockerRunnerUnavailable("unapproved credential environment variable")
            seen_names.add(name)
            argv.extend(("--env", name))
        seen_destinations: set[str] = set()
        for credential in request.credential_files:
            source = _regular_file(credential.source, "credential file")
            if credential.destination not in _FIXED_CREDENTIAL_DESTINATIONS or credential.destination in seen_destinations:
                raise DockerRunnerUnavailable("unsupported credential file destination")
            seen_destinations.add(credential.destination)
            argv.extend(("--mount", _mount(source, credential.destination, readonly=True)))
        argv.extend((identity.digest, "python", "-m", "hwskill.test_worker", "--request", "/run/request.json"))
        return tuple(argv)

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
            payload = _worker_request_payload(collections, environment, self.repo_root)
            with tempfile.TemporaryDirectory(prefix="hwskill-docker-request-") as request_directory, tempfile.TemporaryDirectory(
                prefix="hwskill-docker-workspace-"
            ) as workspace_directory:
                os.chmod(request_directory, 0o700)
                request_path = Path(request_directory) / "request.json"
                descriptor = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                request = DockerTestRequest(
                    request_path, artifacts, Path(workspace_directory),
                    selection_network_mode(collections),
                    tuple(environment.environment_variables), self._credential_files,
                )
                argv = self.build_run_command(request)
                completed = self._run_container(argv, environment.environment_variables, environment.timeout_seconds)
            result_path = artifacts / "result.json"
            if not result_path.is_file() or result_path.is_symlink():
                raise DockerRunnerUnavailable("Docker worker did not produce a result")
            result = _load_result(result_path, artifacts)
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
    ) -> subprocess.CompletedProcess[str]:
        child_environment = _docker_client_environment(environment_variables)
        try:
            return self._command_runner(
                tuple(argv), text=True, capture_output=True,
                timeout=max(float(timeout_seconds) + 30.0, 60.0), check=False,
                env=child_environment,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            raise DockerRunnerUnavailable("Docker worker is unavailable") from exc

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
        "model": environment.model,
        "reasoning": environment.reasoning,
        "timeout_seconds": environment.timeout_seconds,
        "credential_environment": names,
    }


def _load_result(path: Path, artifact_root: Path) -> TestRunResult:
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
    collections = tuple(_parse_collection_result(item, artifact_root) for item in _list(data["collections"], "collections"))
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
    return TestRunResult(
        status, artifact_root, collections, _single_line(data["runner"], "runner"),
        _single_line(data["host"], "host"), _single_line(data["model"], "model"),
        _single_line(data["host_version"], "host_version"), blocked_reason=blocked_reason,
    )


def _parse_collection_result(value: object, root: Path) -> CollectionResult | CoreCollectionResult:
    data = _object(value, "collection")
    _result_keys(data, {"kind", "target_id", "status", "cases"}, "collection")
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
