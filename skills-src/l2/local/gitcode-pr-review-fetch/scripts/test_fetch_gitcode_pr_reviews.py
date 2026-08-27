#!/usr/bin/env python3

import importlib.util
import pathlib
import sys
import unittest


SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


SCRIPT_PATH = pathlib.Path(__file__).with_name("fetch_gitcode_pr_reviews.py")
SPEC = importlib.util.spec_from_file_location("fetch_gitcode_pr_reviews", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeApi:
    def __init__(self, pr, comment_pages, files=None, single_comments=None):
        self.pr = pr
        self.comment_pages = comment_pages
        self.files = files or []
        self.single_comments = single_comments or {}
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/files"):
            return self.files, {"total_page": "1"}
        if path.endswith("/comments"):
            page = int((params or {}).get("page", 1))
            return self.comment_pages[page - 1]
        if "/pulls/comments/" in path:
            return self.single_comments[int(path.rsplit("/", 1)[1])], {}
        return self.pr, {}

    def get_paginated(self, path, params=None):
        return MODULE.GitCodeApi.get_paginated(self, path, params)


class ReportTest(unittest.TestCase):
    def test_help_describes_include_files_json_only(self):
        help_text = " ".join(MODULE.build_parser().format_help().split())

        self.assertIn(
            "Include changed files in JSON output only; Markdown does not display files.",
            help_text,
        )

    def test_fetches_all_comments_and_normalizes_replies(self):
        original_review = {
            "id": 2,
            "discussion_id": "discussion",
            "comment_type": "diff_comment",
            "body": "finding",
            "resolved": False,
            "diff_position": {"new_path": "a.cpp", "new_line": 7},
            "reply": [{"id": 3, "body": "response", "user": {"login": "author"}}],
            "future_field": {"preserved": True},
        }
        api = FakeApi(
            pr={"number": 433, "head": {"sha": "head"}},
            comment_pages=[
                ([{"id": 1, "comment_type": "pr_comment", "body": "summary"}], {"total_page": "2"}),
                ([original_review], {"total_page": "2"}),
            ],
            single_comments={
                2: {
                    "id": 2,
                    "comment_type": "DiffNote",
                    "position": {
                        "base_sha": "base",
                        "head_sha": "head",
                        "new_path": "a.cpp",
                        "new_line": 7,
                    },
                }
            },
        )

        report = MODULE.fetch_report(api, "openeuler", "OmniStream", 433, False)

        self.assertEqual(report["summary"]["comment_count"], 2)
        self.assertEqual(report["summary"]["review_comment_count"], 1)
        self.assertEqual(report["summary"]["reply_count"], 1)
        self.assertEqual(report["summary"]["unresolved_count"], 1)
        self.assertEqual(report["comments"][1]["comment_type"], "diff_comment")
        self.assertEqual(report["comments"][1]["source_comment_type"], "DiffNote")
        self.assertEqual(report["comments"][1]["position"]["new_line"], 7)
        self.assertEqual(report["comments"][1]["replies"][0]["body"], "response")
        self.assertEqual(report["comments"][1]["future_field"], {"preserved": True})
        self.assertIn("reply", original_review)
        self.assertNotIn("replies", original_review)

    def test_optionally_fetches_changed_files(self):
        api = FakeApi(
            pr={"number": 433},
            comment_pages=[([], {"total_page": "1"})],
            files=[{"new_path": "a.cpp"}],
        )

        report = MODULE.fetch_report(api, "openeuler", "OmniStream", 433, True)

        self.assertEqual(report["files"], [{"new_path": "a.cpp"}])
        self.assertTrue(any(path.endswith("/files") for path, _ in api.calls))

    def test_enriches_partial_diff_position_from_single_comment_endpoint(self):
        api = FakeApi(
            pr={"number": 433},
            comment_pages=[
                (
                    [
                        {
                            "id": 2,
                            "comment_type": "diff_comment",
                            "diff_position": {
                                "start_new_line": 113,
                                "end_new_line": 113,
                            },
                            "reply": [{"id": 3, "body": "response"}],
                        }
                    ],
                    {"total_page": "1"},
                )
            ],
            single_comments={
                2: {
                    "id": 2,
                    "position": {
                        "base_sha": "base",
                        "head_sha": "head",
                        "new_path": "a.cpp",
                        "new_line": 113,
                    },
                }
            },
        )

        report = MODULE.fetch_report(api, "openeuler", "OmniStream", 433, False)

        self.assertEqual(report["comments"][0]["position"]["new_path"], "a.cpp")
        self.assertEqual(report["comments"][0]["position"]["new_line"], 113)
        self.assertEqual(report["comments"][0]["replies"][0]["body"], "response")
        self.assertTrue(any("/pulls/comments/2" in path for path, _ in api.calls))

    def test_markdown_contains_review_position_resolution_and_reply(self):
        report = {
            "pull_request": {
                "number": 433,
                "title": "Review fetch",
                "state": "open",
                "head": {"sha": "abc"},
                "base": {"sha": "def"},
            },
            "summary": {
                "comment_count": 1,
                "review_comment_count": 1,
                "reply_count": 1,
                "unresolved_count": 1,
            },
            "comments": [
                {
                    "id": 2,
                    "discussion_id": "discussion",
                    "comment_type": "diff_comment",
                    "body": "finding",
                    "resolved": False,
                    "position": {"new_path": "a.cpp", "new_line": 7},
                    "user": {"login": "reviewer"},
                    "replies": [
                        {
                            "id": 3,
                            "body": "response",
                            "user": {"login": "author"},
                            "created_at": "2026-07-16T18:39:37+08:00",
                        }
                    ],
                }
            ],
            "files": [],
        }

        markdown = MODULE.render_markdown(report)

        self.assertIn("a.cpp:7", markdown)
        self.assertIn("未解决", markdown)
        self.assertIn("finding", markdown)
        self.assertIn("response", markdown)


if __name__ == "__main__":
    unittest.main()
