import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hwskill.audit import AuditEvent, AuditWriter
from hwskill.codex_adapter import session_start
from hwskill.mcp_server import HwskillMcpRuntime
from hwskill.profiles import bind_profile


ROOT = Path(__file__).parents[1]


class CodexRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        self.project.mkdir()
        bind_profile(self.project, ROOT, "codex-demo")
        self.audit_path = Path(self.temp.name) / "audit.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_audit_has_digest_but_no_prompt_or_body(self):
        AuditWriter(self.audit_path).write(AuditEvent(
            event="load", skill_id="local/example",
            content_digest="sha256:abc", result="ok",
        ))
        record = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertEqual(record["content_digest"], "sha256:abc")
        self.assertNotIn("prompt", record)
        self.assertNotIn("content", record)

    def test_hook_injects_metadata_not_body(self):
        output = session_start(
            {"cwd": str(self.project), "session_id": "s1", "source": "startup"},
            ROOT, AuditWriter(self.audit_path),
        )
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("catalog_digest", context)
        self.assertIn("systematic-debugging", context)
        self.assertNotIn("# Systematic Debugging", context)

    async def test_mcp_load_returns_text_and_structured_metadata(self):
        runtime = HwskillMcpRuntime(self.project, ROOT, AuditWriter(self.audit_path))
        result = await runtime.load("superpowers/systematic-debugging")
        self.assertIn("x-hwskill-runtime", result.text)
        self.assertTrue(result.structured["skill_dir"].endswith("systematic-debugging"))
        search = await runtime.search("debug failing test")
        self.assertEqual(search["results"][0]["skill_id"], "superpowers/systematic-debugging")


if __name__ == "__main__":
    unittest.main()
