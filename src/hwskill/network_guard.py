"""Irreversibly deny socket creation before executing one command action."""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import sys


_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_RET_K = 0x06
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def install_network_guard() -> None:
    """Apply a process-tree seccomp filter that denies socket and socketpair."""
    machine = platform.machine().lower()
    syscalls = {
        "x86_64": (41, 53, 425, 426, 427),
        "amd64": (41, 53, 425, 426, 427),
        "aarch64": (198, 199, 425, 426, 427),
        "arm64": (198, 199, 425, 426, 427),
        "i386": (102, 425, 426, 427),
        "i686": (102, 425, 426, 427),
    }.get(machine)
    if syscalls is None:
        raise OSError(errno.ENOTSUP, "network guard does not support this architecture")
    instructions = [_SockFilter(_BPF_LD_W_ABS, 0, 0, 0)]
    for number in syscalls:
        instructions.extend((
            _SockFilter(_BPF_JMP_JEQ_K, 0, 1, number),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
        ))
    instructions.append(_SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW))
    program_array = (_SockFilter * len(instructions))(*instructions)
    program = _SockFprog(len(instructions), program_array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "--" or len(arguments) == 1:
        return 2
    try:
        install_network_guard()
        os.execvp(arguments[1], arguments[1:])
    except OSError:
        return 3
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
