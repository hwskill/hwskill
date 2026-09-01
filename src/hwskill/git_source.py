from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Callable, Literal, Mapping

from .frontmatter import FrontmatterError, parse_skill_markdown


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class GitSourceError(ValueError):
    """A remote source cannot be safely resolved or inspected."""


@dataclass(frozen=True)
class RemoteRefs:
    default_branch: str
    refs: Mapping[str, str]


@dataclass(frozen=True)
class ResolvedTrack:
    track: str
    kind: Literal["branch", "tag", "commit"]
    commit: str


@dataclass(frozen=True)
class DiscoveredSkill:
    path: str
    name: str
    description: str


class GitSourceClient:
    def __init__(self, runner: CommandRunner = subprocess.run):
        self._runner = runner

    def list_remote(self, repository: str) -> RemoteRefs:
        default_branch, refs = self._read_remote_refs(repository)
        if default_branch is None:
            raise GitSourceError("remote did not advertise a default branch")
        if default_branch not in refs:
            raise GitSourceError(f"remote default branch is not available: {default_branch}")
        return RemoteRefs(default_branch=default_branch, refs=MappingProxyType(dict(refs)))

    def resolve(self, repository: str, track: str) -> ResolvedTrack:
        kind = _track_kind(track)
        if kind == "commit":
            actual = self._verify_commit_track(repository, track)
            return ResolvedTrack(track=track, kind=kind, commit=actual)

        _, refs = self._read_remote_refs(repository)
        if track not in refs:
            raise GitSourceError(f"remote ref not found: {track}")
        return ResolvedTrack(
            track=track,
            kind=kind,
            commit=self._verify_commit_track(repository, track),
        )

    def materialize(self, repository: str, track: str, destination: Path) -> ResolvedTrack:
        _validate_empty_destination(destination)
        resolved = self.resolve(repository, track)
        actual = self._fetch_and_checkout(repository, track, destination)
        if actual != resolved.commit:
            raise GitSourceError(
                f"materialized revision mismatch for {track}: expected {resolved.commit}, got {actual}"
            )
        return resolved

    def _verify_commit_track(self, repository: str, track: str) -> str:
        with TemporaryDirectory(prefix="hwskill-git-source-") as temporary:
            destination = Path(temporary)
            self._initialize_and_fetch(repository, track, destination)
            try:
                completed = self._run(["rev-parse", "FETCH_HEAD^{commit}"], cwd=destination)
            except GitSourceError as error:
                raise GitSourceError(f"track does not resolve to a commit: {track}") from error
            actual = completed.stdout.strip()
        if not _COMMIT_RE.fullmatch(actual):
            raise GitSourceError(f"track did not resolve to a full commit SHA: {actual!r}")
        if _COMMIT_RE.fullmatch(track) and actual != track:
            raise GitSourceError(
                f"commit track did not resolve to the requested commit: expected {track}, got {actual}"
            )
        return actual

    def _fetch_and_checkout(self, repository: str, track: str, destination: Path) -> str:
        self._initialize_and_fetch(repository, track, destination)
        self._run(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=destination)
        completed = self._run(["rev-parse", "HEAD^{commit}"], cwd=destination)
        commit = completed.stdout.strip()
        if not _COMMIT_RE.fullmatch(commit):
            raise GitSourceError(f"checkout did not resolve to a full commit SHA: {commit!r}")
        return commit

    def _initialize_and_fetch(self, repository: str, track: str, destination: Path) -> None:
        self._run(["init", "--quiet"], cwd=destination)
        self._run(["fetch", "--quiet", "--depth=1", repository, track], cwd=destination)

    def _read_remote_refs(self, repository: str) -> tuple[str | None, Mapping[str, str]]:
        completed = self._run(
            ["ls-remote", "--symref", repository, "HEAD", "refs/heads/*", "refs/tags/*"]
        )
        default_branch: str | None = None
        refs: dict[str, str] = {}
        peeled: dict[str, str] = {}

        for line in completed.stdout.splitlines():
            if line.startswith("ref: "):
                target, separator, name = line[5:].partition("\t")
                if separator and name == "HEAD" and target.startswith("refs/heads/"):
                    default_branch = target
                continue
            object_id, separator, ref = line.partition("\t")
            if not separator or not _COMMIT_RE.fullmatch(object_id):
                continue
            if ref.endswith("^{}") and ref.startswith("refs/tags/"):
                peeled[ref[:-3]] = object_id
            elif ref.startswith(("refs/heads/", "refs/tags/")):
                refs[ref] = object_id

        refs.update(peeled)
        return default_branch, MappingProxyType(dict(refs))

    def _run(self, args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            environment.pop(name, None)
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
        with TemporaryDirectory(prefix="hwskill-git-hooks-") as hooks_path:
            command = [
                "git",
                "-c",
                f"core.hooksPath={hooks_path}",
                "-c",
                "submodule.recurse=false",
                *args,
            ]
            try:
                completed = self._runner(
                    command,
                    cwd=cwd,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=True,
                )
            except subprocess.CalledProcessError as error:
                raise GitSourceError(_command_failure(args, error.stderr, error.stdout)) from error
        if completed.returncode:
            raise GitSourceError(_command_failure(args, completed.stderr, completed.stdout))
        return completed


def verify_existing_tag(track: str, expected_commit: str, actual_commit: str) -> None:
    if not track.startswith("refs/tags/"):
        raise GitSourceError(f"not a tag track: {track}")
    if not _COMMIT_RE.fullmatch(expected_commit) or not _COMMIT_RE.fullmatch(actual_commit):
        raise GitSourceError("tag revisions must be full commit SHAs")
    if expected_commit != actual_commit:
        raise GitSourceError(
            f"tag moved: {track} changed from {expected_commit} to {actual_commit}"
        )


def discover_skills(checkout: Path, skills_path: str) -> tuple[DiscoveredSkill, ...]:
    relative_skills_path = _safe_relative_path(skills_path)
    if checkout.is_symlink() or not checkout.is_dir():
        raise GitSourceError("checkout must be a real directory")
    checkout_root = checkout.resolve(strict=True)
    skills_root = checkout / Path(*relative_skills_path.parts)
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise GitSourceError("skills path must be a real directory")
    _ensure_within(checkout_root, skills_root.resolve(strict=True), "skills path")

    discovered: list[DiscoveredSkill] = []
    for child in sorted(skills_root.iterdir(), key=lambda item: item.name):
        if child.is_symlink():
            raise GitSourceError(f"unsafe symlinked Skill directory: {child.name}")
        if not child.is_dir():
            continue
        _ensure_within(checkout_root, child.resolve(strict=True), "Skill directory")
        skill_file = child / "SKILL.md"
        if skill_file.is_symlink():
            raise GitSourceError(f"unsafe symlinked SKILL.md: {child.name}")
        if not skill_file.is_file():
            continue
        try:
            metadata, _ = parse_skill_markdown(skill_file.read_text(encoding="utf-8"))
        except (FrontmatterError, OSError, UnicodeError) as error:
            raise GitSourceError(f"invalid SKILL.md frontmatter for {child.name}: {error}") from error
        discovered.append(
            DiscoveredSkill(
                path=child.relative_to(skills_root).as_posix(),
                name=metadata["name"],
                description=metadata["description"],
            )
        )
    return tuple(discovered)


def _track_kind(track: str) -> Literal["branch", "tag", "commit"]:
    if _COMMIT_RE.fullmatch(track):
        return "commit"
    if track.startswith("refs/heads/") and _valid_ref_suffix(track.removeprefix("refs/heads/")):
        return "branch"
    if track.startswith("refs/tags/") and _valid_ref_suffix(track.removeprefix("refs/tags/")):
        return "tag"
    raise GitSourceError(
        "track must be a fully qualified refs/heads/... or refs/tags/... ref, "
        "or a full 40-character commit SHA"
    )


def _valid_ref_suffix(value: str) -> bool:
    return bool(value) and all(part not in {"", ".", ".."} for part in value.split("/"))


def _validate_empty_destination(destination: Path) -> None:
    if destination.is_symlink() or not destination.is_dir():
        raise GitSourceError("destination must be a caller-owned empty directory")
    if any(destination.iterdir()):
        raise GitSourceError("destination must be a caller-owned empty directory")


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value:
        raise GitSourceError("skills path must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
        raise GitSourceError("skills path must be a safe relative path")
    return path


def _ensure_within(root: Path, candidate: Path, label: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise GitSourceError(f"{label} escapes the checkout") from error


def _command_failure(args: list[str], stderr: str | None, stdout: str | None) -> str:
    detail = (stderr or stdout or "Git command failed").strip()
    return f"git {' '.join(args)} failed: {detail}"
