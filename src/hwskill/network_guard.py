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
_BPF_JMP_JSET_K = 0x45
_BPF_RET_K = 0x06
_SECCOMP_DATA_NR_OFFSET = 0
_SECCOMP_DATA_ARCH_OFFSET = 4
_SECCOMP_DATA_ARG0_OFFSET = 16
_AF_UNIX = 1
_AUDIT_ARCH_X86_64 = 0xC000003E
_AUDIT_ARCH_AARCH64 = 0xC00000B7
_AUDIT_ARCH_I386 = 0x40000003
_X32_SYSCALL_BIT = 0x40000000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000
_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
_LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
_LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
_LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
_LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
_LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
_LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
_LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
_LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
_LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
_LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
_LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
_LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
_LANDLOCK_ACCESS_FS_REFER = 1 << 13
_LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
_LANDLOCK_READ = _LANDLOCK_ACCESS_FS_EXECUTE | _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_READ_DIR
_LANDLOCK_WRITE_V1 = sum(1 << bit for bit in range(1, 13))
GUARD_FAILURE_EXIT = 125
GUARD_FAILURE_MARKER = "hwskill isolation guard unavailable"


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def install_network_guard() -> None:
    """Deny internet socket creation while retaining AF_UNIX local process IPC."""
    instructions = _network_guard_instructions(platform.machine().lower())
    program_array = (_SockFilter * len(instructions))(*instructions)
    program = _SockFprog(len(instructions), program_array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _network_guard_instructions(machine: str) -> list[_SockFilter]:
    architecture = {
        "x86_64": (_AUDIT_ARCH_X86_64, (41, 53), (425, 426, 427), True),
        "amd64": (_AUDIT_ARCH_X86_64, (41, 53), (425, 426, 427), True),
        "aarch64": (_AUDIT_ARCH_AARCH64, (198, 199), (425, 426, 427), False),
        "arm64": (_AUDIT_ARCH_AARCH64, (198, 199), (425, 426, 427), False),
        # socketcall's family argument lives behind a userspace pointer, which
        # classic BPF cannot inspect safely.  Keep its conservative deny rule.
        "i386": (_AUDIT_ARCH_I386, (), (102, 425, 426, 427), False),
        "i686": (_AUDIT_ARCH_I386, (), (102, 425, 426, 427), False),
    }.get(machine)
    if architecture is None:
        raise OSError(errno.ENOTSUP, "network guard does not support this architecture")
    audit_arch, local_ipc_syscalls, denied_syscalls, reject_x32 = architecture
    instructions = [
        _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARCH_OFFSET),
        _SockFilter(_BPF_JMP_JEQ_K, 1, 0, audit_arch),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
        _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_NR_OFFSET),
    ]
    if reject_x32:
        instructions.extend((
            _SockFilter(_BPF_JMP_JSET_K, 0, 1, _X32_SYSCALL_BIT),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
        ))
    for number in local_ipc_syscalls:
        instructions.extend((
            # On a mismatch, skip this syscall's argument check and both returns.
            _SockFilter(_BPF_JMP_JEQ_K, 0, 4, number),
            _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARG0_OFFSET),
            _SockFilter(_BPF_JMP_JEQ_K, 0, 1, _AF_UNIX),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
        ))
    for number in denied_syscalls:
        instructions.extend((
            _SockFilter(_BPF_JMP_JEQ_K, 0, 1, number),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
        ))
    instructions.append(_SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW))
    return instructions


def install_filesystem_guard(read_only: tuple[str, ...], read_write: tuple[str, ...]) -> None:
    """Allow only runtime files plus declared test mounts; notably exclude /proc and /credentials."""
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    abi = syscall(_LANDLOCK_CREATE_RULESET, 0, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        code = ctypes.get_errno() or errno.ENOTSUP
        raise OSError(code, "Landlock filesystem isolation is unavailable")
    handled = _LANDLOCK_READ | _LANDLOCK_WRITE_V1
    if abi >= 2:
        handled |= _LANDLOCK_ACCESS_FS_REFER
    if abi >= 3:
        handled |= _LANDLOCK_ACCESS_FS_TRUNCATE
    ruleset_attr = _LandlockRulesetAttr(handled)
    ruleset_fd = syscall(_LANDLOCK_CREATE_RULESET, ctypes.byref(ruleset_attr), ctypes.sizeof(ruleset_attr), 0)
    if ruleset_fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    try:
        system_read_only = tuple(dict.fromkeys(
            os.path.realpath(path) for path in (
                "/bin", "/usr", "/lib", "/lib64", "/etc", sys.prefix, sys.exec_prefix,
                os.path.dirname(sys.executable),
            )
            if os.path.exists(path)
        ))
        system_read_write = tuple(dict.fromkeys(
            os.path.realpath(path) for path in ("/dev", "/tmp") if os.path.exists(path)
        ))
        for path, writable in tuple((path, False) for path in (*system_read_only, *read_only)) + tuple(
            (path, True) for path in (*system_read_write, *read_write)
        ):
            descriptor = os.open(path, getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            try:
                allowed = handled if writable else _LANDLOCK_READ
                attribute = _LandlockPathBeneathAttr(allowed, descriptor)
                if syscall(
                    _LANDLOCK_ADD_RULE, ruleset_fd, _LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.byref(attribute), 0,
                ) != 0:
                    code = ctypes.get_errno()
                    raise OSError(code, os.strerror(code))
            finally:
                os.close(descriptor)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        if syscall(_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    finally:
        os.close(ruleset_fd)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    read_only: list[str] = []
    read_write: list[str] = []
    allow_network = False
    network_only = False
    while arguments and arguments[0] != "--":
        option = arguments.pop(0)
        if option == "--allow-network":
            allow_network = True
            continue
        if option == "--network-only":
            network_only = True
            continue
        if option not in {"--read-only", "--read-write"} or not arguments:
            return 2
        (read_only if option == "--read-only" else read_write).append(arguments.pop(0))
    if not arguments or arguments.pop(0) != "--" or not arguments:
        return 2
    if network_only and (allow_network or read_only or read_write):
        return 2
    try:
        if not network_only:
            install_filesystem_guard(tuple(read_only), tuple(read_write))
        if network_only or not allow_network:
            install_network_guard()
        os.execvp(arguments[0], arguments)
    except OSError:
        print(GUARD_FAILURE_MARKER, file=sys.stderr)
        return GUARD_FAILURE_EXIT
    return GUARD_FAILURE_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
