from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Any, Iterator, Mapping
from urllib.parse import unquote, urlsplit
import weakref

from hwskill.directory.schema import validator_for

from .models import Candidate, CandidateState, stable_digest


class PublishingStoreError(RuntimeError):
    pass


class PublicationLockedError(PublishingStoreError):
    pass


class ReleaseIntegrityError(PublishingStoreError):
    pass


class ImmutableReleaseError(ReleaseIntegrityError):
    pass


class StaleReleaseError(PublishingStoreError):
    pass


_RELEASE_ID = re.compile(r"^release-[0-9a-f]{64}$")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_RENAME_NOREPLACE = 1


def _validate_release_id(release_id: str) -> str:
    if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
        raise ValueError("release_id must be release- followed by 64 lowercase hexadecimal characters")
    return release_id


def _validate_sequence(sequence: int) -> int:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("sequence must be a positive integer")
    return sequence


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _close_fds(fds: tuple[int, ...]) -> None:
    for descriptor in reversed(fds):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _open_absolute_directory(path: Path, *, create: bool) -> int:
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise ReleaseIntegrityError(f"unsafe or unavailable directory {path}: {exc.strerror}") from exc


def _open_or_create_dir_at(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise ReleaseIntegrityError(f"managed directory {name!r} is unsafe: {exc.strerror}") from exc


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_regular_at(parent_fd: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unsafe or missing regular file {name!r}: {exc.strerror}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseIntegrityError(f"non-regular file is forbidden: {name}")
        if before.st_nlink != 1:
            raise ReleaseIntegrityError(f"hard-linked artifact files are forbidden: {name}")
        body = _read_all(descriptor)
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after or len(body) != after.st_size:
            raise ReleaseIntegrityError(f"file changed while being read: {name}")
        return body
    finally:
        os.close(descriptor)


def _read_tree_fd(
    directory_fd: int,
    prefix: str = "",
) -> tuple[dict[str, str], dict[str, bytes]]:
    manifest: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for name in sorted(os.listdir(directory_fd)):
        relative = f"{prefix}/{name}" if prefix else name
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                manifest[relative + "/"] = "directory"
                child_manifest, child_files = _read_tree_fd(child, relative)
                manifest.update(child_manifest)
                files.update(child_files)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            body = _read_regular_at(directory_fd, name)
            manifest[relative] = "sha256:" + hashlib.sha256(body).hexdigest()
            files[relative] = body
        else:
            raise ReleaseIntegrityError(f"artifact contains forbidden non-regular node: {relative}")
    return manifest, files


def _manifest_fd(directory_fd: int) -> dict[str, str]:
    return _read_tree_fd(directory_fd)[0]


def _manifest_digest_fd(directory_fd: int) -> str:
    return stable_digest(_manifest_fd(directory_fd))


def _verified_tree_fd(directory_fd: int, expected_digest: str, label: str) -> dict[str, bytes]:
    manifest, files = _read_tree_fd(directory_fd)
    if stable_digest(manifest) != expected_digest:
        raise ReleaseIntegrityError(f"{label} failed its actual read check")
    return files


def directory_digest(directory: Path) -> str:
    descriptor = _open_absolute_directory(directory, create=False)
    try:
        return _manifest_digest_fd(descriptor)
    finally:
        os.close(descriptor)


def file_digest(path: Path) -> str:
    parent_fd = _open_absolute_directory(path.parent, create=False)
    try:
        return "sha256:" + hashlib.sha256(_read_regular_at(parent_fd, path.name)).hexdigest()
    finally:
        os.close(parent_fd)


def _copy_tree_fd(source_fd: int, destination_fd: int) -> None:
    for name in sorted(os.listdir(source_fd)):
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            os.mkdir(name, 0o700, dir_fd=destination_fd)
            child_source = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            child_destination = os.open(name, _DIRECTORY_FLAGS, dir_fd=destination_fd)
            try:
                _copy_tree_fd(child_source, child_destination)
                os.fsync(child_destination)
            finally:
                os.close(child_destination)
                os.close(child_source)
        elif stat.S_ISREG(metadata.st_mode):
            source_file = os.open(name, _FILE_FLAGS, dir_fd=source_fd)
            opened = os.fstat(source_file)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                os.close(source_file)
                raise ReleaseIntegrityError(f"artifact node changed before copy: {name}")
            if opened.st_nlink != 1:
                os.close(source_file)
                raise ReleaseIntegrityError(f"hard-linked artifact files are forbidden: {name}")
            destination_file = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=destination_fd,
            )
            try:
                before = os.fstat(source_file)
                while True:
                    chunk = os.read(source_file, 1024 * 1024)
                    if not chunk:
                        break
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_file, view)
                        view = view[written:]
                after = os.fstat(source_file)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ReleaseIntegrityError(f"artifact changed while copying: {name}")
                os.fsync(destination_file)
            finally:
                os.close(destination_file)
                os.close(source_file)
        else:
            raise ReleaseIntegrityError(f"artifact contains forbidden non-regular node: {name}")


