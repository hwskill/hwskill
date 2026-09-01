"""Gitdir-local, fail-closed pending behavior-verification state."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
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
    """A fsynced but unpublished replacement, used to gate a transaction."""
    path: Path
    temporary: Path

    def publish(self) -> Path:
        if not self.temporary.exists():
            raise PendingVerificationError("prepared pending verification state is missing")
        try:
            os.replace(self.temporary, self.path)
            os.chmod(self.path, 0o600)
            _fsync_directory(self.path.parent)
            return self.path
        except OSError as exc:
            raise PendingVerificationError("cannot publish pending verification state") from exc

    def discard(self) -> None:
        self.temporary.unlink(missing_ok=True)


def write_pending_verification(repo_root: Path, selection: TestSelection, digests: Mapping[str, str]) -> Path:
    """Atomically replace state under the real Git directory, never the worktree."""
    prepared = prepare_pending_verification(repo_root, selection, digests)
    try:
        return prepared.publish()
    except BaseException:
        prepared.discard()
        raise


def prepare_pending_verification(repo_root: Path, selection: TestSelection, digests: Mapping[str, str]) -> PreparedPendingVerification:
    """Create and fsync local state without making it visible as current yet."""
    path = _pending_path(repo_root, create_parent=True)
    data = {
        "schema_version": 1,
        "changed_paths": list(selection.changed_paths),
        "digests": dict(sorted((str(key), str(value)) for key, value in digests.items())),
        "selection": _selection_data(selection),
    }
    encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return _prepare_atomic_write(path, encoded)


def load_pending_verification(repo_root: Path) -> PendingVerification | None:
    try:
        path = _pending_path(repo_root, create_parent=False)
    except PendingVerificationError:
        return None
    if not _safe_regular_file(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"schema_version", "changed_paths", "digests", "selection"} or data["schema_version"] != 1:
            return None
        if not isinstance(data["changed_paths"], list) or not all(isinstance(item, str) for item in data["changed_paths"]):
            return None
        if not isinstance(data["digests"], dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in data["digests"].items()):
            return None
        selection = _selection_from_data(data["selection"])
        if tuple(sorted(data["changed_paths"])) != selection.changed_paths:
            return None
        return PendingVerification(selection, tuple(sorted(data["digests"].items())))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def clear_pending_verification(repo_root: Path, passed: VerificationResult) -> bool:
    """Clear only a matching record with PASS evidence for every selected target."""
    record = load_pending_verification(repo_root)
    if record is None:
        return False
    if record.selection != passed.selection or record.digests != tuple(sorted(passed.digests.items())):
        return False
    try:
        current_digests = {item.skill_id: item.content_digest for item in load_impact_inventory(repo_root).skills}
    except Exception:
        # A missing or unreadable inventory must never turn unverified changes into
        # verified ones. The caller can repair it and rerun the affected tests.
        return False
    if any(current_digests.get(skill_id) != digest for skill_id, digest in record.digests):
        return False
    statuses = dict(passed.case_statuses)
    if any(statuses.get(case_id) != "PASS" for case_id in record.selection.required_case_ids):
        return False
    try:
        path = _pending_path(repo_root, create_parent=False)
        if not _safe_regular_file(path):
            return False
        path.unlink()
        _fsync_directory(path.parent)
        return True
    except OSError:
        return False


def _pending_path(repo_root: Path, *, create_parent: bool) -> Path:
    root = Path(repo_root).resolve()
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=root, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=environment,
    )
    if completed.returncode:
        raise PendingVerificationError("cannot resolve Git directory")
    raw = completed.stdout.decode("utf-8", errors="strict").strip()
    if not raw:
        raise PendingVerificationError("Git returned an empty Git directory")
    untrusted = Path(raw)
    if not untrusted.is_absolute() and not _safe_relative_git_dir(root, untrusted):
        raise PendingVerificationError("relative Git directory escapes repository or uses a symlink")
    git_dir = (root / untrusted if not untrusted.is_absolute() else untrusted).resolve(strict=True)
    if not git_dir.is_dir() or not (git_dir / "HEAD").is_file():
        raise PendingVerificationError("resolved Git directory is invalid")
    parent = git_dir / "hwskill"
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise PendingVerificationError("pending verification directory is unsafe")
    if create_parent:
        parent.mkdir(mode=0o700, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise PendingVerificationError("pending verification directory is unsafe")
    return parent / "pending-verification.json"


def _safe_relative_git_dir(root: Path, relative: Path) -> bool:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        current = root
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                return False
        return True
    except (OSError, ValueError):
        return False


def _prepare_atomic_write(path: Path, value: bytes) -> PreparedPendingVerification:
    if path.exists() and not _safe_regular_file(path):
        raise PendingVerificationError("pending verification file is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-verification.", dir=path.parent)
    temporary = Path(temporary_name)
    prepared: PreparedPendingVerification | None = None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        prepared = PreparedPendingVerification(path, temporary)
        return prepared
    except OSError as exc:
        raise PendingVerificationError("cannot atomically write pending verification state") from exc
    finally:
        if temporary.exists() and prepared is None:
            temporary.unlink(missing_ok=True)


def _safe_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink()
    except OSError:
        return False


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _selection_data(selection: TestSelection) -> dict[str, object]:
    return {
        "core": selection.core,
        "skill_ids": list(selection.skill_ids),
        "profile_ids": list(selection.profile_ids),
        "collection_paths": [path.as_posix() for path in selection.collection_paths],
        "reasons": list(selection.reasons),
        "changed_paths": list(selection.changed_paths),
        "digests": [list(item) for item in selection.digests],
    }


def _selection_from_data(value: object) -> TestSelection:
    if not isinstance(value, dict) or set(value) != {"core", "skill_ids", "profile_ids", "collection_paths", "reasons", "changed_paths", "digests"}:
        raise ValueError("invalid pending selection")
    strings = ("skill_ids", "profile_ids", "collection_paths", "reasons", "changed_paths")
    if not isinstance(value["core"], bool) or any(not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]) for key in strings):
        raise ValueError("invalid pending selection")
    digest_values = value["digests"]
    if not isinstance(digest_values, list) or not all(isinstance(item, list) and len(item) == 2 and all(isinstance(part, str) for part in item) for item in digest_values):
        raise ValueError("invalid pending selection")
    return TestSelection(
        core=value["core"], skill_ids=tuple(value["skill_ids"]), profile_ids=tuple(value["profile_ids"]),
        collection_paths=tuple(Path(item) for item in value["collection_paths"]), reasons=tuple(value["reasons"]),
        changed_paths=tuple(value["changed_paths"]), digests=tuple((item[0], item[1]) for item in digest_values),
    )
