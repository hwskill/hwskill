"""Descriptor-anchored, gitdir-local pending behavior-verification state."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
from typing import Mapping

from .test_impact import TestSelection, load_impact_inventory


class PendingVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingVerification:
    selection: TestSelection
    digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class VerificationResult:
    selection: TestSelection
    digests: Mapping[str, str]
    case_statuses: tuple[tuple[str, str], ...]


@dataclass
class PreparedPendingVerification:
    path: Path
    directory_fd: int
    temporary_name: str
    previous: bytes | None
    published: bool = False
    closed: bool = False

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
            os.close(self.directory_fd)
            self.closed = True


_PENDING_NAME = "pending-verification.json"


def write_pending_verification(repo_root: Path, selection: TestSelection, digests: Mapping[str, str]) -> Path:
    prepared = prepare_pending_verification(repo_root, selection, digests)
    try:
        prepared.publish()
        return prepared.path
    except BaseException:
        prepared.discard()
        raise


def prepare_pending_verification(repo_root: Path, selection: TestSelection, digests: Mapping[str, str]) -> PreparedPendingVerification:
    directory_fd, directory_path = _open_pending_directory(repo_root, create=True)
    try:
        previous = _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True)
        data = {
            "schema_version": 1,
            "changed_paths": list(selection.changed_paths),
            "digests": dict(sorted((str(key), str(value)) for key, value in digests.items())),
            "selection": _selection_data(selection),
        }
        encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        return PreparedPendingVerification(directory_path / _PENDING_NAME, directory_fd, _write_temporary_at(directory_fd, encoded), previous)
    except BaseException:
        os.close(directory_fd)
        raise


def load_pending_verification(repo_root: Path) -> PendingVerification | None:
    try:
        directory_fd, _ = _open_pending_directory(repo_root, create=False)
    except PendingVerificationError:
        return None
    try:
        raw = _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True)
        return None if raw is None else _parse_pending(raw)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    finally:
        os.close(directory_fd)


def clear_pending_verification(repo_root: Path, passed: VerificationResult) -> bool:
    record = load_pending_verification(repo_root)
    if record is None or record.selection != passed.selection or record.digests != tuple(sorted(passed.digests.items())):
        return False
    try:
        current = {item.skill_id: item.content_digest for item in load_impact_inventory(repo_root).skills}
    except Exception:
        return False
    if any(current.get(skill_id) != digest for skill_id, digest in record.digests):
        return False
    statuses = dict(passed.case_statuses)
    if any(statuses.get(case_id) != "PASS" for case_id in record.selection.required_case_ids):
        return False
    try:
        directory_fd, _ = _open_pending_directory(repo_root, create=False)
    except PendingVerificationError:
        return False
    try:
        if _read_regular_at(directory_fd, _PENDING_NAME, missing_ok=True) is None:
            return False
        os.unlink(_PENDING_NAME, dir_fd=directory_fd)
        _fsync_fd(directory_fd)
        return True
    except OSError:
        return False
    finally:
        os.close(directory_fd)


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
    if not isinstance(data, dict) or set(data) != {"schema_version", "changed_paths", "digests", "selection"} or data["schema_version"] != 1:
        return None
    if not isinstance(data["changed_paths"], list) or not all(_safe_value(item) for item in data["changed_paths"]):
        return None
    if not isinstance(data["digests"], dict) or not all(_safe_value(key) and _safe_value(value) for key, value in data["digests"].items()):
        return None
    selection = _selection_from_data(data["selection"])
    if tuple(sorted(data["changed_paths"])) != selection.changed_paths:
        return None
    return PendingVerification(selection, tuple(sorted(data["digests"].items())))


def _safe_value(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "\x00" not in value and "\n" not in value and len(value) <= 1024


def _selection_data(selection: TestSelection) -> dict[str, object]:
    return {"core": selection.core, "skill_ids": list(selection.skill_ids), "profile_ids": list(selection.profile_ids), "collection_paths": [path.as_posix() for path in selection.collection_paths], "reasons": list(selection.reasons), "changed_paths": list(selection.changed_paths), "digests": [list(item) for item in selection.digests]}


def _selection_from_data(value: object) -> TestSelection:
    if not isinstance(value, dict) or set(value) != {"core", "skill_ids", "profile_ids", "collection_paths", "reasons", "changed_paths", "digests"}:
        raise ValueError("invalid pending selection")
    strings = ("skill_ids", "profile_ids", "collection_paths", "reasons", "changed_paths")
    if not isinstance(value["core"], bool) or any(not isinstance(value[key], list) or not all(_safe_value(item) for item in value[key]) for key in strings):
        raise ValueError("invalid pending selection")
    digest_values = value["digests"]
    if not isinstance(digest_values, list) or not all(isinstance(item, list) and len(item) == 2 and all(_safe_value(part) for part in item) for item in digest_values):
        raise ValueError("invalid pending selection")
    return TestSelection(core=value["core"], skill_ids=tuple(value["skill_ids"]), profile_ids=tuple(value["profile_ids"]), collection_paths=tuple(Path(item) for item in value["collection_paths"]), reasons=tuple(value["reasons"]), changed_paths=tuple(value["changed_paths"]), digests=tuple((item[0], item[1]) for item in digest_values))
