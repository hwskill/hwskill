#!/usr/bin/env python3

import io
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock


SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


import gitcode_pr_common as COMMON


class FakeApi:
    def __init__(self, comment_pages=None, files=None, pull_request=None):
        self.comment_pages = comment_pages or []
        self.files = files or []
        self.pull_request = pull_request if pull_request is not None else {}
        self.calls = []
        self.per_page = 100

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/files"):
            return self.files, {"total_page": "1"}
        if path.endswith("/comments"):
            page = int((params or {}).get("page", 1))
            return self.comment_pages[page - 1]
        return self.pull_request, {}

    def get_paginated(self, path, params=None):
        return COMMON.GitCodeApi.get_paginated(self, path, params)


class RaisingOpener:
    def __init__(self, token):
        self.token = token

    def open(self, request, timeout=None):
        url = request.full_url
        raise urllib.error.HTTPError(
            url,
            401,
            "Unauthorized",
            hdrs={},
            fp=io.BytesIO(f"bad token {self.token}".encode("utf-8")),
        )


class ParseTargetTest(unittest.TestCase):
    def test_parse_pull_url(self):
        self.assertEqual(
            COMMON.parse_target("https://gitcode.com/openeuler/OmniStream/pull/433", None),
            ("openeuler", "OmniStream", 433),
        )

    def test_parse_pulls_url_and_owner_repo(self):
        self.assertEqual(
            COMMON.parse_target("https://gitcode.com/openeuler/OmniStream/pulls/433/", None),
            ("openeuler", "OmniStream", 433),
        )
        self.assertEqual(
            COMMON.parse_target("openeuler/OmniStream", "433"),
            ("openeuler", "OmniStream", 433),
        )

    def test_rejects_invalid_target(self):
        with self.assertRaisesRegex(ValueError, "GitCode PR"):
            COMMON.parse_target("https://example.com/openeuler/OmniStream/pull/433", None)
        with self.assertRaisesRegex(ValueError, "PR number"):
            COMMON.parse_target("openeuler/OmniStream", None)


class PaginationTest(unittest.TestCase):
    def test_uses_total_page_and_deduplicates_comment_ids(self):
        api = FakeApi(
            comment_pages=[
                ([{"id": 1}, {"id": 2}], {"total_page": "2"}),
                ([{"id": 2}, {"id": 3}], {"total_page": "2"}),
            ],
        )

        comments = api.get_paginated("/comments", {"per_page": 2})

        self.assertEqual([comment["id"] for comment in comments], [1, 2, 3])
        self.assertEqual([call[1]["page"] for call in api.calls], [1, 2])

    def test_falls_back_to_short_page_without_total_page_header(self):
        api = FakeApi(
            comment_pages=[
                ([{"id": 1}, {"id": 2}], {}),
                ([{"id": 3}], {}),
            ],
        )

        comments = api.get_paginated("/comments", {"per_page": 2})

        self.assertEqual([comment["id"] for comment in comments], [1, 2, 3])
        self.assertEqual(len(api.calls), 2)


class HelperTest(unittest.TestCase):
    def test_pull_paths_quotes_owner_and_repo(self):
        self.assertEqual(
            COMMON.pull_paths("org/team", "repo name", 446),
            ("/repos/org%2Fteam/repo%20name", "/repos/org%2Fteam/repo%20name/pulls/446"),
        )

    def test_fetch_pull_request_rejects_non_object_payload(self):
        with self.assertRaisesRegex(COMMON.GitCodeApiError, "non-object pull request"):
            COMMON.fetch_pull_request(FakeApi(pull_request=[]), "/repos/org/repo/pulls/446")

    def test_fetch_changed_files_uses_all_pages(self):
        api = FakeApi(files=[{"id": 1}, {"id": 2}])
        files = COMMON.fetch_changed_files(api, "/repos/org/repo/pulls/446")
        self.assertEqual([item["id"] for item in files], [1, 2])
        self.assertTrue(all(call[0].endswith("/files") for call in api.calls))

    def test_write_text_atomic_replaces_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = pathlib.Path(directory) / "report.json"
            output_path.write_text("old", encoding="utf-8")

            COMMON.write_text_atomic(output_path, "new\\n")

            self.assertEqual(output_path.read_text(encoding="utf-8"), "new\\n")

    def test_write_text_atomic_removes_temporary_file_when_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = pathlib.Path(directory) / "report.json"
            output_path.write_bytes(b"keep\n")
            real_named_temporary_file = COMMON.tempfile.NamedTemporaryFile

            class WriteFailingTemporaryFile:
                def __init__(self, *args, **kwargs):
                    self.context = real_named_temporary_file(*args, **kwargs)

                def __enter__(self):
                    self.file = self.context.__enter__()
                    self.name = self.file.name
                    return self

                def __exit__(self, *args):
                    return self.context.__exit__(*args)

                def write(self, output):
                    self.file.write(output[:1])
                    raise OSError("write failed")

            with mock.patch.object(
                COMMON.tempfile,
                "NamedTemporaryFile",
                side_effect=WriteFailingTemporaryFile,
            ):
                with self.assertRaisesRegex(OSError, "write failed"):
                    COMMON.write_text_atomic(output_path, "replacement\n")

            self.assertEqual(output_path.read_bytes(), b"keep\n")
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [output_path])

    def test_write_text_atomic_removes_temporary_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = pathlib.Path(directory) / "report.json"
            output_path.write_bytes(b"keep\n")

            with mock.patch.object(
                COMMON.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    COMMON.write_text_atomic(output_path, "replacement\n")

            self.assertEqual(output_path.read_bytes(), b"keep\n")
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [output_path])


class SecurityTest(unittest.TestCase):
    def test_user_agent_is_repository_neutral(self):
        response = mock.MagicMock()
        response.read.return_value = b"{}"
        response.headers = {}
        opener = mock.MagicMock()
        opener.open.return_value = response
        api = COMMON.GitCodeApi(opener=opener, max_retries=0)

        api.get_json("/repos/org/repo/pulls/1")

        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.get_header("User-agent"),
            "gitcode-pr-review-fetch/1",
        )

    def test_http_error_redacts_token_from_url_and_body(self):
        token = "secret-read-token"
        api = COMMON.GitCodeApi(
            token=token,
            opener=RaisingOpener(token),
            max_retries=0,
        )

        with self.assertRaises(COMMON.GitCodeApiError) as context:
            api.get_json("/repos/org/repo/pulls/1")

        message = str(context.exception)
        self.assertNotIn(token, message)
        self.assertIn("***", message)


if __name__ == "__main__":
    unittest.main()
