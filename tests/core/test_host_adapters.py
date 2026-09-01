import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hwskill.audit import AuditWriter
from hwskill.configuration import setup_codex
from hwskill.profiles import bind_profile, set_profiles
from hwskill.scopes import project_scope, user_scope


ROOT = Path(__file__).parents[2]


def require(module_name, attribute):
    try:
        module = __import__(module_name, fromlist=[attribute])
    except ModuleNotFoundError as exc:
        raise AssertionError(f"missing production module: {module_name}") from exc
    return getattr(module, attribute)


class HostAdaptersTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        self.project.mkdir()
        bind_profile(self.project, ROOT, "codex-demo")
        self.audit_path = Path(self.temp.name) / "audit.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_catalog_exposes_metadata_without_skill_body(self):
        render_effective_catalog = require(
            "hwskill.catalog", "render_effective_catalog"
        )

        text = render_effective_catalog(
            self.project, ROOT, AuditWriter(self.audit_path), session_id="shared-1"
        )

        self.assertIn("catalog_digest:", text)
        self.assertIn("local/gitcode-pr-review-fetch", text)
        self.assertIn("hwskill_search", text)
        self.assertIn("hwskill_load", text)
        self.assertIn("即使目录中已有匹配 ID，也必须先调用 hwskill_search", text)
        self.assertIn("不要调用宿主原生 Skill 工具", text)
        self.assertNotIn("# GitCode PR Review Fetch", text)
        event = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertEqual(event["event"], "catalog")
        self.assertEqual(event["session_id"], "shared-1")

    def test_claude_session_start_wraps_shared_catalog(self):
        session_start = require("hwskill.claude_code_adapter", "session_start")

        output = session_start(
            {"cwd": str(self.project), "session_id": "claude-1", "source": "startup"},
            ROOT,
            AuditWriter(self.audit_path),
        )

        hook = output["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "SessionStart")
        self.assertIn("local/gitcode-pr-review-fetch", hook["additionalContext"])

    def test_claude_runner_accepts_json_stdin(self):
        run_session_start = require("hwskill.claude_code_adapter", "run_session_start")

        output = json.loads(run_session_start(
            ROOT,
            self.audit_path,
            json.dumps({"cwd": str(self.project), "session_id": "claude-2"}),
        ))

        self.assertIn("catalog_digest:", output["hookSpecificOutput"]["additionalContext"])

    def test_opencode_adapter_returns_plain_shared_catalog(self):
        render_catalog = require("hwskill.opencode_adapter", "render_catalog")

        text = render_catalog(
            self.project, ROOT, AuditWriter(self.audit_path), session_id="opencode-1"
        )

        self.assertTrue(text.startswith("hwskill Effective Skill Catalog"))
        self.assertIn("local/gitcode-pr-review-fetch", text)

    def test_user_codex_adapter_uses_event_cwd_and_user_profile_fallback(self):
        run_session_start = require("hwskill.codex_adapter", "run_session_start")
        fallback_project = Path(self.temp.name) / "fallback"
        fallback_project.mkdir()
        with patch.dict(
            os.environ,
            {
                "HOME": str(Path(self.temp.name) / "home"),
                "XDG_CONFIG_HOME": str(Path(self.temp.name) / "config"),
                "XDG_STATE_HOME": str(Path(self.temp.name) / "state"),
            },
            clear=True,
        ):
            user = user_scope()
            set_profiles(user, ROOT, ("personal-baseline",))
            output = run_session_start(
                ROOT,
                self.audit_path,
                json.dumps({"cwd": str(fallback_project), "session_id": "user-1"}),
                scope="user",
                user_target=user,
            )

        self.assertIn("local/chinese-thinking", output)

    def test_user_codex_adapter_is_empty_when_valid_project_setup_exists(self):
        run_session_start = require("hwskill.codex_adapter", "run_session_start")
        setup_codex(project_scope(self.project), ROOT)

        output = run_session_start(
            ROOT,
            self.audit_path,
            json.dumps({"cwd": str(self.project), "session_id": "user-2"}),
            scope="user",
        )

        self.assertEqual(output, "")
        self.assertFalse(self.audit_path.exists())


if __name__ == "__main__":
    unittest.main()
