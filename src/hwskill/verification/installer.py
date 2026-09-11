from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
from typing import Any, Mapping

from .models import install_digest
from .secure_fs import UnsafePathError, open_child_directory, open_directory_chain, unlink_owned_tree


class InstallInputError(ValueError):
    pass


class InstallBlocked(InstallInputError):
    pass


class AcquisitionRequired(InstallInputError):
    pass


class InstallConflict(InstallInputError):
    pass


class InstallationError(InstallInputError):
    pass


@dataclass(frozen=True)
class InstallationResult:
    result: str
    target: Path
    idempotent: bool
    reason: str


def _skill_parts(skill_id: str) -> tuple[str, ...]:
    path = PurePosixPath(skill_id)
    if not skill_id or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InstallInputError("skill_id is not a safe relative directory")
    return path.parts


def _validate_material(material: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        skill_id = material["skill_id"]
        entry_digest = material["entry_digest"]
        expected_install_digest = material["install_digest"]
        identity = material["source_identity"]
        install_json = material["install_json"]
    except KeyError as error:
        raise InstallInputError(f"missing bound install material field: {error.args[0]}") from error
    if material.get("lifecycle") == "withdrawn":
        raise InstallBlocked("withdrawn skills cannot be newly installed")
    if material.get("install_capability") != "installable":
        raise InstallBlocked("catalog does not permit directory installation")
    if not isinstance(skill_id, str) or not isinstance(entry_digest, str) or not isinstance(expected_install_digest, str):
        raise InstallInputError("skill_id and digests must be strings")
    if not isinstance(identity, Mapping) or identity.get("kind") not in {"hosted", "external"}:
        raise InstallInputError("source_identity kind must be hosted or external")
    if not isinstance(identity.get("identity"), str) or not identity["identity"]:
        raise InstallInputError("source_identity identity is required")
    if not isinstance(identity.get("resolved_revision"), str) or not identity["resolved_revision"]:
        raise InstallInputError("source_identity must have a fixed resolved_revision")
    if not isinstance(install_json, Mapping):
        raise InstallInputError("install_json must be an object")
    if install_json.get("skill_id") != skill_id or install_json.get("entry_digest") != entry_digest:
        raise InstallInputError("install_json is not bound to skill_id and entry_digest")
    if install_json.get("install", {}).get("method") != "directory":
        raise InstallInputError("directory installer only accepts method=directory")
    if install_digest(install_json) != expected_install_digest:
        raise InstallInputError("install_digest does not match canonical install JSON")
    return skill_id, dict(identity)


def _scan_tree(directory_fd: int, prefix: str = "") -> tuple[list[str], list[tuple[str, bytes]]]:
    directories: list[str] = []
    files: list[tuple[str, bytes]] = []
    for name in sorted(os.listdir(directory_fd)):
        relative = f"{prefix}/{name}" if prefix else name
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise InstallInputError(f"directory contains symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child_fd = open_child_directory(directory_fd, name)
            except (UnsafePathError, OSError) as error:
                raise InstallInputError(f"directory changed while being reviewed: {relative}") from error
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise InstallInputError(f"directory changed while being reviewed: {relative}")
                directories.append(relative)
                child_directories, child_files = _scan_tree(child_fd, relative)
                directories.extend(child_directories)
                files.extend(child_files)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(metadata.st_mode):
            try:
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
            except OSError as error:
                raise InstallInputError(f"file changed while being reviewed: {relative}") from error
            try:
                opened = os.fstat(file_fd)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino) or not stat.S_ISREG(opened.st_mode):
                    raise InstallInputError(f"file changed while being reviewed: {relative}")
                chunks: list[bytes] = []
                while chunk := os.read(file_fd, 1024 * 1024):
                    chunks.append(chunk)
                files.append((relative, b"".join(chunks)))
            finally:
                os.close(file_fd)
        else:
            raise InstallInputError(f"directory contains non-regular file: {relative}")
    return directories, files


def _tree_digest(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _copy_tree(source_fd: int, target_fd: int) -> None:
    for name in sorted(os.listdir(source_fd)):
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            try:
                source_child = open_child_directory(source_fd, name)
            except (UnsafePathError, OSError) as error:
                raise InstallInputError(f"source changed during copy: {name}") from error
            try:
                os.mkdir(name, 0o700, dir_fd=target_fd)
                target_child = open_child_directory(target_fd, name)
                try:
                    opened = os.fstat(source_child)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise InstallationError(f"source changed during copy: {name}")
                    _copy_tree(source_child, target_child)
                    os.fchmod(target_child, mode)
                    os.fsync(target_child)
                finally:
                    os.close(target_child)
            finally:
                os.close(source_child)
        elif stat.S_ISREG(metadata.st_mode):
            source_file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_fd)
            try:
                target_file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=target_fd)
            except Exception:
                os.close(source_file)
                raise
            try:
                opened = os.fstat(source_file)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino) or not stat.S_ISREG(opened.st_mode):
                    raise InstallationError(f"source changed during copy: {name}")
                while chunk := os.read(source_file, 1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(target_file, view):]
                os.fsync(target_file)
            finally:
                os.close(target_file)
                os.close(source_file)
        else:
            raise InstallInputError(f"source contains symlink or non-regular file: {name}")


