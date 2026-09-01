from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Callable


MANAGED_ROOTS = ("sources", "skills-src", "profiles", "registry", "tests")


class TransactionError(RuntimeError):
    """A candidate repository transaction cannot safely be applied."""


class TransactionConflictError(TransactionError):
    """A target changed after its candidate snapshot was created."""


@dataclass(frozen=True)
class MaintenanceSummary:
    operation: str
    source_ids: tuple[str, ...] = ()
    added_skill_ids: tuple[str, ...] = ()
    updated_skill_ids: tuple[str, ...] = ()
    removed_skill_ids: tuple[str, ...] = ()
    manualized_skill_ids: tuple[str, ...] = ()
    affected_profile_ids: tuple[str, ...] = ()
    affected_test_paths: tuple[str, ...] = ()
    source_details: tuple[dict[str, object], ...] = ()


@dataclass
class MaintenancePlan:
    transaction: "RepositoryTransaction"
    summary: MaintenanceSummary

    def apply(self) -> None:
        self.transaction.apply()


@dataclass(frozen=True)
class _PathState:
    kind: str
    digest: str | None
    device: int | None
    inode: int | None


_MISSING = _PathState("missing", None, None, None)
BeforeOperation = Callable[[int, str], None]
Validate = Callable[[Path], None]


def validated_plan(
    transaction: "RepositoryTransaction",
    summary: MaintenanceSummary,
    validate: Validate,
) -> MaintenancePlan:
    """Close a failed candidate before exposing a maintenance plan."""
    transaction.validate(validate)
    return MaintenancePlan(transaction, summary)


@dataclass
class _TargetHandle:
    relative_path: Path
    parent_fd: int
    name: str


