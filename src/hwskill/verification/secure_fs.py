from __future__ import annotations

import errno
import os
from pathlib import Path
import stat


class UnsafePathError(ValueError):
    pass


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def absolute_parts(path: Path) -> tuple[str, ...]:
    absolute = path.absolute()
    parts = absolute.parts
    if not parts or parts[0] != "/" or any(part in {"", ".", ".."} for part in parts[1:]):
        raise UnsafePathError("path must be absolute and contain no dot segments")
    return parts[1:]


def open_directory_chain(path: Path, *, create: bool, mode: int = 0o700) -> int:
    """Open a directory without following any ancestor symlink."""
    current = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in absolute_parts(path):
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except OSError as error:
                if error.errno not in {errno.ELOOP, errno.ENOTDIR}:
                    raise
                raise UnsafePathError(f"directory path contains a symlink or non-directory component: {path}") from error
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def open_child_directory(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        if error.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise UnsafePathError(f"directory entry is a symlink or non-directory: {name}") from error


def read_regular_file_at(parent_fd: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise UnsafePathError(f"file entry is a symlink: {name}") from error
        raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePathError(f"file entry is not regular: {name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def unlink_owned_tree(parent_fd: int, name: str, expected: os.stat_result) -> None:
    """Remove only the exact unpublished staging directory we created."""
    try:
        descriptor = open_child_directory(parent_fd, name)
    except FileNotFoundError:
        return
    try:
        actual = os.fstat(descriptor)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            return
        for child_name in os.listdir(descriptor):
            child = os.stat(child_name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(child.st_mode):
                unlink_owned_tree(descriptor, child_name, child)
            else:
                os.unlink(child_name, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
        os.rmdir(name, dir_fd=parent_fd)