def _rename_noreplace(source_parent_fd: int, source_name: str, target_parent_fd: int, target_name: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise InstallationError("atomic no-replace directory publication is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(source_parent_fd, os.fsencode(source_name), target_parent_fd, os.fsencode(target_name), 1) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise InstallConflict(f"concurrent or existing skill directory: {target_name}")
    raise InstallationError(f"atomic no-replace publication failed: {os.strerror(error_number)}")


def _open_source(path: Path) -> int:
    try:
        return open_directory_chain(path, create=False)
    except (FileNotFoundError, UnsafePathError) as error:
        raise InstallInputError("source must be a real directory with no symlink ancestors") from error


def reviewed_directory_digest(path: Path) -> str:
    """Digest regular files from a directory reached without symlink traversal."""
    descriptor = _open_source(path)
    try:
        _, files = _scan_tree(descriptor)
        return _tree_digest(files)
    finally:
        os.close(descriptor)


def install_reviewed_directory(material: Mapping[str, Any], acquired_directory: Path, target_root: Path) -> InstallationResult:
    """Atomically publish an already acquired directory without executing it."""
    skill_id, identity = _validate_material(material)
    parts = _skill_parts(skill_id)
    source_path = acquired_directory.absolute()
    destination_path = target_root.absolute().joinpath(*parts)
    if source_path == destination_path or source_path in destination_path.parents or destination_path in source_path.parents:
        raise InstallInputError("source and installation destination must not overlap")
    try:
        source_fd = _open_source(acquired_directory)
    except InstallInputError as error:
        if identity["kind"] == "external" and not acquired_directory.exists():
            raise AcquisitionRequired("external source must be acquired before installation") from error
        if identity["kind"] == "hosted" and not acquired_directory.exists():
            raise InstallationError("hosted source directory is missing") from error
        raise
    try:
        source_directories, source_files = _scan_tree(source_fd)
        if not any(relative == "SKILL.md" for relative, _ in source_files):
            raise InstallationError("acquired directory is incomplete: SKILL.md is missing")
        expected_content = identity.get("content_digest")
        if identity["kind"] == "hosted" and not isinstance(expected_content, str):
            raise InstallInputError("hosted source must have a content_digest")
        if isinstance(expected_content, str) and _tree_digest(source_files) != expected_content:
            raise InstallInputError("acquired directory does not match source_identity content_digest")

        parent_fd: int | None = None
        try:
            parent_fd = open_directory_chain(target_root, create=True)
            for component in parts[:-1]:
                created = False
                try:
                    os.mkdir(component, 0o700, dir_fd=parent_fd)
                    created = True
                except FileExistsError:
                    pass
                if created:
                    os.fsync(parent_fd)
                child = open_child_directory(parent_fd, component)
                os.close(parent_fd)
                parent_fd = child
        except (UnsafePathError, NotADirectoryError, OSError) as error:
            if parent_fd is not None:
                os.close(parent_fd)
            raise InstallInputError("target namespace contains a symlink or non-directory") from error

        destination_name = parts[-1]
        try:
            try:
                existing_metadata = os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing_metadata = None
            if existing_metadata is not None:
                if not stat.S_ISDIR(existing_metadata.st_mode) or stat.S_ISLNK(existing_metadata.st_mode):
                    raise InstallConflict(f"refusing to follow or overwrite existing skill entry: {target_root / skill_id}")
                try:
                    existing_fd = open_child_directory(parent_fd, destination_name)
                except UnsafePathError as error:
                    raise InstallConflict(f"refusing to follow existing skill entry: {target_root / skill_id}") from error
                try:
                    opened = os.fstat(existing_fd)
                    if (opened.st_dev, opened.st_ino) != (existing_metadata.st_dev, existing_metadata.st_ino):
                        raise InstallConflict("existing skill directory changed during comparison")
                    existing_directories, existing_files = _scan_tree(existing_fd)
                except (InstallInputError, UnsafePathError, OSError) as error:
                    raise InstallConflict("existing skill directory is unsafe") from error
                finally:
                    os.close(existing_fd)
                if source_directories == existing_directories and source_files == existing_files:
                    try:
                        current = os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
                    except OSError as error:
                        raise InstallConflict("existing skill directory changed during comparison") from error
                    if (current.st_dev, current.st_ino) != (existing_metadata.st_dev, existing_metadata.st_ino):
                        raise InstallConflict("existing skill directory changed during comparison")
                    return InstallationResult("pass", target_root.absolute().joinpath(*parts), True, "identical reviewed directory already installed")
                raise InstallConflict(f"refusing to overwrite existing skill directory: {target_root / skill_id}")

            stage_name = f".{destination_name}.install-{secrets.token_hex(16)}"
            os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
            stage_metadata = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
            try:
                stage_fd = open_child_directory(parent_fd, stage_name)
            except Exception:
                unlink_owned_tree(parent_fd, stage_name, stage_metadata)
                raise
            published = False
            try:
                _copy_tree(source_fd, stage_fd)
                os.fsync(stage_fd)
                staged_directories, staged_files = _scan_tree(stage_fd)
                if staged_directories != source_directories or staged_files != source_files:
                    raise InstallationError("staged directory changed or is incomplete")
                if isinstance(expected_content, str) and _tree_digest(staged_files) != expected_content:
                    raise InstallationError("staged directory does not match reviewed content digest")
                _rename_noreplace(parent_fd, stage_name, parent_fd, destination_name)
                published = True
                os.fsync(parent_fd)
            finally:
                os.close(stage_fd)
                if not published:
                    unlink_owned_tree(parent_fd, stage_name, stage_metadata)
            return InstallationResult("pass", target_root.absolute().joinpath(*parts), False, "atomically published reviewed directory without execution")
        finally:
            os.close(parent_fd)
    finally:
        os.close(source_fd)
