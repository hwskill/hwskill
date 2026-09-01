"""Descriptor-anchored, gitdir-local pending behavior-verification state."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
from typing import Mapping
import unicodedata

from .test_impact import TestSelection, load_impact_inventory


class PendingVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingVerification:
    identity: str


@dataclass(frozen=True)
class VerificationResult:
    selection: TestSelection
    digests: Mapping[str, str]
    case_statuses: tuple[tuple[str, str], ...]


@dataclass
class RepositoryMutationGuard:
    """The gitdir-local lock shared by pending state and repository applies."""

    directory_path: Path | None
    directory_fd: int | None
    lock_fd: int | None
    closed: bool = False

    def require_pending_directory(self) -> tuple[Path, int]:
        if self.closed or self.directory_path is None or self.directory_fd is None:
            raise PendingVerificationError("pending verification state requires a Git repository")
        return self.directory_path, self.directory_fd

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.directory_fd is not None and self.lock_fd is not None:
            _close_pending_state(self.directory_fd, self.lock_fd)


@dataclass
class PreparedPendingVerification:
    path: Path
    guard: RepositoryMutationGuard
    temporary_name: str
    previous: bytes | None
    owns_guard: bool = True
    published: bool = False
    closed: bool = False

    @property
    def directory_fd(self) -> int:
        return self.guard.require_pending_directory()[1]

    @property
    def lock_fd(self) -> int:
        if self.guard.lock_fd is None:
            raise PendingVerificationError("pending verification state requires a Git repository")
        return self.guard.lock_fd

    def publish(self) -> None:
        self._require_open()
        try:
            os.replace(self.temporary_name, _PENDING_NAME, src_dir_fd=self.directory_fd, dst_dir_fd=self.directory_fd)
            self.published = True
            _assert_regular_at(self.directory_fd, _PENDING_NAME)
            os.chmod(_PENDING_NAME, 0o600, dir_fd=self.directory_fd, follow_symlinks=False)
            _fsync_fd(self.directory_fd)
        except OSError as exc:
            raise PendingVerificationError("cannot publish pending verification state") from exc
        else:
            self._close()

    def discard(self) -> None:
        if self.closed:
            return
        try:
            _unlink_at(self.directory_fd, self.temporary_name)
            if self.published:
                _restore_previous(self.directory_fd, self.previous)
        finally:
            self._close()

    def _require_open(self) -> None:
        if self.closed:
            raise PendingVerificationError("prepared pending verification state is closed")

    def _close(self) -> None:
        if not self.closed:
            self.closed = True
            if self.owns_guard:
                self.guard.close()


_PENDING_NAME = "pending-verification.json"
_LOCK_NAME = "pending-verification.lock"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PENDING_SCHEMA_VERSION = 2
_MAX_IDENTIFIER_COMPONENT_LENGTH = 255
_MAX_REPOSITORY_PATH_LENGTH = 4096


def write_pending_verification(repo_root: Path, selection: TestSelection, digests: Mapping[str, str]) -> Path:
    prepared = prepare_pending_verification(repo_root, selection, digests)
    try:
        prepared.publish()
        return prepared.path
    except BaseException:
        prepared.discard()
        raise


def verification_identity(selection: TestSelection, digests: Mapping[str, str]) -> str:
    """Return the opaque identity used to bind pending state to verification evidence."""
    return _verification_identity(selection, digests)


def acquire_repository_mutation_guard(repo_root: Path) -> RepositoryMutationGuard:
    """Acquire the one gitdir-local mutation lock, or a no-op non-Git guard.

    A real Git repository always uses the descriptor-anchored pending directory
    lock. A repository without a ``.git`` entry cannot contain gitdir-local
    pending state and is retained as a supported transaction-test boundary.
    """
    try:
        return _acquire_repository_guard(repo_root, create=True)
    except PendingVerificationError:
        if not (Path(repo_root) / ".git").exists():
            return RepositoryMutationGuard(None, None, None)
        raise


def _acquire_repository_guard(repo_root: Path, *, create: bool) -> RepositoryMutationGuard:
    directory_fd, directory_path = _open_pending_directory(repo_root, create=create)
    lock_fd: int | None = None
    try:
        lock_fd = _acquire_pending_lock(directory_fd)
        return RepositoryMutationGuard(directory_path, directory_fd, lock_fd)
    except BaseException:
        if lock_fd is None:
            os.close(directory_fd)
        else:
            _close_pending_state(directory_fd, lock_fd)
        raise


def prepare_pending_verification(
    repo_root: Path,
    selection: TestSelection,
    digests: Mapping[str, str],
    *,
    guard: RepositoryMutationGuard | None = None,
) -> PreparedPendingVerification:
    data = _pending_data(selection, digests)
    owns_guard = guard is None
    if guard is None:
        guard = acquire_repository_mutation_guard(repo_root)
    try:
        directory_path, directory_fd = guard.require_pending_directory()
        previous = _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True)
        encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        return PreparedPendingVerification(
            directory_path / _PENDING_NAME, guard,
            _write_temporary_at(directory_fd, encoded), previous, owns_guard,
        )
    except BaseException:
        if owns_guard:
            guard.close()
        raise


def load_pending_verification(repo_root: Path) -> PendingVerification | None:
    try:
        guard = _acquire_repository_guard(repo_root, create=False)
    except PendingVerificationError:
        return None
    try:
        _, directory_fd = guard.require_pending_directory()
        raw = _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True)
        return None if raw is None else _parse_pending(raw)
    except (PendingVerificationError, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    finally:
        guard.close()


def clear_pending_verification(repo_root: Path, passed: VerificationResult) -> bool:
    try:
        expected_identity = _verification_identity(passed.selection, passed.digests)
    except PendingVerificationError:
        return False
    try:
        guard = _acquire_repository_guard(repo_root, create=False)
    except PendingVerificationError:
        return False
    try:
        _, directory_fd = guard.require_pending_directory()
        raw = _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True)
        record = None if raw is None else _parse_pending(raw)
        if record is None or record.identity != expected_identity:
            return False
        try:
            current = {item.skill_id: item.content_digest for item in load_impact_inventory(repo_root).skills}
        except Exception:
            return False
        if any(current.get(skill_id) != digest for skill_id, digest in passed.digests.items()):
            return False
        statuses = dict(passed.case_statuses)
        if any(statuses.get(case_id) != "PASS" for case_id in passed.selection.required_case_ids):
            return False
        _before_clear_unlink()
        if _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True) != raw:
            return False
        os.unlink(_PENDING_NAME, dir_fd=directory_fd)
        _fsync_fd(directory_fd)
        return True
    except (PendingVerificationError, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False
    finally:
        guard.close()


def _open_pending_directory(repo_root: Path, *, create: bool) -> tuple[int, Path]:
    git_fd, git_path = _open_git_directory(repo_root)
    try:
        try:
            pending_fd = os.open("hwskill", _directory_flags(), dir_fd=git_fd)
        except FileNotFoundError:
            if not create:
                raise PendingVerificationError("pending verification directory is missing")
            os.mkdir("hwskill", 0o700, dir_fd=git_fd)
            pending_fd = os.open("hwskill", _directory_flags(), dir_fd=git_fd)
    except OSError as exc:
        raise PendingVerificationError("pending verification directory is unsafe") from exc
    finally:
        os.close(git_fd)
    return pending_fd, git_path / "hwskill"


def _open_git_directory(repo_root: Path) -> tuple[int, Path]:
    root = Path(repo_root).absolute()
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=environment)
    if completed.returncode:
        raise PendingVerificationError("cannot resolve Git directory")
    raw = completed.stdout.decode("utf-8", errors="strict").strip()
    candidate = Path(raw)
    if not raw or (not candidate.is_absolute() and ".." in candidate.parts):
        raise PendingVerificationError("Git returned an unsafe Git directory")
    path = candidate if candidate.is_absolute() else root / candidate
    fd = _open_directory_path(path)
    try:
        _assert_regular_at(fd, "HEAD")
    except BaseException:
        os.close(fd)
        raise PendingVerificationError("resolved Git directory is invalid")
    return fd, path


def _open_directory_path(path: Path) -> int:
    absolute = Path(path)
    if not absolute.is_absolute():
        raise PendingVerificationError("Git directory must be absolute")
    fd = os.open("/", _directory_flags())
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, _directory_flags(), dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        os.close(fd)
        raise PendingVerificationError("cannot safely open Git directory") from exc


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _acquire_pending_lock(directory_fd: int) -> int:
    try:
        lock_fd = os.open(
            _LOCK_NAME,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise PendingVerificationError("pending verification lock is unsafe") from exc
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise PendingVerificationError("pending verification lock is unsafe")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return lock_fd
    except BaseException:
        os.close(lock_fd)
        raise


def _close_pending_state(directory_fd: int, lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        try:
            os.close(lock_fd)
        finally:
            os.close(directory_fd)


def _before_clear_unlink() -> None:
    """Deterministic test seam; the pending-state lock remains held here."""


def _write_temporary_at(directory_fd: int, value: bytes) -> str:
    for _ in range(32):
        name = ".pending-" + secrets.token_hex(16)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
        try:
            os.write(fd, value)
            os.fsync(fd)
        except OSError as exc:
            _unlink_at(directory_fd, name)
            raise PendingVerificationError("cannot prepare pending verification state") from exc
        finally:
            os.close(fd)
        return name
    raise PendingVerificationError("cannot allocate pending verification state")


def _restore_previous(directory_fd: int, previous: bytes | None) -> None:
    if previous is None:
        _unlink_at(directory_fd, _PENDING_NAME)
        _fsync_fd(directory_fd)
        return
    temporary_name = _write_temporary_at(directory_fd, previous)
    try:
        os.replace(temporary_name, _PENDING_NAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.chmod(_PENDING_NAME, 0o600, dir_fd=directory_fd, follow_symlinks=False)
        _fsync_fd(directory_fd)
    finally:
        _unlink_at(directory_fd, temporary_name)


def _read_regular_at(directory_fd: int, name: str, *, missing_ok: bool) -> bytes | None:
    try:
        _assert_regular_at(directory_fd, name)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    finally:
        os.close(fd)


def _assert_regular_at(directory_fd: int, name: str) -> None:
    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise PendingVerificationError("pending verification file is unsafe")


def _unlink_at(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def _fsync_fd(fd: int) -> None:
    os.fsync(fd)


def _parse_pending(raw: bytes) -> PendingVerification | None:
    data = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "verification_identity"}
        or data["schema_version"] != _PENDING_SCHEMA_VERSION
        or not _digest_is_safe(data["verification_identity"])
    ):
        return None
    return PendingVerification(data["verification_identity"])


def _pending_data(selection: TestSelection, digests: Mapping[str, str]) -> dict[str, object]:
    return {
        "schema_version": _PENDING_SCHEMA_VERSION,
        "verification_identity": _verification_identity(selection, digests),
    }


def _verification_identity(selection: TestSelection, digests: Mapping[str, str]) -> str:
    if not _selection_is_safe(selection) or not _digest_mapping_is_safe(digests):
        raise PendingVerificationError("pending verification state contains unsafe metadata")
    payload = {
        "selection": _selection_data(selection),
        "digests": [[key, value] for key, value in sorted(digests.items())],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _selection_is_safe(selection: object) -> bool:
    if not isinstance(selection, TestSelection) or not isinstance(selection.core, bool):
        return False
    if not all(_skill_id_is_safe(skill_id) for skill_id in selection.skill_ids):
        return False
    if not all(_profile_id_is_safe(profile_id) for profile_id in selection.profile_ids):
        return False
    if not all(_collection_path_is_safe(path) for path in selection.collection_paths):
        return False
    if not all(_reason_is_safe(reason) for reason in selection.reasons):
        return False
    if not all(_repo_path_is_safe(path) for path in selection.changed_paths):
        return False
    if not all(
        isinstance(item, tuple) and len(item) == 2 and _skill_id_is_safe(item[0]) and _digest_is_safe(item[1])
        for item in selection.digests
    ):
        return False
    return all(_case_id_is_safe(case_id) for case_id in selection.required_case_ids)


def _digest_mapping_is_safe(digests: object) -> bool:
    return isinstance(digests, Mapping) and all(
        _skill_id_is_safe(key) and _digest_is_safe(value) for key, value in digests.items()
    )


def _identifier_component_is_safe(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= _MAX_IDENTIFIER_COMPONENT_LENGTH
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and not _contains_control(value)
    )


def _skill_id_is_safe(value: object) -> bool:
    return isinstance(value, str) and len(value.split("/")) == 2 and all(
        _identifier_component_is_safe(part) for part in value.split("/")
    )


def _profile_id_is_safe(value: object) -> bool:
    return _identifier_component_is_safe(value)


def _case_id_is_safe(value: object) -> bool:
    if value == "core":
        return True
    if not isinstance(value, str) or ":" not in value:
        return False
    kind, identifier = value.split(":", 1)
    return (kind == "skill" and _skill_id_is_safe(identifier)) or (
        kind == "profile" and _profile_id_is_safe(identifier)
    )


def _digest_is_safe(value: object) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def _repo_path_is_safe(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_REPOSITORY_PATH_LENGTH
        or "\\" in value
        or _contains_control(value)
    ):
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {".", ".."} for part in path.parts):
        return False
    return all(_repo_path_component_is_safe(part) for part in path.parts)


def _repo_path_component_is_safe(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= _MAX_IDENTIFIER_COMPONENT_LENGTH
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and not _contains_control(value)
    )


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _collection_path_is_safe(value: object) -> bool:
    if not isinstance(value, Path) or not _repo_path_is_safe(value.as_posix()):
        return False
    parts = value.parts
    return (
        len(parts) == 5
        and parts[:2] == ("tests", "skills")
        and _skill_id_is_safe("/".join(parts[2:4]))
        and parts[4] == "test.yaml"
    ) or (
        len(parts) == 4
        and parts[:2] == ("tests", "profiles")
        and _profile_id_is_safe(parts[2])
        and parts[3] == "test.yaml"
    )


def _reason_is_safe(value: object) -> bool:
    if value in {"runtime-all", "core-source", "core-test"}:
        return True
    if not isinstance(value, str) or ":" not in value:
        return False
    kind, identifier = value.split(":", 1)
    return (kind in {"skill-added", "skill-content", "test-skill"} and _skill_id_is_safe(identifier)) or (
        kind in {"profile-members", "profile-member-content", "test-profile"}
        and _profile_id_is_safe(identifier)
    )


def _selection_data(selection: TestSelection) -> dict[str, object]:
    return {"core": selection.core, "skill_ids": list(selection.skill_ids), "profile_ids": list(selection.profile_ids), "collection_paths": [path.as_posix() for path in selection.collection_paths], "reasons": list(selection.reasons), "changed_paths": list(selection.changed_paths), "digests": [list(item) for item in selection.digests]}