class RepositoryTransaction:
    """Build, validate, and safely apply a candidate for managed repository roots."""

    def __init__(
        self,
        repo_root: Path,
        *,
        validate: Validate | None = None,
        before_operation: BeforeOperation | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.is_dir():
            raise TransactionError(f"repository root does not exist: {self.repo_root}")
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise TransactionError("repository transactions require POSIX O_NOFOLLOW support")
        self._validate = validate
        self._before_operation = before_operation
        self._state = "active"
        self._repo_fd = os.open(
            self.repo_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        self._candidate_directory = Path(tempfile.mkdtemp(prefix="hwskill-candidate-"))
        self.candidate_root = self._candidate_directory
        try:
            self._baseline = _snapshot_managed_roots(self.repo_root)
            for root_name in MANAGED_ROOTS:
                source = self.repo_root / root_name
                if source.exists():
                    _copy_path(source, self.candidate_root / root_name)
        except BaseException:
            self.discard()
            raise

    def __enter__(self) -> "RepositoryTransaction":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.discard()

    def write_bytes(self, relative_path: Path, content: bytes) -> None:
        self._require_active()
        path = self._candidate_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def write_text(
        self,
        relative_path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> None:
        self.write_bytes(relative_path, content.encode(encoding))

    def validate(self, callback: Validate) -> None:
        """Validate the active candidate and discard it if the gate rejects it."""
        self._require_active()
        try:
            callback(self.candidate_root)
        except BaseException:
            self.discard()
            raise

    def delete(self, relative_path: Path) -> None:
        self._require_active()
        path = self._candidate_path(relative_path)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def changed_paths(self) -> tuple[Path, ...]:
        self._require_active()
        candidate = _snapshot_managed_roots(self.candidate_root)
        changed = [
            path
            for path in sorted(set(self._baseline) | set(candidate), key=lambda item: item.as_posix())
            if _candidate_state_changed(self._baseline.get(path, _MISSING), candidate.get(path, _MISSING))
        ]
        replaced_directories = {
            path
            for path in changed
            if self._baseline.get(path, _MISSING).kind == "directory"
            or candidate.get(path, _MISSING).kind == "directory"
        }
        return tuple(
            path
            for path in changed
            if not any(parent != path and parent in replaced_directories for parent in path.parents)
        )

    def apply(self) -> None:
        self._require_active()
        if self._validate is not None:
            self.validate(self._validate)
        targets = self.changed_paths()
        self._assert_preimages(targets)
        if not targets:
            self._state = "applied"
            return

        with tempfile.TemporaryDirectory(prefix="hwskill-backup-") as temporary_backup:
            backup_root = Path(temporary_backup)
            handles: list[_TargetHandle] = []
            recovered_roots: set[str] = set()
            try:
                self._backup_managed_roots(targets, backup_root)
                for relative_path in targets:
                    handle = self._open_verified_target(relative_path)
                    handles.append(handle)
                    if self._baseline.get(relative_path, _MISSING).kind != "missing":
                        _copy_from_fd_to_path(handle.parent_fd, handle.name, backup_root / relative_path)

                applied: list[_TargetHandle] = []
                try:
                    for index, handle in enumerate(handles, start=1):
                        candidate = self.candidate_root / handle.relative_path
                        operation = "replace" if candidate.exists() else "remove"
                        hook_called = False
                        try:
                            if self._before_operation is not None:
                                hook_called = True
                                self._before_operation(
                                    index,
                                    operation,
                                )
                            self._assert_ancestor_identities(handle.relative_path)
                            self._assert_handle_preimage(handle)
                            applied.append(handle)
                            if operation == "replace":
                                _replace_at(handle.parent_fd, handle.name, candidate)
                            else:
                                _remove_at(handle.parent_fd, handle.name)
                        except BaseException:
                            if hook_called:
                                root_name = handle.relative_path.parts[0]
                                self._restore_managed_root(root_name, backup_root)
                                recovered_roots.add(root_name)
                            raise
                except BaseException:
                    rollback_error = self._rollback(applied, backup_root, recovered_roots)
                    if rollback_error is not None:
                        raise TransactionError("transaction failed and rollback was incomplete") from rollback_error
                    raise
            finally:
                for handle in handles:
                    os.close(handle.parent_fd)
        self._state = "applied"

    def discard(self) -> None:
        if self._state == "discarded":
            return
        shutil.rmtree(self._candidate_directory, ignore_errors=True)
        os.close(self._repo_fd)
        self._state = "discarded"

    def _candidate_path(self, relative_path: Path) -> Path:
        relative = Path(relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.parts[0] not in MANAGED_ROOTS
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise TransactionError("transaction targets must be safe paths below managed roots")
        return self.candidate_root / relative

    def _require_active(self) -> None:
        if self._state == "discarded":
            raise TransactionError("transaction is discarded")
        if self._state == "applied":
            raise TransactionError("transaction is already applied")

    def _assert_preimages(self, targets: tuple[Path, ...]) -> None:
        for relative_path in targets:
            handle = self._open_verified_target(relative_path)
            os.close(handle.parent_fd)

    def _backup_managed_roots(self, targets: tuple[Path, ...], backup_root: Path) -> None:
        for root_name in sorted({path.parts[0] for path in targets}):
            expected = self._baseline.get(Path(root_name), _MISSING)
            actual = _path_state_at(self._repo_fd, root_name)
            if actual != expected:
                raise TransactionConflictError(
                    f"managed root preimage changed: {root_name}"
                )
            if expected.kind != "missing":
                _copy_from_fd_to_path(
                    self._repo_fd,
                    root_name,
                    backup_root / "managed-roots" / root_name,
                )

    def _restore_managed_root(self, root_name: str, backup_root: Path) -> None:
        _remove_entry_at(self._repo_fd, root_name)
        if self._baseline.get(Path(root_name), _MISSING).kind != "missing":
            _copy_path_to_fd(
                backup_root / "managed-roots" / root_name,
                self._repo_fd,
                root_name,
            )

    def _open_verified_target(self, relative_path: Path) -> _TargetHandle:
        parent_fd = os.dup(self._repo_fd)
        try:
            for index, part in enumerate(relative_path.parts[:-1], start=1):
                ancestor = Path(*relative_path.parts[:index])
                child_fd = _open_directory_at(parent_fd, part)
                actual = _directory_state_from_fd(child_fd)
                expected = self._baseline.get(ancestor, _MISSING)
                if actual != expected:
                    os.close(child_fd)
                    raise TransactionConflictError(
                        f"target ancestor preimage changed: {ancestor.as_posix()}"
                    )
                os.close(parent_fd)
                parent_fd = child_fd
            handle = _TargetHandle(relative_path, parent_fd, relative_path.name)
            self._assert_handle_preimage(handle)
            return handle
        except BaseException:
            os.close(parent_fd)
            raise

    def _assert_handle_preimage(self, handle: _TargetHandle) -> None:
        expected = self._baseline.get(handle.relative_path, _MISSING)
        actual = _path_state_at(handle.parent_fd, handle.name)
        if actual != expected:
            raise TransactionConflictError(
                f"target preimage changed: {handle.relative_path.as_posix()}"
            )

    def _assert_ancestor_identities(self, relative_path: Path) -> None:
        parent_fd = os.dup(self._repo_fd)
        try:
            for index, part in enumerate(relative_path.parts[:-1], start=1):
                ancestor = Path(*relative_path.parts[:index])
                child_fd = _open_directory_at(parent_fd, part)
                actual = _directory_state_from_fd(child_fd)
                expected = self._baseline.get(ancestor, _MISSING)
                if (
                    actual.kind != "directory"
                    or actual.device != expected.device
                    or actual.inode != expected.inode
                ):
                    os.close(child_fd)
                    raise TransactionConflictError(
                        f"target ancestor changed: {ancestor.as_posix()}"
                    )
                os.close(parent_fd)
                parent_fd = child_fd
        finally:
            os.close(parent_fd)

    def _rollback(
        self,
        applied: list[_TargetHandle],
        backup_root: Path,
        recovered_roots: set[str],
    ) -> BaseException | None:
        rollback_error: BaseException | None = None
        for handle in reversed(applied):
            if handle.relative_path.parts[0] in recovered_roots:
                continue
            try:
                original = self._baseline.get(handle.relative_path, _MISSING)
                _remove_at(handle.parent_fd, handle.name)
                if original.kind != "missing":
                    _copy_path_to_fd(backup_root / handle.relative_path, handle.parent_fd, handle.name)
            except BaseException as exc:
                rollback_error = rollback_error or exc
        return rollback_error


def _snapshot_managed_roots(root: Path) -> dict[Path, _PathState]:
    snapshot: dict[Path, _PathState] = {}
    for root_name in MANAGED_ROOTS:
        managed_root = root / root_name
        _snapshot_path(managed_root, Path(root_name), snapshot)
    return snapshot


def _snapshot_path(path: Path, relative: Path, snapshot: dict[Path, _PathState]) -> None:
    state = _path_state(path)
    snapshot[relative] = state
    if state.kind == "directory":
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            _snapshot_path(child, relative / child.name, snapshot)


def _path_state(path: Path) -> _PathState:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _MISSING
    if stat.S_ISREG(metadata.st_mode):
        return _PathState("file", _file_digest(path), metadata.st_dev, metadata.st_ino)
    if stat.S_ISDIR(metadata.st_mode):
        return _PathState("directory", _directory_digest(path), metadata.st_dev, metadata.st_ino)
    raise TransactionError(f"unsupported transaction path type: {path}")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        state = _path_state(child)
        _update_directory_digest(digest, child.name, state)
    return digest.hexdigest()


def _update_directory_digest(digest: "hashlib._Hash", name: str, state: _PathState) -> None:
    digest.update(state.kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(name.encode("utf-8"))
    digest.update(b"\0")
    if state.digest is not None:
        digest.update(state.digest.encode("ascii"))
        digest.update(b"\0")


def _candidate_state_changed(before: _PathState, after: _PathState) -> bool:
    if before.kind != after.kind:
        return True
    return before.kind == "file" and before.digest != after.digest


def _path_state_at(parent_fd: int, name: str) -> _PathState:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _MISSING
    if stat.S_ISREG(metadata.st_mode):
        file_fd = _open_file_at(parent_fd, name)
        file_metadata = os.fstat(file_fd)
        return _PathState(
            "file",
            _file_digest_fd(file_fd),
            file_metadata.st_dev,
            file_metadata.st_ino,
        )
    if stat.S_ISDIR(metadata.st_mode):
        directory_fd = _open_directory_at(parent_fd, name)
        try:
            return _directory_state_from_fd(directory_fd)
        finally:
            os.close(directory_fd)
    raise TransactionError(f"unsupported transaction path type: {name}")


def _directory_state_from_fd(directory_fd: int) -> _PathState:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise TransactionError("transaction ancestor is not a directory")
    return _PathState(
        "directory",
        _directory_digest_from_fd(directory_fd),
        metadata.st_dev,
        metadata.st_ino,
    )


def _directory_digest_from_fd(directory_fd: int) -> str:
    digest = hashlib.sha256()
    for name in sorted(os.listdir(directory_fd)):
        _update_directory_digest(digest, name, _path_state_at(directory_fd, name))
    return digest.hexdigest()


def _open_directory_at(parent_fd: int, name: str) -> int:
    try:
        directory_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise TransactionConflictError(f"transaction ancestor is unsafe: {name}") from exc
    if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
        os.close(directory_fd)
        raise TransactionConflictError(f"transaction ancestor is not a directory: {name}")
    return directory_fd


def _open_file_at(parent_fd: int, name: str) -> int:
    try:
        file_fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise TransactionConflictError(f"transaction target is unsafe: {name}") from exc
    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
        os.close(file_fd)
        raise TransactionConflictError(f"transaction target is not a regular file: {name}")
    return file_fd


def _file_digest_fd(file_fd: int) -> str:
    digest = hashlib.sha256()
    try:
        while block := os.read(file_fd, 1024 * 1024):
            digest.update(block)
    finally:
        os.close(file_fd)
    return digest.hexdigest()


def _replace_at(parent_fd: int, name: str, source: Path) -> None:
    _remove_at(parent_fd, name)
    _copy_path_to_fd(source, parent_fd, name)


def _remove_at(parent_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISREG(metadata.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise TransactionError(f"unsupported transaction path type: {name}")
    directory_fd = _open_directory_at(parent_fd, name)
    try:
        for child_name in os.listdir(directory_fd):
            _remove_at(directory_fd, child_name)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _remove_entry_at(parent_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    _clear_recovery_directory_at(parent_fd, name)


def _clear_recovery_directory_at(parent_fd: int, name: str) -> None:
    directory_fd = _open_directory_at(parent_fd, name)
    try:
        for child_name in os.listdir(directory_fd):
            _remove_entry_at(directory_fd, child_name)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _copy_path(source: Path, destination: Path) -> None:
    state = _path_state(source)
    if state.kind == "file":
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    elif state.kind == "directory":
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    else:
        raise TransactionError(f"cannot copy missing transaction path: {source}")


def _copy_path_to_fd(source: Path, parent_fd: int, name: str) -> None:
    state = _path_state(source)
    if state.kind == "file":
        destination_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            stat.S_IMODE(source.stat().st_mode),
            dir_fd=parent_fd,
        )
        try:
            with source.open("rb") as source_file:
                while block := source_file.read(1024 * 1024):
                    _write_all(destination_fd, block)
        finally:
            os.close(destination_fd)
        return
    if state.kind != "directory":
        raise TransactionError(f"cannot copy missing transaction path: {source}")
    os.mkdir(name, stat.S_IMODE(source.stat().st_mode), dir_fd=parent_fd)
    destination_fd = _open_directory_at(parent_fd, name)
    try:
        for child in source.iterdir():
            _copy_path_to_fd(child, destination_fd, child.name)
    finally:
        os.close(destination_fd)


def _copy_from_fd_to_path(parent_fd: int, name: str, destination: Path) -> None:
    state = _path_state_at(parent_fd, name)
    if state.kind == "file":
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_fd = _open_file_at(parent_fd, name)
        try:
            with destination.open("xb") as destination_file:
                while block := os.read(file_fd, 1024 * 1024):
                    destination_file.write(block)
        finally:
            os.close(file_fd)
        return
    if state.kind != "directory":
        raise TransactionError(f"cannot copy missing transaction path: {name}")
    destination.mkdir(parents=True)
    directory_fd = _open_directory_at(parent_fd, name)
    try:
        for child_name in os.listdir(directory_fd):
            _copy_from_fd_to_path(directory_fd, child_name, destination / child_name)
    finally:
        os.close(directory_fd)


def _write_all(file_fd: int, block: bytes) -> None:
    remaining = memoryview(block)
    while remaining:
        written = os.write(file_fd, remaining)
        remaining = remaining[written:]
