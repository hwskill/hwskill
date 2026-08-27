#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


from gitcode_patch import PatchFormatError, render_git_patch


SCRIPT_PATH = pathlib.Path(__file__).with_name("fetch_gitcode_pr_patch.py")
SPEC = importlib.util.spec_from_file_location("fetch_gitcode_pr_patch", SCRIPT_PATH)
PATCH_CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_CLI)
REAL_GITCODE_API = PATCH_CLI.GitCodeApi


class FakeApi:
    def __init__(self, pull_request, files):
        self.pull_request = pull_request
        self.files = files
        self.calls = []
        self.per_page = 100

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/files"):
            return self.files, {"total_page": "1"}
        return self.pull_request, {}

    def get_paginated(self, path, params=None):
        return REAL_GITCODE_API.get_paginated(self, path, params)


def modified_file(path="notes.txt"):
    return {
        "patch": {
            "diff": "@@ -1 +1 @@\n-old\n+new\n",
            "old_path": path,
            "new_path": path,
            "a_mode": "100644",
            "b_mode": "100644",
            "new_file": False,
            "deleted_file": False,
            "renamed_file": False,
            "too_large": False,
            "added_lines": 1,
            "removed_lines": 1,
        }
    }


def pr_446_new_file(path="cpp/runtime/state/restore/NewState.h"):
    """Return the new-file sentinel shape observed in GitCode PR 446."""
    return {
        "patch": {
            "diff": "@@ -0,0 +1 @@\n+new state\n",
            "old_path": path,
            "new_path": path,
            "a_mode": "0",
            "b_mode": "100644",
            "new_file": True,
            "deleted_file": False,
            "renamed_file": False,
            "too_large": False,
            "added_lines": 1,
            "removed_lines": 0,
        }
    }


