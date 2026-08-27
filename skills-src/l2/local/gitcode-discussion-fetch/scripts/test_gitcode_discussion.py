#!/usr/bin/env python3

import pathlib
import subprocess
import sys
import unittest
import urllib.error


SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


import gitcode_discussion as DISCUSSION


class ParseTargetTest(unittest.TestCase):
    def test_accepts_discussion_url(self):
        self.assertEqual(
            DISCUSSION.parse_target(
                "https://gitcode.com/linkeo2012/bolt/discussions/3", None
            ),
            ("linkeo2012", "bolt", 3),
        )

    def test_accepts_repo_and_number(self):
        self.assertEqual(
            DISCUSSION.parse_target("linkeo2012/bolt", "3"),
            ("linkeo2012", "bolt", 3),
        )

    def test_rejects_non_gitcode_or_missing_number(self):
        with self.assertRaisesRegex(ValueError, "GitCode Discussion"):
            DISCUSSION.parse_target("https://example.com/linkeo2012/bolt/discussions/3", None)
        with self.assertRaisesRegex(ValueError, "Discussion number"):
            DISCUSSION.parse_target("linkeo2012/bolt", None)


class FakeApi:
    def __init__(self):
        self.per_page = 2
        self.calls = []
        self.detail_requests = 0

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/discuss/3"):
            self.detail_requests += 1
            return {
                "id": "discussion-id",
                "number": 3,
                "title": "Title",
                "md_content": "Discussion body",
                "author": {"login": "author"},
                "created_at": "2026-08-06T00:00:00+08:00",
                "updated_at": "2026-08-06T01:00:00+08:00",
                "comment_total": 2,
            }, {}
        if path.endswith("/comment"):
            page = params["page"]
            pages = {
                1: ([
                    {
                        "id": "comment-1",
                        "md_content": "Top one",
                        "author": {"login": "alice"},
                        "created_at": "2026-08-06T02:00:00+08:00",
                        "reply_total": 1,
                    },
                    {
                        "id": "comment-2",
                        "md_content": "Top two",
                        "author": {"login": "bob"},
                        "created_at": "2026-08-06T03:00:00+08:00",
                        "reply_total": 0,
                    },
                ], {"total_page": "1"}),
            }
            return pages[page]
        if path.endswith("/comment/comment-1/reply"):
            return ([{
                "id": "reply-1",
                "md_content": "Reply one",
                "author": {"login": "carol"},
                "created_at": "2026-08-06T02:30:00+08:00",
            }], {"total_page": "1"})
        raise AssertionError(f"Unexpected API path: {path}")

    def get_paginated(self, path, params=None):
        return DISCUSSION.GitCodeApi.get_paginated(self, path, params)


class ArchiveTest(unittest.TestCase):
    def test_fetches_all_comments_and_places_replies_below_parent(self):
        archive = DISCUSSION.fetch_discussion_archive(FakeApi(), "linkeo2012", "bolt", 3)

        self.assertEqual(archive["summary"], {"comment_count": 2, "reply_count": 1})
        document = DISCUSSION.render_markdown(archive)
        self.assertLess(document.index("## 评论 1"), document.index("### 回复 1.1"))
        self.assertLess(document.index("### 回复 1.1"), document.index("## 评论 2"))
        self.assertIn("# Title", document)
        self.assertIn("comments: 2", document)
        self.assertIn("replies: 1", document)

    def test_rejects_changed_discussion_snapshot(self):
        api = FakeApi()
        original_get_json = api.get_json

        def get_json(path, params=None):
            payload, headers = original_get_json(path, params)
            if path.endswith("/discuss/3") and api.detail_requests == 2:
                payload = dict(payload)
                payload["updated_at"] = "2026-08-06T01:10:00+08:00"
            return payload, headers

        api.get_json = get_json

        with self.assertRaisesRegex(DISCUSSION.GitCodeApiError, "changed while fetching"):
            DISCUSSION.fetch_discussion_archive(api, "linkeo2012", "bolt", 3)


class MarkdownTest(unittest.TestCase):
    def test_rebases_headings_without_changing_fenced_code(self):
        self.assertEqual(
            DISCUSSION.rebase_headings("# Child\n```\n# literal\n```\n## Detail", 2),
            "### Child\n```\n# literal\n```\n#### Detail",
        )

    def test_does_not_close_a_long_fence_with_a_shorter_marker(self):
        self.assertEqual(
            DISCUSSION.rebase_headings(
                "````\n# literal\n```\n# still literal\n````\n# After", 2
            ),
            "````\n# literal\n```\n# still literal\n````\n### After",
        )


class PaginationTest(unittest.TestCase):
    def test_uses_all_pages_and_deduplicates_ids(self):
        class PagedApi:
            per_page = 2

            def __init__(self):
                self.pages = [
                    ([{"id": "one"}, {"id": "two"}], {"total_page": "2"}),
                    ([{"id": "two"}, {"id": "three"}], {"total_page": "2"}),
                ]
                self.calls = []

            def get_json(self, path, params=None):
                self.calls.append((path, params))
                return self.pages[params["page"] - 1]

        api = PagedApi()
        items = DISCUSSION.GitCodeApi.get_paginated(api, "/comments", {"per_page": 2})

        self.assertEqual([item["id"] for item in items], ["one", "two", "three"])
        self.assertEqual([call[1]["page"] for call in api.calls], [1, 2])


class SecurityTest(unittest.TestCase):
    def test_http_error_redacts_token(self):
        token = "secret-read-token"

        class RaisingOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    hdrs={},
                    fp=__import__("io").BytesIO(f"bad token {token}".encode("utf-8")),
                )

        api = DISCUSSION.GitCodeApi(token=token, opener=RaisingOpener(), max_retries=0)

        with self.assertRaises(DISCUSSION.GitCodeApiError) as context:
            api.get_json("/repos/org/repo/discuss/1")

        self.assertNotIn(token, str(context.exception))
        self.assertIn("***", str(context.exception))


class CliTest(unittest.TestCase):
    def test_help_describes_url_and_repo_number_inputs(self):
        command = [str(SCRIPTS_DIR / "fetch_gitcode_discussion.py"), "--help"]
        completed = subprocess.run(
            [sys.executable, *command], text=True, capture_output=True, check=False
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Discussion URL", completed.stdout)
        self.assertIn("owner/repo", completed.stdout)


if __name__ == "__main__":
    unittest.main()
