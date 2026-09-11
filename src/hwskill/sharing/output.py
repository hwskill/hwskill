from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
from typing import Mapping, Any


class OutputDurabilityUncertainError(OSError):
    code = "output-durability-uncertain"


class OutputNamespaceUncertainError(OSError):
    code = "output-namespace-uncertain"


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _open_directory_chain(path: Path, *, create: bool) -> tuple[list[int], tuple[str, ...]]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptors = [os.open("/", flags)]
    names = tuple(absolute.parts[1:])
    try:
        for name in names:
            if create:
                try:
                    os.mkdir(name, 0o755, dir_fd=descriptors[-1])
                    os.fsync(descriptors[-1])
                except FileExistsError:
                    pass
            descriptors.append(os.open(name, flags, dir_fd=descriptors[-1]))
        return descriptors, names
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _verify_directory_chain(descriptors: list[int], names: tuple[str, ...]) -> None:
    if len(descriptors) != len(names) + 1:
        raise OSError("输出父目录 fd 链损坏")
    if not stat.S_ISDIR(os.fstat(descriptors[0]).st_mode):
        raise OSError("输出根目录 fd 无效")
    for index, name in enumerate(names):
        opened = os.fstat(descriptors[index + 1])
        try:
            named = os.stat(name, dir_fd=descriptors[index], follow_symlinks=False)
        except OSError as exc:
            raise OSError("输出父目录命名空间消失或被替换") from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or _identity(opened) != _identity(named)
        ):
            raise OSError("输出父目录命名空间被替换")


def _verify_published_target(directory: int, name: str, expected: tuple[int, int]) -> None:
    try:
        published = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError as exc:
        raise OSError("已发布输出名称消失或被替换") from exc
    if not stat.S_ISREG(published.st_mode) or _identity(published) != expected:
        raise OSError("已发布输出名称未绑定写入的临时 inode")


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    if not target.name:
        raise ValueError("输出路径必须指向文件")
    temporary_name: str | None = None
    temporary_descriptor: int | None = None
    directory_chain: list[int] = []
    try:
        directory_chain, directory_names = _open_directory_chain(target.parent, create=True)
        directory = directory_chain[-1]
        _verify_directory_chain(directory_chain, directory_names)
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        for _attempt in range(128):
            temporary_name = f".{target.name}.{secrets.token_hex(16)}.tmp"
            try:
                temporary_descriptor = os.open(
                    temporary_name,
                    file_flags,
                    0o600,
                    dir_fd=directory,
                )
                break
            except FileExistsError:
                continue
        else:
            raise OSError("无法分配输出临时名称")
        temporary_identity = _identity(os.fstat(temporary_descriptor))
        with os.fdopen(os.dup(temporary_descriptor), mode="w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _verify_directory_chain(directory_chain, directory_names)
        try:
            named_temporary = os.stat(temporary_name, dir_fd=directory, follow_symlinks=False)
        except OSError as exc:
            raise OSError("输出临时名称在原子替换前消失或被替换") from exc
        if not stat.S_ISREG(named_temporary.st_mode) or _identity(named_temporary) != temporary_identity:
            raise OSError("输出临时名称在原子替换前被替换")
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        temporary_name = None
        try:
            _verify_directory_chain(directory_chain, directory_names)
            _verify_published_target(directory, target.name, temporary_identity)
        except OSError as exc:
            raise OutputNamespaceUncertainError(
                "输出已在持有的目录 inode 中替换，但配置父目录命名空间随后变化，结果不确定"
            ) from exc
        try:
            os.fsync(directory)
        except OSError as exc:
            raise OutputDurabilityUncertainError(
                "输出已原子替换，但父目录 fsync 失败，结果耐久性不确定；可用同一 pending batch 幂等重试"
            ) from exc
        try:
            _verify_directory_chain(directory_chain, directory_names)
            _verify_published_target(directory, target.name, temporary_identity)
        except OSError as exc:
            raise OutputNamespaceUncertainError(
                "输出已替换并 fsync，但配置父目录或目标名称随后变化，结果不确定"
            ) from exc
    finally:
        if temporary_name is not None:
            # POSIX 没有“仅当名称仍指向此 fd inode 时 unlink”的原子操作。失败时保留
            # mkstemp 创建的 0600 高熵 orphan，避免 stat→unlink 竞态删除后来者。
            pass
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        for descriptor in reversed(directory_chain):
            os.close(descriptor)