def _fsync_tree_fd(directory_fd: int) -> None:
    for name in sorted(os.listdir(directory_fd)):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                _fsync_tree_fd(child)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        else:
            raise ReleaseIntegrityError(f"cannot fsync forbidden artifact node: {name}")
    os.fsync(directory_fd)


def _remove_tree_at(parent_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        for entry in os.listdir(child):
            _remove_tree_at(child, entry)
    finally:
        os.close(child)
    os.rmdir(name, dir_fd=parent_fd)


def _rename_noreplace(dir_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise PublishingStoreError("atomic no-replace directory rename is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(dir_fd, os.fsencode(source), dir_fd, os.fsencode(destination), _RENAME_NOREPLACE)
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination)
        raise OSError(error, os.strerror(error), destination)


def _safe_relative_parts(relative: str) -> tuple[str, ...]:
    decoded = unquote(relative)
    path = PurePosixPath(decoded)
    parts = path.parts
    if (
        not relative
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in decoded.split("/"))
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ReleaseIntegrityError(f"unsafe release-relative path: {relative}")
    return parts


def _verify_regular_relative(directory_fd: int, relative: str) -> None:
    parts = _safe_relative_parts(relative)
    current = os.dup(directory_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        descriptor = os.open(parts[-1], _FILE_FLAGS, dir_fd=current)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ReleaseIntegrityError(f"release reference is not a regular file: {relative}")
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ReleaseIntegrityError(f"release is missing referenced file {relative}") from exc
    finally:
        os.close(current)


def event_references(events: tuple[dict[str, Any], ...] | list[dict[str, Any]], public_base_url: str) -> tuple[tuple[str, str], ...]:
    prefix = urlsplit(f"{public_base_url.rstrip('/')}/releases/")
    references: list[tuple[str, str]] = []
    for event in events:
        for field in ("page_url", "machine_url"):
            parsed = urlsplit(event[field])
            if parsed.query or parsed.fragment or (parsed.scheme, parsed.netloc) != (prefix.scheme, prefix.netloc):
                raise ReleaseIntegrityError(f"event {field} is outside its immutable release")
            decoded_path = unquote(parsed.path)
            if not decoded_path.startswith(prefix.path):
                raise ReleaseIntegrityError(f"event {field} is outside its immutable release")
            release_id, separator, relative = decoded_path.removeprefix(prefix.path).partition("/")
            try:
                _validate_release_id(release_id)
            except ValueError as exc:
                raise ReleaseIntegrityError(f"event {field} has an invalid release identity") from exc
            if not separator or not release_id or not relative:
                raise ReleaseIntegrityError(f"event {field} does not identify a release file")
            relative = relative.rstrip("/")
            _safe_relative_parts(relative)
            references.append((release_id, f"{relative}/index.html" if field == "page_url" else relative))
    return tuple(references)


def _validate_record_schema(record: Mapping[str, Any]) -> None:
    errors = sorted(validator_for("release").iter_errors(record), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ReleaseIntegrityError(f"release record fails schema at {location}: {first.message}")


class FileReleaseStore:
    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(os.fspath(root)))
        root_fd = _open_absolute_directory(self.root, create=True)
        candidates_fd = _open_or_create_dir_at(root_fd, "candidates")
        snapshots_fd = _open_or_create_dir_at(candidates_fd, "artifacts")
        releases_fd = _open_or_create_dir_at(root_fd, "releases")
        feed_fd = _open_or_create_dir_at(root_fd, "feed")
        records_fd = _open_or_create_dir_at(feed_fd, "records")
        self._root_fd = root_fd
        self._candidates_fd = candidates_fd
        self._snapshots_fd = snapshots_fd
        self._releases_fd = releases_fd
        self._feed_fd = feed_fd
        self._records_fd = records_fd
        self._finalizer = weakref.finalize(
            self,
            _close_fds,
            (records_fd, feed_fd, releases_fd, snapshots_fd, candidates_fd, root_fd),
        )
        self.candidates = self.root / "candidates"
        self.snapshots = self.candidates / "artifacts"
        self.releases = self.root / "releases"
        self.records = self.root / "feed" / "records"

    def _write_json_atomic_at(self, directory_fd: int, name: str, value: object) -> None:
        temporary = f".{name}.tmp-{secrets.token_hex(12)}"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            body = _canonical_json(value)
            view = memoryview(body)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            raise

    def _read_json_at(self, directory_fd: int, name: str) -> dict[str, Any] | None:
        try:
            body = _read_regular_at(directory_fd, name)
        except ReleaseIntegrityError as exc:
            try:
                os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            raise exc
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseIntegrityError(f"invalid JSON in {name}") from exc
        if not isinstance(value, dict):
            raise ReleaseIntegrityError(f"JSON object required in {name}")
        return value

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        descriptor = os.open(
            ".writer.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._root_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ReleaseIntegrityError("writer lock is not a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PublicationLockedError("another publisher holds the single-writer lock") from exc
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def bind_namespace(self, feed_id: str, public_base_url: str, build_config_identity: str) -> None:
        expected = {
            "schema_version": 1,
            "feed_id": feed_id,
            "public_base_url": public_base_url,
            "build_config_identity": build_config_identity,
        }
        existing = self._read_json_at(self._root_fd, "namespace.json")
        if existing is None:
            self._write_json_atomic_at(self._root_fd, "namespace.json", expected)
        elif existing != expected:
            raise PublishingStoreError("publication store namespace does not match feed, URL, or build configuration")

    def read_namespace(self) -> dict[str, Any] | None:
        return self._read_json_at(self._root_fd, "namespace.json")

    def candidate_path(self, release_id: str) -> Path:
        return self.candidates / f"{_validate_release_id(release_id)}.json"

    def snapshot_path(self, release_id: str) -> Path:
        return self.snapshots / _validate_release_id(release_id)

    def save_candidate(self, candidate: Candidate) -> None:
        _validate_release_id(candidate.release_id)
        _validate_sequence(candidate.sequence)
        if candidate.committed_at is not None and candidate.committed_at != candidate.publication_time:
            raise ReleaseIntegrityError("candidate committed_at must equal publication_time")
        expected_snapshot_path = self.snapshot_path(candidate.release_id)
        if candidate.artifact_snapshot_path != expected_snapshot_path:
            raise ReleaseIntegrityError("candidate snapshot path is outside its store-owned location")
        existing = self.load_candidate(candidate.release_id)
        if existing is not None:
            immutable_fields = (
                "release_id",
                "source_commit",
                "catalog_digest",
                "snapshot_digest",
                "build_config_identity",
                "artifact_snapshot_path",
                "artifact_snapshot_digest",
                "publication_time",
                "sequence",
                "previous_sequence",
                "events",
            )
            if any(getattr(existing, field) != getattr(candidate, field) for field in immutable_fields):
                raise ImmutableReleaseError("candidate immutable preparation fields cannot change")
            if existing.committed_at is not None and candidate.committed_at != existing.committed_at:
                raise ImmutableReleaseError("candidate committed_at cannot change after it is frozen")
            if existing.committed_at is None and candidate.committed_at is not None and candidate.state not in {
                CandidateState.ACTIVATED,
                CandidateState.COMMITTED,
            }:
                raise PublishingStoreError("candidate committed_at can only freeze after activation")
            order = list(CandidateState)
            if order.index(candidate.state) < order.index(existing.state) or order.index(candidate.state) > order.index(existing.state) + 1:
                raise PublishingStoreError("candidate state transition is not monotonic")
        self._write_json_atomic_at(self._candidates_fd, f"{candidate.release_id}.json", candidate.to_dict())

    def load_candidate(self, release_id: str) -> Candidate | None:
        release_id = _validate_release_id(release_id)
        value = self._read_json_at(self._candidates_fd, f"{release_id}.json")
        if value is None:
            return None
        candidate = Candidate.from_dict(value)
        if candidate.release_id != release_id:
            raise ReleaseIntegrityError(f"candidate file identity differs from {release_id}")
        derived_release_id = "release-" + stable_digest(
            {
                "source_commit": candidate.source_commit,
                "catalog_digest": candidate.catalog_digest,
                "snapshot_digest": candidate.snapshot_digest,
                "build_config_identity": candidate.build_config_identity,
            }
        ).removeprefix("sha256:")
        if derived_release_id != release_id:
            raise ReleaseIntegrityError(f"candidate {release_id} is not bound to its release inputs")
        _validate_sequence(candidate.sequence)
        if (candidate.state in {CandidateState.ACTIVATED, CandidateState.COMMITTED}) != (candidate.committed_at is not None):
            raise ReleaseIntegrityError(f"candidate {release_id} has an invalid committed_at lifecycle")
        if candidate.committed_at is not None and candidate.committed_at != candidate.publication_time:
            raise ReleaseIntegrityError(f"candidate {release_id} committed_at is not anchored to publication_time")
        if candidate.artifact_snapshot_path != self.snapshot_path(release_id):
            raise ReleaseIntegrityError(f"candidate {release_id} has an unsafe snapshot path")
        return candidate

    def pending_candidates(self) -> list[Candidate]:
        candidates: list[Candidate] = []
        for name in sorted(os.listdir(self._candidates_fd)):
            if name == "artifacts":
                continue
            if not name.endswith(".json"):
                raise ReleaseIntegrityError(f"unexpected candidate entry {name}")
            release_id = name.removesuffix(".json")
            _validate_release_id(release_id)
            candidate = self.load_candidate(release_id)
            if candidate is not None and candidate.state != CandidateState.COMMITTED:
                candidates.append(candidate)
        if len(candidates) > 1:
            raise PublishingStoreError("more than one pending candidate cannot be advanced safely")
        return candidates

    def validate_pending_chain(self, candidates: list[Candidate]) -> None:
        if not candidates:
            return
        candidate = candidates[0]
        head = self.read_feed_head()
        if head is None:
            valid = candidate.sequence == 1 and candidate.previous_sequence is None
        elif (
            candidate.state == CandidateState.ACTIVATED
            and candidate.sequence == head["sequence"]
            and candidate.release_id == head["release_id"]
        ):
            valid = candidate.previous_sequence == (None if candidate.sequence == 1 else candidate.sequence - 1)
        else:
            valid = candidate.sequence == head["sequence"] + 1 and candidate.previous_sequence == head["sequence"]
        if not valid:
            raise PublishingStoreError("pending candidate does not form the unique next sequence")

    def freeze_artifact(self, release_id: str, source: Path) -> tuple[Path, str]:
        release_id = _validate_release_id(release_id)
        source_fd = _open_absolute_directory(source, create=False)
        try:
            digest = _manifest_digest_fd(source_fd)
            try:
                existing_fd = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._snapshots_fd)
            except FileNotFoundError:
                existing_fd = None
            except OSError as exc:
                raise ImmutableReleaseError(f"candidate snapshot {release_id} is not a safe directory") from exc
            if existing_fd is not None:
                try:
                    if _manifest_digest_fd(existing_fd) != digest:
                        raise ImmutableReleaseError(f"candidate snapshot {release_id} has different bytes")
                finally:
                    os.close(existing_fd)
                return self.snapshot_path(release_id), digest
            staging = f".{release_id}.tmp-{secrets.token_hex(12)}"
            os.mkdir(staging, 0o700, dir_fd=self._snapshots_fd)
            staging_fd = os.open(staging, _DIRECTORY_FLAGS, dir_fd=self._snapshots_fd)
            try:
                _copy_tree_fd(source_fd, staging_fd)
                if _manifest_digest_fd(source_fd) != digest or _manifest_digest_fd(staging_fd) != digest:
                    raise ReleaseIntegrityError("frozen snapshot differs from stable source manifest")
                _fsync_tree_fd(staging_fd)
                try:
                    _rename_noreplace(self._snapshots_fd, staging, release_id)
                except FileExistsError:
                    existing_fd = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._snapshots_fd)
                    try:
                        if _manifest_digest_fd(existing_fd) != digest:
                            raise ImmutableReleaseError(f"candidate snapshot {release_id} won a conflicting race")
                    finally:
                        os.close(existing_fd)
                os.fsync(self._snapshots_fd)
            finally:
                # rename 失败后名字可被并发复用；宁可保留随机 orphan，也不能按名删除后来者。
                if staging_fd >= 0:
                    os.close(staging_fd)
            return self.snapshot_path(release_id), digest
        finally:
            os.close(source_fd)

    @contextmanager
    def snapshot_view(self, release_id: str) -> Iterator[Path]:
        release_id = _validate_release_id(release_id)
        descriptor = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._snapshots_fd)
        try:
            yield Path(f"/proc/self/fd/{descriptor}")
        finally:
            os.close(descriptor)

    def verify_snapshot(self, release_id: str, expected_digest: str) -> None:
        self.read_verified_snapshot(release_id, expected_digest)

    def read_verified_snapshot(self, release_id: str, expected_digest: str) -> dict[str, bytes]:
        descriptor = os.open(_validate_release_id(release_id), _DIRECTORY_FLAGS, dir_fd=self._snapshots_fd)
        try:
            return _verified_tree_fd(descriptor, expected_digest, f"candidate snapshot {release_id}")
        finally:
            os.close(descriptor)

    def _publish_from_fd(self, release_id: str, source_fd: int, source_digest: str) -> str:
        try:
            destination_fd = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._releases_fd)
        except FileNotFoundError:
            destination_fd = None
        except OSError as exc:
            raise ImmutableReleaseError(f"release {release_id} already exists as an unsafe entry") from exc
        if destination_fd is not None:
            try:
                if _manifest_digest_fd(destination_fd) != source_digest:
                    raise ImmutableReleaseError(f"release {release_id} already exists with different bytes")
            finally:
                os.close(destination_fd)
            return source_digest
        staging = f".{release_id}.tmp-{secrets.token_hex(12)}"
        os.mkdir(staging, 0o700, dir_fd=self._releases_fd)
        staging_fd = os.open(staging, _DIRECTORY_FLAGS, dir_fd=self._releases_fd)
        try:
            _copy_tree_fd(source_fd, staging_fd)
            if _manifest_digest_fd(source_fd) != source_digest or _manifest_digest_fd(staging_fd) != source_digest:
                raise ReleaseIntegrityError("uploaded bytes differ from both safe manifests")
            _fsync_tree_fd(staging_fd)
            try:
                _rename_noreplace(self._releases_fd, staging, release_id)
            except FileExistsError as exc:
                destination_fd = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._releases_fd)
                try:
                    if _manifest_digest_fd(destination_fd) != source_digest:
                        raise ImmutableReleaseError(
                            f"release {release_id} was concurrently created with different bytes"
                        ) from exc
                finally:
                    os.close(destination_fd)
            os.fsync(self._releases_fd)
            return source_digest
        finally:
            # 同上：仅关闭仍绑定原 inode 的 fd，不再对可复用 pathname 执行清理。
            if staging_fd >= 0:
                os.close(staging_fd)

    def upload_release(self, release_id: str, source: Path) -> str:
        release_id = _validate_release_id(release_id)
        source_fd = _open_absolute_directory(source, create=False)
        try:
            digest = _manifest_digest_fd(source_fd)
            return self._publish_from_fd(release_id, source_fd, digest)
        finally:
            os.close(source_fd)

    def upload_snapshot(self, release_id: str, expected_digest: str) -> str:
        release_id = _validate_release_id(release_id)
        source_fd = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._snapshots_fd)
        try:
            if _manifest_digest_fd(source_fd) != expected_digest:
                raise ReleaseIntegrityError(f"candidate snapshot {release_id} changed after preparation")
            return self._publish_from_fd(release_id, source_fd, expected_digest)
        finally:
            os.close(source_fd)

    def release_path(self, release_id: str) -> Path:
        return self.releases / _validate_release_id(release_id)

    @contextmanager
    def release_view(self, release_id: str) -> Iterator[Path]:
        descriptor = os.open(_validate_release_id(release_id), _DIRECTORY_FLAGS, dir_fd=self._releases_fd)
        try:
            yield Path(f"/proc/self/fd/{descriptor}")
        finally:
            os.close(descriptor)

    def verify_release(self, release_id: str, expected_digest: str) -> None:
        self.read_verified_release(release_id, expected_digest)

    def read_verified_release(self, release_id: str, expected_digest: str) -> dict[str, bytes]:
        release_id = _validate_release_id(release_id)
        try:
            descriptor = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._releases_fd)
        except OSError as exc:
            raise ReleaseIntegrityError(f"release {release_id} is unavailable or unsafe") from exc
        try:
            return _verified_tree_fd(descriptor, expected_digest, f"release {release_id}")
        finally:
            os.close(descriptor)

    def verify_references(self, references: tuple[tuple[str, str], ...]) -> None:
        for release_id, relative in references:
            release_id = _validate_release_id(release_id)
            try:
                descriptor = os.open(release_id, _DIRECTORY_FLAGS, dir_fd=self._releases_fd)
            except OSError as exc:
                raise ReleaseIntegrityError(f"referenced release {release_id} is unavailable") from exc
            try:
                _verify_regular_relative(descriptor, relative)
            finally:
                os.close(descriptor)

    def read_current(self) -> dict[str, Any] | None:
        return self._read_json_at(self._root_fd, "current.json")

    def activate(self, release_id: str, sequence: int) -> None:
        release_id = _validate_release_id(release_id)
        sequence = _validate_sequence(sequence)
        head = self.read_feed_head()
        if head is not None:
            if head["sequence"] > sequence or (
                head["sequence"] == sequence and head["release_id"] != release_id
            ):
                raise StaleReleaseError("an older or conflicting release cannot replace the current entrypoint")
        current = self.read_current()
        if current is not None and current.get("sequence") == sequence and current.get("release_id") != release_id:
            raise StaleReleaseError("the current entrypoint already binds this sequence to another release")
        self._write_json_atomic_at(
            self._root_fd,
            "current.json",
            {"schema_version": 1, "release_id": release_id, "sequence": sequence},
        )

    def rollback(self, release_id: str) -> None:
        release_id = _validate_release_id(release_id)
        candidate = self.load_candidate(release_id)
        if candidate is None or candidate.state != CandidateState.COMMITTED:
            raise PublishingStoreError("rollback requires a committed release")
        self.verify_release(release_id, candidate.artifact_snapshot_digest)
        self._write_json_atomic_at(
            self._root_fd,
            "current.json",
            {"schema_version": 1, "release_id": release_id, "sequence": candidate.sequence, "rollback": True},
        )

    def record_path(self, sequence: int) -> Path:
        return self.records / f"{_validate_sequence(sequence):020d}.json"

    def load_record(self, sequence: int) -> dict[str, Any] | None:
        sequence = _validate_sequence(sequence)
        return self._read_json_at(self._records_fd, f"{sequence:020d}.json")

    def append_record(self, record: dict[str, Any]) -> None:
        sequence = _validate_sequence(record.get("sequence"))
        _validate_release_id(record.get("release_id"))
        name = f"{sequence:020d}.json"
        existing = self._read_json_at(self._records_fd, name)
        if existing is not None:
            if existing != record:
                raise ImmutableReleaseError(f"feed sequence {sequence} is immutable")
            return
        self._write_json_atomic_at(self._records_fd, name, record)

    def read_feed_head(self) -> dict[str, Any] | None:
        head = self._read_json_at(self._feed_fd, "head.json")
        if head is None:
            return None
        if set(head) != {"schema_version", "sequence", "release_id"} or head.get("schema_version") != 1:
            raise ReleaseIntegrityError("feed head has an invalid schema")
        _validate_sequence(head.get("sequence"))
        _validate_release_id(head.get("release_id"))
        return head

    def publish_feed_head(self, sequence: int, release_id: str) -> None:
        sequence = _validate_sequence(sequence)
        release_id = _validate_release_id(release_id)
        previous = self.read_feed_head()
        expected = 1 if previous is None else previous["sequence"] + 1
        if sequence < expected:
            if previous is not None and previous["sequence"] == sequence and previous["release_id"] != release_id:
                raise StaleReleaseError("feed sequence is already bound to another release")
            return
        if sequence != expected or self.load_record(sequence) is None:
            raise PublishingStoreError("feed head must advance by one existing immutable record")
        self._write_json_atomic_at(
            self._feed_fd,
            "head.json",
            {"schema_version": 1, "sequence": sequence, "release_id": release_id},
        )

    def read_feed(self) -> list[dict[str, Any]]:
        head = self.read_feed_head()
        if head is None:
            return []
        namespace = self.read_namespace()
        if namespace is None:
            raise ReleaseIntegrityError("committed feed has no namespace binding")
        records: list[dict[str, Any]] = []
        for sequence in range(1, head["sequence"] + 1):
            record = self.load_record(sequence)
            if record is None:
                raise ReleaseIntegrityError(f"feed history is missing sequence {sequence}")
            _validate_record_schema(record)
            expected_previous = None if sequence == 1 else sequence - 1
            if record["sequence"] != sequence or record.get("previous_sequence") != expected_previous:
                raise ReleaseIntegrityError(f"feed record {sequence} breaks the sequence chain")
            candidate = self.load_candidate(record["release_id"])
            if candidate is None or candidate.state not in {CandidateState.ACTIVATED, CandidateState.COMMITTED}:
                raise ReleaseIntegrityError(f"feed record {sequence} lacks its durable candidate")
            expected = {
                "schema_version": 1,
                "release_id": candidate.release_id,
                "sequence": candidate.sequence,
                "source_commit": candidate.source_commit,
                "publication_time": candidate.publication_time,
                "committed_at": candidate.committed_at,
                "previous_sequence": candidate.previous_sequence,
                "events": list(candidate.events),
            }
            if {key: record.get(key) for key in expected} != expected:
                raise ReleaseIntegrityError(f"feed record {sequence} differs from its immutable candidate")
            if any(event["feed_id"] != namespace["feed_id"] for event in record["events"]):
                raise ReleaseIntegrityError(f"feed record {sequence} differs from its namespace identity")
            if candidate.build_config_identity != namespace["build_config_identity"]:
                raise ReleaseIntegrityError(f"candidate {candidate.release_id} differs from its store namespace")
            documents = self.read_verified_release(candidate.release_id, candidate.artifact_snapshot_digest)
            for reference_release, relative in event_references(candidate.events, namespace["public_base_url"]):
                if reference_release != candidate.release_id or relative not in documents:
                    raise ReleaseIntegrityError(
                        f"candidate {candidate.release_id} has a reference outside its verified release"
                    )
            records.append(record)
        if records[-1]["release_id"] != head["release_id"]:
            raise ReleaseIntegrityError("feed head does not match the last immutable record")
        return records
