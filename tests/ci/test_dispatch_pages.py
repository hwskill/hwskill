import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/dispatch_pages.py"


class DispatchPagesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        self.remote = self.root / "remote.git"
        self.git("init", "-q", "-b", "main", str(self.source))
        self.git("-C", str(self.source), "config", "user.name", "CI Test")
        self.git("-C", str(self.source), "config", "user.email", "ci@example.invalid")
        self.git("-C", str(self.source), "commit", "-q", "--allow-empty", "-m", "first")
        self.first_sha = self.git("-C", str(self.source), "rev-parse", "HEAD").stdout.strip()
        self.git("clone", "-q", "--bare", str(self.source), str(self.remote))
        spec = importlib.util.spec_from_file_location("dispatch_pages", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    @staticmethod
    def git(*args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True)

    def run_script(self, sha, token="test-secret"):
        stdout, stderr = io.StringIO(), io.StringIO()
        env = {"PAGES_DISPATCH_TOKEN": token} if token is not None else {}
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"html_url":"https://github.com/hwskill/hwskill.github.io/actions/runs/123"}'
        response.__enter__.return_value = response
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            self.module.urllib.request, "urlopen", return_value=response
        ) as post, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.module.main([
                sha, "--source-remote", str(self.remote), "--api-url", "https://example.invalid/dispatches"
            ])
        return code, stdout.getvalue(), stderr.getvalue(), post

    def test_latest_commit_dispatches_automatic_release(self):
        code, out, err, post = self.run_script(self.first_sha)
        self.assertEqual(code, 0, err)
        request = post.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/dispatches")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(json.loads(request.data), {
            "ref": "main", "inputs": {"source_sha": self.first_sha, "release_mode": "automatic"}
        })
        self.assertIn("/actions/runs/123", out)
        self.assertNotIn("test-secret", out + err)

    def test_obsolete_commit_skips_without_token(self):
        self.git("-C", str(self.source), "commit", "-q", "--allow-empty", "-m", "second")
        self.git("-C", str(self.source), "-c", "http.lowSpeedLimit=1", "-c", "http.lowSpeedTime=10", "push", "-q", str(self.remote), "HEAD:main")
        code, out, err, post = self.run_script(self.first_sha, token=None)
        self.assertEqual(code, 0, err)
        self.assertIn("obsolete", out)
        post.assert_not_called()

    def test_current_commit_without_token_fails(self):
        code, _, err, post = self.run_script(self.first_sha, token=None)
        self.assertNotEqual(code, 0)
        self.assertIn("PAGES_DISPATCH_TOKEN", err)
        post.assert_not_called()

    def test_http_rejection_fails_without_leaking_token(self):
        with mock.patch.dict(os.environ, {"PAGES_DISPATCH_TOKEN": "test-secret"}, clear=True), mock.patch.object(
            self.module.urllib.request, "urlopen", side_effect=HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
        ), contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = self.module.main([self.first_sha, "--source-remote", str(self.remote), "--api-url", "https://example.invalid/dispatches"])
        self.assertNotEqual(code, 0)
        self.assertIn("403", stderr.getvalue())
        self.assertNotIn("test-secret", stderr.getvalue())

    def test_invalid_sha_is_rejected_before_request(self):
        code, _, _, post = self.run_script("not-a-sha")
        self.assertNotEqual(code, 0)
        post.assert_not_called()
