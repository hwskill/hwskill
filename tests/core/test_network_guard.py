from __future__ import annotations

import errno
from io import StringIO
import unittest
from unittest.mock import patch


class NetworkGuardFilterTest(unittest.TestCase):
    def test_network_only_mode_installs_seccomp_without_filesystem_guard(self) -> None:
        from hwskill import network_guard

        error = StringIO()
        with patch.object(network_guard, "install_filesystem_guard") as filesystem, patch.object(
            network_guard, "install_network_guard"
        ) as network, patch.object(network_guard.os, "execvp", side_effect=OSError("exec blocked")), patch(
            "sys.stderr", error,
        ):
            code = network_guard.main(("--network-only", "--", "true"))

        self.assertEqual(code, network_guard.GUARD_FAILURE_EXIT)
        filesystem.assert_not_called()
        network.assert_called_once_with()
        self.assertIn(network_guard.GUARD_FAILURE_MARKER, error.getvalue())

    def test_filter_rejects_an_audit_architecture_mismatch_before_syscall_dispatch(self) -> None:
        from hwskill.network_guard import (
            _AUDIT_ARCH_X86_64,
            _BPF_JMP_JEQ_K,
            _BPF_JMP_JSET_K,
            _BPF_LD_W_ABS,
            _BPF_RET_K,
            _X32_SYSCALL_BIT,
            _SECCOMP_DATA_ARCH_OFFSET,
            _SECCOMP_DATA_NR_OFFSET,
            _SECCOMP_RET_ERRNO,
            _network_guard_instructions,
        )

        instructions = _network_guard_instructions("x86_64")

        self.assertEqual((instructions[0].code, instructions[0].k), (_BPF_LD_W_ABS, _SECCOMP_DATA_ARCH_OFFSET))
        self.assertEqual(
            (instructions[1].code, instructions[1].jt, instructions[1].jf, instructions[1].k),
            (_BPF_JMP_JEQ_K, 1, 0, _AUDIT_ARCH_X86_64),
        )
        self.assertEqual(
            (instructions[2].code, instructions[2].k),
            (_BPF_RET_K, _SECCOMP_RET_ERRNO | errno.EPERM),
        )
        self.assertEqual((instructions[3].code, instructions[3].k), (_BPF_LD_W_ABS, _SECCOMP_DATA_NR_OFFSET))
        self.assertEqual(
            (instructions[4].code, instructions[4].jt, instructions[4].jf, instructions[4].k),
            (_BPF_JMP_JSET_K, 0, 1, _X32_SYSCALL_BIT),
        )
        self.assertEqual(
            (instructions[5].code, instructions[5].k),
            (_BPF_RET_K, _SECCOMP_RET_ERRNO | errno.EPERM),
        )
