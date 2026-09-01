from __future__ import annotations

from dataclasses import dataclass
import hashlib
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


_MISSING = _PathState("missing", None)
Replace = Callable[[Path, Path], None]
Remove = Callable[[Path], None]
Validate = Callable[[Path], None]


class RepositoryTransaction:
    """Build, validate, and safely apply a candidate for managed repository roots."""

    def __init__(
        self,
        repo_root: Path,
        *,
        validate: Validate | None = None,
        replace: Replace | None = None,
        remove: Remove | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.is_dir():
            raise TransactionError(f"repository root does not exist: {self.repo_root}")
        self._validate = validate
        self._replace = replace or _replace_path
        self._remove = remove or _remove_path
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

    def delete(self, relative_path: Path) -> None:
        path = self._candidate_path(relative_path)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def changed_paths(self) -> tuple[Path, ...]:
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
        if self._validate is not None:
            self._validate(self.candidate_root)
        targets = self.changed_paths()
        self._assert_preimages(targets)
        if not targets:
            return

        with tempfile.TemporaryDirectory(prefix="hwskill-backup-") as temporary_backup:
            backup_root = Path(temporary_backup)
            for relative_path in targets:
                original = self.repo_root / relative_path
                if self._baseline.get(relative_path, _MISSING).kind != "missing":
                    _copy_path(original, backup_root / relative_path)

            applied: list[Path] = []
            try:
                for relative_path in targets:
                    applied.append(relative_path)
                    candidate = self.candidate_root / relative_path
                    destination = self.repo_root / relative_path
                    if candidate.exists():
                        self._replace(candidate, destination)
                    else:
                        self._remove(destination)
            except BaseException:
                rollback_error = self._rollback(applied, backup_root)
                if rollback_error is not None:
                    raise TransactionError("transaction failed and rollback was incomplete") from rollback_error
                raise

    def discard(self) -> None:
        shutil.rmtree(self._candidate_directory, ignore_errors=True)

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

    def _assert_preimages(self, targets: tuple[Path, ...]) -> None:
        for relative_path in targets:
            expected = self._baseline.get(relative_path, _MISSING)
            actual = _path_state(self.repo_root / relative_path)
            if actual != expected:
                raise TransactionConflictError(
                    f"target preimage changed: {relative_path.as_posix()}"
                )

    def _rollback(self, applied: list[Path], backup_root: Path) -> BaseException | None:
        rollback_error: BaseException | None = None
        for relative_path in reversed(applied):
            try:
                destination = self.repo_root / relative_path
                original = self._baseline.get(relative_path, _MISSING)
                _remove_path(destination)
                if original.kind != "missing":
                    _copy_path(backup_root / relative_path, destination)
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
        return _PathState("file", _file_digest(path))
    if stat.S_ISDIR(metadata.st_mode):
        return _PathState("directory", _directory_digest(path))
    raise TransactionError(f"unsupported transaction path type: {path}")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        state = _path_state(child)
        digest.update(state.kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(relative)
        digest.update(b"\0")
        if state.digest is not None:
            digest.update(state.digest.encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def _candidate_state_changed(before: _PathState, after: _PathState) -> bool:
    if before.kind != after.kind:
        return True
    return before.kind == "file" and before.digest != after.digest


def _replace_path(source: Path, destination: Path) -> None:
    _remove_path(destination)
    _copy_path(source, destination)


def _remove_path(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        shutil.rmtree(path)
    elif stat.S_ISREG(metadata.st_mode):
        path.unlink()
    else:
        raise TransactionError(f"unsupported transaction path type: {path}")


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