class PatchCliTest(unittest.TestCase):
    def test_help_describes_empty_patch_no_op_contract(self):
        help_text = PATCH_CLI.build_parser().format_help()

        self.assertIn("comparison preimage", help_text)
        self.assertIn("api_base", help_text)
        self.assertNotIn("against the pull request base SHA", help_text)
        self.assertIn("zero-byte", help_text)
        self.assertIn("git apply --allow-empty", help_text)

    def assert_only_metadata_and_files(self, api):
        self.assertEqual(
            [path for path, _ in api.calls],
            [
                "/repos/openeuler/OmniStream/pulls/446",
                "/repos/openeuler/OmniStream/pulls/446/files",
            ],
        )
        self.assertFalse(
            any(
                path.endswith("/comments") or "/pulls/comments/" in path
                for path, _ in api.calls
            )
        )

    def test_main_fetches_only_metadata_and_files_and_separates_diagnostics(self):
        api = FakeApi(
            {
                "state": " MERGED ",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha"},
            },
            [modified_file()],
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch.object(PATCH_CLI, "GitCodeApi", return_value=api):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = PATCH_CLI.main(
                    ["https://gitcode.com/openeuler/OmniStream/pull/446"]
                )

        self.assertEqual(status, 0)
        self.assertTrue(stdout.getvalue().startswith("diff --git "))
        self.assertNotIn("base-sha", stdout.getvalue())
        self.assertEqual(
            stderr.getvalue(),
            "patch: state=merged api_base=base-sha head=head-sha files=1\n",
        )
        self.assert_only_metadata_and_files(api)

    def test_main_writes_empty_patch_for_no_changed_files(self):
        api = FakeApi(
            {
                "state": "open",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha"},
            },
            [],
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch.object(PATCH_CLI, "GitCodeApi", return_value=api):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = PATCH_CLI.main(
                    ["https://gitcode.com/openeuler/OmniStream/pull/446"]
                )

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "patch: state=open api_base=base-sha head=head-sha files=0\n",
        )
        self.assert_only_metadata_and_files(api)

    def test_main_atomically_replaces_output_with_empty_patch_for_no_changed_files(self):
        api = FakeApi(
            {
                "state": "closed",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha"},
            },
            [],
        )

        with tempfile.TemporaryDirectory() as directory:
            output_path = pathlib.Path(directory) / "pull.patch"
            output_path.write_bytes(b"keep\n")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with mock.patch.object(PATCH_CLI, "GitCodeApi", return_value=api):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                    stderr
                ):
                    status = PATCH_CLI.main(
                        [
                            "https://gitcode.com/openeuler/OmniStream/pull/446",
                            "--output",
                            str(output_path),
                        ]
                    )

            self.assertEqual(status, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(),
                "patch: state=closed api_base=base-sha head=head-sha files=0\n",
            )
            self.assertEqual(output_path.read_bytes(), b"")
            self.assert_only_metadata_and_files(api)
            subprocess.run(
                ["git", "init", "-q"],
                cwd=directory,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "apply", "--check", "--allow-empty", str(output_path)],
                cwd=directory,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "apply", "--allow-empty", str(output_path)],
                cwd=directory,
                check=True,
                capture_output=True,
            )

    def test_main_preserves_existing_output_when_renderer_returns_empty_patch(self):
        api = FakeApi(
            {"base": {"sha": "base-sha"}, "head": {"sha": "head-sha"}},
            [modified_file()],
        )

        with tempfile.TemporaryDirectory() as directory:
            output_path = pathlib.Path(directory) / "pull.patch"
            output_path.write_bytes(b"keep\n")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with mock.patch.object(PATCH_CLI, "GitCodeApi", return_value=api):
                with mock.patch.object(PATCH_CLI, "render_git_patch", return_value=""):
                    with contextlib.redirect_stdout(
                        stdout
                    ), contextlib.redirect_stderr(stderr):
                        status = PATCH_CLI.main(
                            [
                                "https://gitcode.com/openeuler/OmniStream/pull/446",
                                "--output",
                                str(output_path),
                            ]
                        )

            self.assertEqual(status, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("empty patch", stderr.getvalue())
            self.assertEqual(output_path.read_bytes(), b"keep\n")
            self.assert_only_metadata_and_files(api)

    def test_main_preserves_existing_output_when_later_file_is_too_large(self):
        too_large_file = modified_file("large.txt")
        too_large_file["patch"]["too_large"] = True
        api = FakeApi(
            {"base": {"sha": "base-sha"}, "head": {"sha": "head-sha"}},
            [modified_file(), too_large_file],
        )

        with tempfile.TemporaryDirectory() as directory:
            output_path = pathlib.Path(directory) / "pull.patch"
            output_path.write_bytes(b"keep\n")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with mock.patch.object(PATCH_CLI, "GitCodeApi", return_value=api):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                    stderr
                ):
                    status = PATCH_CLI.main(
                        [
                            "https://gitcode.com/openeuler/OmniStream/pull/446",
                            "--output",
                            str(output_path),
                        ]
                    )

            self.assertEqual(status, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("large.txt", stderr.getvalue())
            self.assertIn("too large", stderr.getvalue())
            self.assertEqual(output_path.read_bytes(), b"keep\n")


class GitPatchApplyTest(unittest.TestCase):
    def assert_patch_applies(
        self,
        files,
        base_files,
        expected_files,
        expected_modes=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for relative_path, content in base_files.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o644)

            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            patch_path = root / "change.patch"
            try:
                rendered_patch = render_git_patch(files)
            except PatchFormatError as error:
                self.fail(f"renderer rejected a supported patch: {error}")
            with patch_path.open("w", encoding="utf-8", newline="") as patch_file:
                patch_file.write(rendered_patch)
            subprocess.run(
                [
                    "git",
                    "apply",
                    "--check",
                    "--whitespace=nowarn",
                    str(patch_path),
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "apply",
                    "--whitespace=nowarn",
                    str(patch_path),
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )

            actual_files = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and ".git" not in path.parts and path != patch_path
            }
            self.assertEqual(actual_files, expected_files)
            for relative_path, expected_mode in (expected_modes or {}).items():
                actual_mode = stat.S_IMODE((root / relative_path).stat().st_mode)
                self.assertEqual(actual_mode, expected_mode)

    def test_renders_and_applies_modified_file(self):
        files = [
            {
                "patch": {
                    "diff": "@@ -1,2 +1,3 @@\n one\n-two\n+TWO\n+three\n",
                    "old_path": "notes.txt",
                    "new_path": "notes.txt",
                    "a_mode": "100644",
                    "b_mode": "100644",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertIn("diff --git a/notes.txt b/notes.txt\n", patch)
        self.assertIn("--- a/notes.txt\n+++ b/notes.txt\n", patch)
        self.assert_patch_applies(
            files,
            {"notes.txt": b"one\ntwo\n"},
            {"notes.txt": b"one\nTWO\nthree\n"},
        )

    def test_renders_multiple_files_in_stable_input_order_and_applies(self):
        files = [modified_file("z-last.txt"), pr_446_new_file("a-first.txt")]

        first_patch = render_git_patch(files)
        second_patch = render_git_patch(files)

        self.assertEqual(first_patch, second_patch)
        self.assertLess(
            first_patch.index("diff --git a/z-last.txt b/z-last.txt"),
            first_patch.index("diff --git a/a-first.txt b/a-first.txt"),
        )
        self.assert_patch_applies(
            files,
            {"z-last.txt": b"old\n"},
            {"z-last.txt": b"new\n", "a-first.txt": b"new state\n"},
        )

    def test_renders_and_applies_empty_file_to_text(self):
        file_info = modified_file("empty.txt")
        file_info["patch"].update(
            {
                "diff": "@@ -0,0 +1 @@\n+filled\n",
                "added_lines": 1,
                "removed_lines": 0,
            }
        )

        self.assert_patch_applies(
            [file_info], {"empty.txt": b""}, {"empty.txt": b"filled\n"}
        )

    def test_renders_and_applies_text_to_empty_file(self):
        file_info = modified_file("filled.txt")
        file_info["patch"].update(
            {
                "diff": "@@ -1 +0,0 @@\n-filled\n",
                "added_lines": 0,
                "removed_lines": 1,
            }
        )

        self.assert_patch_applies(
            [file_info], {"filled.txt": b"filled\n"}, {"filled.txt": b""}
        )

    def test_renders_and_applies_empty_file_rename_to_text(self):
        file_info = modified_file("empty.txt")
        file_info["patch"].update(
            {
                "diff": "@@ -0,0 +1 @@\n+filled\n",
                "new_path": "filled.txt",
                "renamed_file": True,
                "added_lines": 1,
                "removed_lines": 0,
            }
        )

        self.assert_patch_applies(
            [file_info], {"empty.txt": b""}, {"filled.txt": b"filled\n"}
        )

    def test_renders_and_applies_new_file_with_zero_count_old_range(self):
        files = [
            {
                "patch": {
                    "diff": "@@ -0,0 +1 @@\n+new file\n",
                    "old_path": None,
                    "new_path": "added.txt",
                    "a_mode": None,
                    "b_mode": "100644",
                    "new_file": True,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertIn("diff --git a/added.txt b/added.txt\n", patch)
        self.assertIn("new file mode 100644\n", patch)
        self.assertIn("--- /dev/null\n+++ b/added.txt\n", patch)
        self.assert_patch_applies(files, {}, {"added.txt": b"new file\n"})

    def test_renders_and_applies_pr_446_new_file_sentinels(self):
        path = "cpp/runtime/state/restore/NewState.h"
        files = [pr_446_new_file(path)]

        patch = render_git_patch(files)

        self.assertIn(f"diff --git a/{path} b/{path}\n", patch)
        self.assertIn("new file mode 100644\n", patch)
        self.assertIn(f"--- /dev/null\n+++ b/{path}\n", patch)
        self.assert_patch_applies(files, {}, {path: b"new state\n"})

    def test_renders_and_applies_deleted_file_with_zero_count_new_range(self):
        files = [
            {
                "patch": {
                    "diff": "@@ -1 +0,0 @@\n-gone\n",
                    "old_path": "removed.txt",
                    "new_path": None,
                    "a_mode": "100644",
                    "b_mode": None,
                    "new_file": False,
                    "deleted_file": True,
                    "renamed_file": False,
                    "too_large": False,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertIn("diff --git a/removed.txt b/removed.txt\n", patch)
        self.assertIn("deleted file mode 100644\n", patch)
        self.assertIn("--- a/removed.txt\n+++ /dev/null\n", patch)
        self.assert_patch_applies(files, {"removed.txt": b"gone\n"}, {})

    def test_renders_and_applies_pure_rename(self):
        files = [
            {
                "patch": {
                    "diff": "",
                    "old_path": "old.txt",
                    "new_path": "new.txt",
                    "a_mode": "100644",
                    "b_mode": "100644",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": True,
                    "too_large": False,
                    "added_lines": 0,
                    "removed_lines": 0,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertEqual(
            patch,
            "diff --git a/old.txt b/new.txt\n"
            "similarity index 100%\n"
            "rename from old.txt\n"
            "rename to new.txt\n",
        )
        self.assert_patch_applies(
            files, {"old.txt": b"same\n"}, {"new.txt": b"same\n"}
        )

    def test_renders_and_applies_rename_with_modification(self):
        files = [
            {
                "patch": {
                    "diff": "@@ -1 +1 @@\n-old\n+new\n",
                    "old_path": "before.txt",
                    "new_path": "after.txt",
                    "a_mode": "100644",
                    "b_mode": "100644",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": True,
                    "too_large": False,
                    "added_lines": 1,
                    "removed_lines": 1,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertIn("rename from before.txt\nrename to after.txt\n", patch)
        self.assertIn("--- a/before.txt\n+++ b/after.txt\n", patch)
        self.assert_patch_applies(
            files, {"before.txt": b"old\n"}, {"after.txt": b"new\n"}
        )

    def test_renders_and_applies_mode_only_change(self):
        files = [
            {
                "patch": {
                    "diff": "",
                    "old_path": "script.sh",
                    "new_path": "script.sh",
                    "a_mode": "100644",
                    "b_mode": "100755",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                    "added_lines": 0,
                    "removed_lines": 0,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertIn("old mode 100644\nnew mode 100755\n", patch)
        self.assert_patch_applies(
            files,
            {"script.sh": b"#!/bin/sh\n"},
            {"script.sh": b"#!/bin/sh\n"},
            {"script.sh": 0o755},
        )

    def test_renders_and_applies_empty_new_file(self):
        files = [
            {
                "patch": {
                    "diff": "",
                    "old_path": None,
                    "new_path": "empty.txt",
                    "a_mode": None,
                    "b_mode": "100644",
                    "new_file": True,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                    "added_lines": 0,
                    "removed_lines": 0,
                }
            }
        ]

        self.assert_patch_applies(
            files, {}, {"empty.txt": b""}, {"empty.txt": 0o644}
        )

    def test_renders_and_applies_quoted_path(self):
        path = 'dir with space/quote"and\\backslash.txt'
        files = [
            {
                "patch": {
                    "diff": "@@ -1 +1 @@\n-old\n+new\n",
                    "old_path": path,
                    "new_path": path,
                    "a_mode": "100644",
                    "b_mode": "100644",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                    "added_lines": 1,
                    "removed_lines": 1,
                }
            }
        ]

        patch = render_git_patch(files)

        self.assertIn('"a/dir with space/quote\\"and\\\\backslash.txt"', patch)
        self.assert_patch_applies(files, {path: b"old\n"}, {path: b"new\n"})

    def test_renders_and_applies_crlf_new_file_without_byte_changes(self):
        files = [
            {
                "patch": {
                    "diff": "@@ -0,0 +1,2 @@\r\n+first\r\n+second\r\n",
                    "old_path": None,
                    "new_path": "crlf.txt",
                    "a_mode": None,
                    "b_mode": "100644",
                    "new_file": True,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                    "added_lines": 2,
                    "removed_lines": 0,
                }
            }
        ]

        self.assert_patch_applies(files, {}, {"crlf.txt": b"first\r\nsecond\r\n"})

    def test_renders_and_applies_file_without_trailing_newline(self):
        files = [
            {
                "patch": {
                    "diff": (
                        "@@ -0,0 +1 @@\n"
                        "+last line\n"
                        "\\ No newline at end of file\n"
                    ),
                    "old_path": None,
                    "new_path": "no-newline.txt",
                    "a_mode": None,
                    "b_mode": "100644",
                    "new_file": True,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                    "added_lines": 1,
                    "removed_lines": 0,
                }
            }
        ]

        self.assert_patch_applies(files, {}, {"no-newline.txt": b"last line"})

    def test_renders_and_applies_replacement_without_trailing_newline(self):
        files = [
            {
                "patch": {
                    "diff": (
                        "@@ -1 +1 @@\n"
                        "-old\n"
                        "\\ No newline at end of file\n"
                        "+new\n"
                        "\\ No newline at end of file\n"
                    ),
                    "old_path": "no-newline.txt",
                    "new_path": "no-newline.txt",
                    "a_mode": "100644",
                    "b_mode": "100644",
                    "new_file": False,
                    "deleted_file": False,
                    "renamed_file": False,
                    "too_large": False,
                    "added_lines": 1,
                    "removed_lines": 1,
                }
            }
        ]

        self.assert_patch_applies(
            files, {"no-newline.txt": b"old"}, {"no-newline.txt": b"new"}
        )


class GitPatchValidationTest(unittest.TestCase):
    def patch_file(self, **overrides):
        patch = {
            "diff": "@@ -1 +1 @@\n-old\n+new\n",
            "old_path": "file.txt",
            "new_path": "file.txt",
            "a_mode": "100644",
            "b_mode": "100644",
            "new_file": False,
            "deleted_file": False,
            "renamed_file": False,
            "too_large": False,
            "added_lines": 1,
            "removed_lines": 1,
        }
        patch.update(overrides)
        return {"patch": patch}

    def assert_patch_error(self, regex, **overrides):
        with self.assertRaisesRegex(PatchFormatError, regex):
            render_git_patch([self.patch_file(**overrides)])

    def test_rejects_too_large_patch(self):
        self.assert_patch_error("too large", too_large=True)

    def test_rejects_missing_or_non_boolean_too_large(self):
        cases = (None, 0, 1, "false", "true")
        for value in cases:
            with self.subTest(value=value):
                self.assert_patch_error("too_large must be a boolean", too_large=value)

        file_info = self.patch_file()
        del file_info["patch"]["too_large"]
        with self.assertRaisesRegex(PatchFormatError, "too_large must be a boolean"):
            render_git_patch([file_info])

    def test_rejects_missing_or_non_boolean_status_flags(self):
        for field in ("new_file", "deleted_file", "renamed_file"):
            for value in (None, 0, 1, "false", "true"):
                with self.subTest(field=field, value=value):
                    self.assert_patch_error(
                        f"{field} must be a boolean", **{field: value}
                    )

            file_info = self.patch_file()
            del file_info["patch"][field]
            with self.subTest(field=field, value="missing"):
                with self.assertRaisesRegex(
                    PatchFormatError, f"{field} must be a boolean"
                ):
                    render_git_patch([file_info])

    def test_rejects_binary_files_marker(self):
        self.assert_patch_error(
            "binary patch", diff="Binary files a/file.txt and b/file.txt differ\n"
        )

    def test_rejects_git_binary_patch_marker(self):
        self.assert_patch_error("binary patch", diff="GIT binary patch\nliteral 0\nHcmV?d00001\n")

    def test_rejects_content_changes_without_hunk(self):
        for diff in (
            "-old\n+new\n",
            "garbage\n@@ -1 +1 @@\n-old\n+new\n",
            "\n@@ -1 +1 @@\n-old\n+new\n",
        ):
            with self.subTest(diff=diff):
                self.assert_patch_error("start with a hunk header", diff=diff)

    def test_rejects_nested_line_count_mismatch(self):
        self.assert_patch_error("added line count", added_lines=2)

    def test_prefers_nested_line_counts_over_top_level_counts(self):
        file_info = self.patch_file(added_lines=2)
        file_info.update({"additions": 1, "deletions": 1})

        with self.assertRaisesRegex(PatchFormatError, "added line count"):
            render_git_patch([file_info])

    def test_rejects_top_level_line_count_mismatch_when_nested_missing(self):
        file_info = self.patch_file()
        del file_info["patch"]["added_lines"]
        del file_info["patch"]["removed_lines"]
        file_info.update({"additions": 1, "deletions": 2})

        with self.assertRaisesRegex(PatchFormatError, "removed line count"):
            render_git_patch([file_info])

    def test_rejects_contradictory_status_flags(self):
        for flags in (
            {"new_file": True, "deleted_file": True},
            {"new_file": True, "renamed_file": True},
            {"deleted_file": True, "renamed_file": True},
        ):
            with self.subTest(flags=flags):
                self.assert_patch_error("contradictory status", **flags)

    def test_rejects_missing_required_paths(self):
        cases = (
            {"old_path": None},
            {"new_path": None},
            {"new_file": True, "old_path": None, "new_path": None},
            {"deleted_file": True, "old_path": None, "new_path": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_patch_error("missing a (?:new|old) path", **overrides)

    def test_rejects_missing_required_modes(self):
        cases = (
            {"a_mode": None},
            {"b_mode": None},
            {"new_file": True, "old_path": None, "a_mode": None, "b_mode": None},
            {
                "deleted_file": True,
                "new_path": None,
                "a_mode": None,
                "b_mode": None,
            },
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_patch_error("missing a (?:new|old) mode", **overrides)

    def test_rejects_non_regular_or_noncanonical_modes(self):
        for mode in ("garbage", "100600", "100664", "120000", "160000"):
            with self.subTest(mode=mode):
                self.assert_patch_error("unsupported .* mode", a_mode=mode)

    def test_rejects_invalid_status_matrix(self):
        cases = (
            {
                "name": "ordinary no-op",
                "regex": "no changes",
                "overrides": {"diff": "", "added_lines": 0, "removed_lines": 0},
            },
            {
                "name": "unflagged path change",
                "regex": "path change.*renamed_file",
                "overrides": {"new_path": "other.txt"},
            },
            {
                "name": "rename without path change",
                "regex": "rename.*different paths",
                "overrides": {"renamed_file": True},
            },
            {
                "name": "new file with old path",
                "regex": "new file.*old path",
                "overrides": {
                    "diff": "@@ -0,0 +1 @@\n+new\n",
                    "new_file": True,
                    "old_path": "old.txt",
                    "a_mode": None,
                    "added_lines": 1,
                    "removed_lines": 0,
                },
            },
            {
                "name": "new file with old mode",
                "regex": "new file.*old mode",
                "overrides": {
                    "diff": "@@ -0,0 +1 @@\n+new\n",
                    "new_file": True,
                    "old_path": None,
                    "a_mode": "100644",
                    "added_lines": 1,
                    "removed_lines": 0,
                },
            },
            {
                "name": "new file diff consumes old content",
                "regex": "new file diff.*old lines",
                "overrides": {
                    "new_file": True,
                    "old_path": None,
                    "a_mode": None,
                },
            },
            {
                "name": "deleted file with new path",
                "regex": "deleted file.*new path",
                "overrides": {
                    "diff": "@@ -1 +0,0 @@\n-old\n",
                    "deleted_file": True,
                    "new_path": "new.txt",
                    "b_mode": None,
                    "added_lines": 0,
                    "removed_lines": 1,
                },
            },
            {
                "name": "deleted file with new mode",
                "regex": "deleted file.*new mode",
                "overrides": {
                    "diff": "@@ -1 +0,0 @@\n-old\n",
                    "deleted_file": True,
                    "new_path": None,
                    "b_mode": "100644",
                    "added_lines": 0,
                    "removed_lines": 1,
                },
            },
            {
                "name": "deleted file diff produces new content",
                "regex": "deleted file diff.*new lines",
                "overrides": {
                    "deleted_file": True,
                    "new_path": None,
                    "b_mode": None,
                },
            },
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assert_patch_error(case["regex"], **case["overrides"])

    def test_rejects_incomplete_or_contradictory_new_file_sentinels(self):
        cases = (
            {"old_path": "file.txt", "a_mode": None},
            {"old_path": None, "a_mode": "0"},
            {"old_path": "other.txt", "a_mode": "0"},
            {"old_path": "file.txt", "a_mode": "100644"},
        )
        for old_side in cases:
            with self.subTest(old_side=old_side):
                file_info = pr_446_new_file("file.txt")
                file_info["patch"].update(old_side)
                with self.assertRaisesRegex(PatchFormatError, "new file.*old"):
                    render_git_patch([file_info])

    def test_rejects_unverified_deleted_file_sentinels(self):
        self.assert_patch_error(
            "deleted file.*new path",
            diff="@@ -1 +0,0 @@\n-old\n",
            old_path="file.txt",
            new_path="file.txt",
            a_mode="100644",
            b_mode="0",
            new_file=False,
            deleted_file=True,
            added_lines=0,
            removed_lines=1,
        )

    def test_rejects_malformed_or_inconsistent_hunks(self):
        cases = (
            ("malformed hunk header", "@@garbage\n-old\n+new\n"),
            ("old line count", "@@ -1,2 +1 @@\n-old\n+new\n"),
            ("new line count", "@@ -1 +1,2 @@\n-old\n+new\n"),
            ("old line count", "@@ -1,2 +1 @@\n context\n"),
        )
        for regex, diff in cases:
            with self.subTest(diff=diff):
                self.assert_patch_error(
                    regex,
                    diff=diff,
                    added_lines=diff.count("\n+"),
                    removed_lines=diff.count("\n-"),
                )

    def test_rejects_impossible_hunk_coordinates(self):
        cases = (
            (
                "old range with nonzero count must start at 1 or later",
                "@@ -0 +1 @@\n-old\n+new\n",
            ),
            (
                "new range with nonzero count must start at 1 or later",
                "@@ -1 +0 @@\n-old\n+new\n",
            ),
            (
                "old hunks are out of order or overlapping",
                (
                    "@@ -3 +3 @@\n-three\n+THREE\n"
                    "@@ -1 +1 @@\n-one\n+ONE\n"
                ),
            ),
            (
                "old hunks are out of order or overlapping",
                (
                    "@@ -1,2 +1,2 @@\n-one\n+ONE\n two\n"
                    "@@ -2 +2 @@\n-two\n+TWO\n"
                ),
            ),
            (
                "new hunks are out of order or overlapping",
                (
                    "@@ -1,2 +1,2 @@\n-one\n+ONE\n two\n"
                    "@@ -3 +2 @@\n-three\n+THREE\n"
                ),
            ),
            (
                "old and new hunk gaps differ",
                "@@ -2 +3 @@\n-two\n+TWO\n",
            ),
            (
                "old and new hunk gaps differ",
                (
                    "@@ -1 +1,2 @@\n-one\n+ONE\n+extra\n"
                    "@@ -3 +5 @@\n-three\n+THREE\n"
                ),
            ),
        )
        for regex, diff in cases:
            with self.subTest(diff=diff):
                self.assert_patch_error(
                    regex,
                    diff=diff,
                    added_lines=diff.count("\n+"),
                    removed_lines=diff.count("\n-"),
                )

    def test_rejects_zero_count_hunks_for_ordinary_files(self):
        cases = (
            ("zero-count old range", "@@ -1,0 +2 @@\n+inserted\n"),
            ("zero-count new range", "@@ -2 +1,0 @@\n-removed\n"),
        )
        for regex, diff in cases:
            with self.subTest(diff=diff):
                self.assert_patch_error(
                    regex,
                    diff=diff,
                    added_lines=diff.count("\n+"),
                    removed_lines=diff.count("\n-"),
                )

    def test_rejects_zero_count_hunks_for_renamed_files(self):
        cases = (
            ("zero-count old range", "@@ -1,0 +2 @@\n+inserted\n"),
            ("zero-count new range", "@@ -2 +1,0 @@\n-removed\n"),
        )
        for regex, diff in cases:
            with self.subTest(diff=diff):
                self.assert_patch_error(
                    regex,
                    diff=diff,
                    new_path="renamed.txt",
                    renamed_file=True,
                    added_lines=diff.count("\n+"),
                    removed_lines=diff.count("\n-"),
                )

    def test_rejects_nonzero_zero_range_anchors_for_new_and_deleted_files(self):
        cases = (
            {
                "regex": "zero-count old range.*start at 0",
                "overrides": {
                    "diff": "@@ -1,0 +2 @@\n+inserted\n",
                    "new_file": True,
                    "old_path": None,
                    "a_mode": None,
                    "added_lines": 1,
                    "removed_lines": 0,
                },
            },
            {
                "regex": "zero-count new range.*start at 0",
                "overrides": {
                    "diff": "@@ -2 +1,0 @@\n-removed\n",
                    "deleted_file": True,
                    "new_path": None,
                    "b_mode": None,
                    "added_lines": 0,
                    "removed_lines": 1,
                },
            },
        )
        for case in cases:
            with self.subTest(overrides=case["overrides"]):
                self.assert_patch_error(case["regex"], **case["overrides"])

    def test_rejects_zero_range_hunk_combined_with_another_hunk(self):
        diff = (
            "@@ -0,0 +1 @@\n"
            "+inserted\n"
            "@@ -1 +2 @@\n"
            "-old\n"
            "+new\n"
        )

        self.assert_patch_error(
            "zero-count range must be the only hunk",
            diff=diff,
            added_lines=2,
            removed_lines=1,
        )

    def test_rejects_hunks_without_actual_changes(self):
        self.assert_patch_error(
            "hunk must contain an added or removed line",
            diff="@@ -1 +1 @@\n unchanged\n",
            added_lines=0,
            removed_lines=0,
        )

    def test_rejects_misplaced_no_newline_markers(self):
        for diff in (
            "@@ -1 +1 @@\n\\ No newline at end of file\n-old\n+new\n",
            (
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
                "\\ No newline at end of file\n"
                "\\ No newline at end of file\n"
            ),
        ):
            with self.subTest(diff=diff):
                self.assert_patch_error(
                    "no-newline marker must follow a hunk content line",
                    diff=diff,
                    added_lines=1,
                    removed_lines=1,
                )

    def test_rejects_content_after_no_newline_side_eof(self):
        cases = (
            (
                "old side continues",
                (
                    "@@ -1,2 +1 @@\n"
                    "-old\n"
                    "\\ No newline at end of file\n"
                    "-more\n"
                    "+new\n"
                ),
                1,
                2,
            ),
            (
                "new side continues",
                (
                    "@@ -1 +1,2 @@\n"
                    "-old\n"
                    "+first\n"
                    "\\ No newline at end of file\n"
                    "+second\n"
                ),
                2,
                1,
            ),
            (
                "old side continues",
                (
                    "@@ -1,2 +1 @@\n"
                    "-old\n"
                    "\\ No newline at end of file\n"
                    " context\n"
                ),
                0,
                1,
            ),
            (
                "old side continues",
                (
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "\\ No newline at end of file\n"
                    "+new\n"
                    "@@ -2 +2 @@\n"
                    "-more\n"
                    "+newer\n"
                ),
                2,
                2,
            ),
            (
                "old side continues",
                (
                    "@@ -1,2 +1,2 @@\n"
                    " context\n"
                    "\\ No newline at end of file\n"
                    "-old\n"
                    "+new\n"
                ),
                1,
                1,
            ),
        )
        for regex, diff, added_lines, removed_lines in cases:
            with self.subTest(diff=diff):
                self.assert_patch_error(
                    regex,
                    diff=diff,
                    added_lines=added_lines,
                    removed_lines=removed_lines,
                )

    def test_rejects_unsafe_paths(self):
        for path in (
            "/absolute.txt",
            "../outside.txt",
            "dir/../outside.txt",
            ".",
            "dir/./file.txt",
            "dir//file.txt",
            "dir/",
            "nul\0path.txt",
            "carriage\rreturn.txt",
            "line\nfeed.txt",
            "lone-surrogate-\ud800.txt",
        ):
            with self.subTest(path=path):
                self.assert_patch_error("unsafe path", old_path=path, new_path=path)

    def test_validates_all_files_before_rendering(self):
        files = [self.patch_file(), self.patch_file(old_path="../bad", new_path="../bad")]

        with mock.patch("gitcode_patch._render_file_patch") as render_file:
            with self.assertRaisesRegex(PatchFormatError, "unsafe path"):
                render_git_patch(files)

        render_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
