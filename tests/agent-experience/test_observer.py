from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
OBSERVER = REPO_ROOT / "tests/agent-experience/observer.py"
RUNNER = REPO_ROOT / "scripts/validation/run_clean_luna_eval.sh"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [os.environ.get("PYTHON", "/usr/bin/python3"), str(OBSERVER), *(str(value) for value in arguments)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO_ROOT / "src")},
        )

    def _fixture(self) -> dict[str, Path | str]:
        project = self.root / "project"
        project.mkdir()
        (project / "public/reference-skill").mkdir(parents=True)
        (project / "public/reference-skill/SKILL.md").write_text("# reviewed\n", encoding="utf-8")
        result_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["result", "source_identity"],
            "properties": {
                "result": {"const": "pass"},
                "source_identity": {
                    "type": "object",
                    "required": ["resolved_revision"],
                    "properties": {"resolved_revision": {"type": "string"}},
                },
            },
        }
        _write_json(project / "schemas/result.schema.json", result_schema)

        checkout = project / "checkouts/upstream"
        checkout.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "config", "user.email", "observer@example.test"], check=True)
        subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Observer Fixture"], check=True)
        (checkout / "SKILL.md").write_text("# upstream\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(checkout), "add", "SKILL.md"], check=True)
        subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True)
        revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(
            ["git", "-C", str(checkout), "remote", "add", "origin", "https://example.test/upstream.git"],
            check=True,
        )
        _write_json(
            project / "public/install.json",
            {
                "source": {
                    "kind": "external",
                    "repository": "https://example.test/upstream.git",
                    "requested_ref": revision,
                }
            },
        )

        host = self.root / "bin/codex"
        host.parent.mkdir()
        host.write_text("#!/bin/sh\necho 'codex-cli 9.8.7'\n", encoding="utf-8")
        host.chmod(0o755)
        host_stat = host.stat()
        run_metadata = self.root / "run.json"
        _write_json(
            run_metadata,
            {
                "schema_version": 1,
                "host": "codex",
                "host_version": "9.8.7",
                "host_executable": str(host),
                "host_identity": {"device": host_stat.st_dev, "inode": host_stat.st_ino},
                "model": "gpt-5.6-luna",
                "reasoning_effort": "medium",
                "invocation": [
                    str(host),
                    "exec",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--model",
                    "gpt-5.6-luna",
                    "-c",
                    'model_reasoning_effort="medium"',
                    "-c",
                    'shell_environment_policy.inherit="none"',
                ],
                "started_at": "2026-09-11T00:00:00Z",
                "completed_at": "2026-09-11T00:00:01Z",
                "exit_code": 0,
                "metrics": {
                    "tool_calls": 3,
                    "human_interventions": 0,
                    "duration_ms": 1000,
                    "token_usage": 123,
                },
            },
        )
        task = self.root / "task.json"
        _write_json(
            task,
            {
                "schema_version": 1,
                "id": "fixture-external-install",
                "workflow": "external_install",
                "tuning": "holdout",
                "prompt": "安装公开 external 技能并留下机器结果。",
                "public_inputs": ["public/install.json", "public/reference-skill/SKILL.md"],
                "allowed_changes": ["installed/**", "results/**"],
                "required_paths": [
                    {"path": "installed/example/SKILL.md", "kind": "file"},
                    {"path": "results/result.json", "kind": "file"},
                ],
                "schema_checks": [{"path": "results/result.json", "schema": "schemas/result.schema.json"}],
                "directory_checks": [{"actual": "installed/example", "expected": "public/reference-skill"}],
                "repository_validation": False,
                "host_expectation": {
                    "host": "codex",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium",
                },
                "source_revision_checks": [
                    {
                        "checkout": "checkouts/upstream",
                        "install": "public/install.json",
                        "report": "results/result.json",
                    }
                ],
                "json_outputs": [
                    {
                        "path": "results/result.json",
                        "required_values": [
                            {"pointer": "/result", "equals": "pass"},
                            {"pointer": "/source_identity/resolved_revision", "equals": revision},
                        ],
                    }
                ],
                "yaml_outputs": [],
            },
        )
        return {
            "project": project,
            "task": task,
            "run_metadata": run_metadata,
            "revision": revision,
        }

    def test_observer_passes_only_from_deterministic_artifacts(self) -> None:
        fixture = self._fixture()
        baseline = self.root / "baseline.json"
        snapshot = self.invoke("snapshot", "--workspace", fixture["project"], "--output", baseline)
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)

        project = Path(fixture["project"])
        (project / "installed/example").mkdir(parents=True)
        (project / "installed/example/SKILL.md").write_text("# reviewed\n", encoding="utf-8")
        _write_json(
            project / "results/result.json",
            {"result": "pass", "source_identity": {"resolved_revision": fixture["revision"]}},
        )
        output = self.root / "observation.json"
        observed = self.invoke(
            "observe",
            "--task",
            fixture["task"],
            "--workspace",
            project,
            "--baseline",
            baseline,
            "--run-metadata",
            fixture["run_metadata"],
            "--output",
            output,
        )
        self.assertEqual(observed.returncode, 0, observed.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "pass")
        self.assertTrue(all(check["status"] == "pass" for check in result["checks"]))

    def test_agent_success_claim_cannot_hide_an_out_of_scope_change(self) -> None:
        fixture = self._fixture()
        baseline = self.root / "baseline.json"
        self.assertEqual(
            self.invoke("snapshot", "--workspace", fixture["project"], "--output", baseline).returncode,
            0,
        )
        project = Path(fixture["project"])
        (project / "installed/example").mkdir(parents=True)
        (project / "installed/example/SKILL.md").write_text("# reviewed\n", encoding="utf-8")
        _write_json(
            project / "results/result.json",
            {
                "result": "pass",
                "source_identity": {"resolved_revision": fixture["revision"]},
                "agent_claim": "I succeeded",
            },
        )
        (project / "public/install.json").write_text("{}\n", encoding="utf-8")
        output = self.root / "observation.json"
        observed = self.invoke(
            "observe",
            "--task",
            fixture["task"],
            "--workspace",
            project,
            "--baseline",
            baseline,
            "--run-metadata",
            fixture["run_metadata"],
            "--output",
            output,
        )
        self.assertEqual(observed.returncode, 1)
        result = json.loads(output.read_text(encoding="utf-8"))
        scope = next(check for check in result["checks"] if check["category"] == "file_scope")
        self.assertEqual(scope["status"], "fail")
        self.assertIn("public/install.json", scope["evidence"]["unexpected_changes"])

    def test_symlink_and_incomplete_directory_are_observer_failures(self) -> None:
        fixture = self._fixture()
        baseline = self.root / "baseline.json"
        self.assertEqual(
            self.invoke("snapshot", "--workspace", fixture["project"], "--output", baseline).returncode,
            0,
        )
        project = Path(fixture["project"])
        (project / "installed/example").mkdir(parents=True)
        (project / "installed/example/SKILL.md").symlink_to(project / "public/reference-skill/SKILL.md")
        _write_json(
            project / "results/result.json",
            {"result": "pass", "source_identity": {"resolved_revision": fixture["revision"]}},
        )
        output = self.root / "observation.json"
        observed = self.invoke(
            "observe",
            "--task",
            fixture["task"],
            "--workspace",
            project,
            "--baseline",
            baseline,
            "--run-metadata",
            fixture["run_metadata"],
            "--output",
            output,
        )
        self.assertEqual(observed.returncode, 1)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(any(check["status"] == "fail" for check in result["checks"]))

    def test_host_and_revision_are_measured_not_taken_from_agent_text(self) -> None:
        fixture = self._fixture()
        baseline = self.root / "baseline.json"
        self.assertEqual(
            self.invoke("snapshot", "--workspace", fixture["project"], "--output", baseline).returncode,
            0,
        )
        project = Path(fixture["project"])
        (project / "installed/example").mkdir(parents=True)
        (project / "installed/example/SKILL.md").write_text("# reviewed\n", encoding="utf-8")
        _write_json(
            project / "results/result.json",
            {"result": "pass", "source_identity": {"resolved_revision": "0" * 40}},
        )
        run_metadata = json.loads(Path(fixture["run_metadata"]).read_text(encoding="utf-8"))
        run_metadata["model"] = "gpt-6-astra"
        _write_json(Path(fixture["run_metadata"]), run_metadata)
        output = self.root / "observation.json"
        observed = self.invoke(
            "observe",
            "--task",
            fixture["task"],
            "--workspace",
            project,
            "--baseline",
            baseline,
            "--run-metadata",
            fixture["run_metadata"],
            "--output",
            output,
        )
        self.assertEqual(observed.returncode, 1)
        result = json.loads(output.read_text(encoding="utf-8"))
        failures = {check["category"] for check in result["checks"] if check["status"] == "fail"}
        self.assertIn("host_identity", failures)
        self.assertIn("source_revision", failures)

    def test_source_check_disables_repository_fsmonitor_and_still_detects_dirty_files(self) -> None:
        fixture = self._fixture()
        project = Path(fixture["project"])
        checkout = project / "checkouts/upstream"
        marker = self.root / "fsmonitor-ran"
        monitor = self.root / "malicious-fsmonitor"
        monitor.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf '0\\n'\n", encoding="utf-8")
        monitor.chmod(0o755)
        subprocess.run(["/usr/bin/git", "-C", str(checkout), "config", "core.fsmonitor", str(monitor)], check=True)
        baseline = self.root / "baseline.json"
        self.assertEqual(self.invoke("snapshot", "--workspace", project, "--output", baseline).returncode, 0)
        (checkout / "SKILL.md").write_text("# hidden dirty change\n", encoding="utf-8")
        (project / "installed/example").mkdir(parents=True)
        (project / "installed/example/SKILL.md").write_text("# reviewed\n", encoding="utf-8")
        _write_json(
            project / "results/result.json",
            {"result": "pass", "source_identity": {"resolved_revision": fixture["revision"]}},
        )
        output = self.root / "observation.json"
        observed = self.invoke(
            "observe",
            "--task",
            fixture["task"],
            "--workspace",
            project,
            "--baseline",
            baseline,
            "--run-metadata",
            fixture["run_metadata"],
            "--output",
            output,
        )
        self.assertEqual(observed.returncode, 1, observed.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        source = next(check for check in result["checks"] if check["category"] == "source_revision")
        self.assertEqual(source["status"], "fail")
        self.assertIn("not clean", source["reason"])
        self.assertFalse(marker.exists())

    def test_yaml_business_values_are_checked_independently(self) -> None:
        fixture = self._fixture()
        task = json.loads(Path(fixture["task"]).read_text(encoding="utf-8"))
        task["yaml_outputs"] = [
            {
                "path": "results/contribution.yaml",
                "required_values": [{"pointer": "/id", "equals": "local/expected"}],
            }
        ]
        _write_json(Path(fixture["task"]), task)
        baseline = self.root / "baseline.json"
        self.assertEqual(
            self.invoke("snapshot", "--workspace", fixture["project"], "--output", baseline).returncode,
            0,
        )
        project = Path(fixture["project"])
        (project / "installed/example").mkdir(parents=True)
        (project / "installed/example/SKILL.md").write_text("# reviewed\n", encoding="utf-8")
        _write_json(
            project / "results/result.json",
            {"result": "pass", "source_identity": {"resolved_revision": fixture["revision"]}},
        )
        (project / "results/contribution.yaml").write_text("id: local/wrong\n", encoding="utf-8")
        output = self.root / "observation.json"
        observed = self.invoke(
            "observe",
            "--task",
            fixture["task"],
            "--workspace",
            project,
            "--baseline",
            baseline,
            "--run-metadata",
            fixture["run_metadata"],
            "--output",
            output,
        )
        self.assertEqual(observed.returncode, 1, observed.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        yaml_check = next(check for check in result["checks"] if check["name"] == "machine YAML values")
        self.assertEqual(yaml_check["status"], "fail")

    def test_no_luna_run_emits_a_schema_valid_blocked_record(self) -> None:
        output = self.root / "blocked.json"
        isolation = self.root / "isolation.json"
        _write_json(
            isolation,
            {
                "temporary_home": True,
                "temporary_config_root": True,
                "temporary_project": True,
                "inherited_skills": False,
                "inherited_profile": False,
                "inherited_hooks": False,
                "inherited_mcp": False,
                "public_inputs_only": True,
                "minimal_authentication": True,
            },
        )
        blocked = self.invoke(
            "blocked",
            "--tasks-dir",
            REPO_ROOT / "tests/agent-experience/tasks",
            "--repo-root",
            REPO_ROOT,
            "--isolation-metadata",
            isolation,
            "--reason",
            "Luna execution was not requested and no credential was used",
            "--output",
            output,
        )
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        validated = self.invoke("validate-result", "--input", output)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["tasks"])
        self.assertTrue(any(task["tuning"] == "holdout" for task in result["tasks"]))
        self.assertTrue(all(task["status"] == "not_run" for task in result["tasks"]))
        self.assertTrue(all(task["metrics"]["attempts"] == 0 for task in result["tasks"]))
        self.assertTrue(all(task["metrics"]["success_rate"] is None for task in result["tasks"]))
        self.assertTrue(all(task["metrics"]["token_usage"] is None for task in result["tasks"]))

    def test_record_run_keeps_obtainable_tool_and_token_metrics(self) -> None:
        transcript = self.root / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}),
                    json.dumps(
                        {
                            "type": "turn.completed",
                            "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 25},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        host = self.root / "codex"
        host.write_text("#!/bin/sh\necho 'codex-cli 9.8.7'\n", encoding="utf-8")
        host.chmod(0o755)
        output = self.root / "run.json"
        recorded = self.invoke(
            "record-run",
            "--transcript",
            transcript,
            "--host-executable",
            host,
            "--started-at",
            "2026-09-11T00:00:00Z",
            "--completed-at",
            "2026-09-11T00:00:02Z",
            "--exit-code",
            "0",
            "--invocation-argument=codex",
            "--output",
            output,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        metrics = json.loads(output.read_text(encoding="utf-8"))["metrics"]
        self.assertEqual(metrics["tool_calls"], 1)
        self.assertEqual(metrics["duration_ms"], 2000)
        self.assertEqual(metrics["token_usage"], 125)


class CleanRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(
        self,
        *arguments: str,
        credential: str | None = None,
        host_body: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        binary_dir = self.root / "bin"
        binary_dir.mkdir(exist_ok=True)
        marker = self.root / "host-was-run"
        fake_codex = binary_dir / "codex"
        fake_codex.write_text(host_body or f"#!/bin/sh\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        environment = {
            "PATH": f"{binary_dir}:/usr/bin:/bin",
            "HOME": str(self.root / "sensitive-home"),
            "CODEX_HOME": str(self.root / "sensitive-codex"),
            "XDG_CONFIG_HOME": str(self.root / "sensitive-config"),
            "TMPDIR": str(self.root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        (self.root / "tmp").mkdir()
        if credential is not None:
            environment["CODEX_API_KEY"] = credential
        return subprocess.run(
            ["/bin/sh", str(RUNNER), *arguments],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def test_default_mode_records_blocked_and_cleans_isolated_roots(self) -> None:
        output = self.root / "blocked.json"
        completed = self.run_script("--output", str(output), credential="must-not-be-used")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertFalse((self.root / "host-was-run").exists())
        self.assertEqual(list((self.root / "tmp").iterdir()), [])
        self.assertTrue(output.is_file(), completed.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("not requested", result["reason"])
        self.assertTrue(result["isolation"]["temporary_home"])
        self.assertFalse(result["isolation"]["inherited_mcp"])

    def test_execute_mode_without_minimal_api_key_never_starts_host(self) -> None:
        output = self.root / "no-credential.json"
        completed = self.run_script("--execute", "--output", str(output))
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertFalse((self.root / "host-was-run").exists())
        self.assertTrue(output.is_file(), completed.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("CODEX_API_KEY", result["reason"])

    def test_execute_mode_never_trusts_a_path_injected_codex_with_a_credential(self) -> None:
        output = self.root / "untrusted-host.json"
        completed = self.run_script("--execute", "--output", str(output), credential="real-looking-secret")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertFalse((self.root / "host-was-run").exists())
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["tasks"]), 5)
        self.assertEqual(len({task["task_id"] for task in result["tasks"]}), 5)

    def test_runner_does_not_use_an_unverified_python_from_path(self) -> None:
        output = self.root / "blocked.json"
        poisoned = self.root / "poisoned-python"
        fake_python = self.root / "bin/python3"
        fake_python.parent.mkdir()
        fake_python.write_text(f"#!/bin/sh\ntouch '{poisoned}'\nexit 88\n", encoding="utf-8")
        fake_python.chmod(0o755)
        completed = self.run_script("--output", str(output))
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertFalse(poisoned.exists())
        self.assertTrue(output.is_file(), completed.stderr)

    def test_production_runner_has_no_fake_host_override_and_pins_invocation(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn("HWSKILL_LUNA_TEST", body)
        self.assertNotIn("command -v codex", body)
        self.assertIn("/proc/self/fd/9 exec", body)
        self.assertIn("--model gpt-5.6-luna", body)
        self.assertIn('model_reasoning_effort="medium"', body)
        self.assertIn('shell_environment_policy.inherit="none"', body)
        self.assertIn("--ignore-user-config", body)
        self.assertIn("--ignore-rules", body)
        self.assertIn("--ephemeral", body)

    def test_record_run_uses_the_already_verified_open_host_inode(self) -> None:
        host = self.root / "trusted-test-host"
        host.write_text("#!/bin/sh\necho 'codex-cli 9.8.7'\n", encoding="utf-8")
        host.chmod(0o755)
        proof = self.root / "host-proof.json"
        probed = ObserverTests.invoke(
            self,
            "probe-host",
            "--host-executable",
            host,
            "--allow-test-host",
            "--output",
            proof,
        )
        self.assertEqual(probed.returncode, 0, probed.stderr)
        descriptor = os.open(host, os.O_RDONLY)
        try:
            original = self.root / "original-host"
            host.rename(original)
            host.write_text("#!/bin/sh\necho 'codex-cli 1.0.0'\n", encoding="utf-8")
            host.chmod(0o755)
            transcript = self.root / "transcript.jsonl"
            transcript.write_text("", encoding="utf-8")
            output = self.root / "run.json"
            recorded = subprocess.run(
                [
                    "/usr/bin/python3",
                    str(OBSERVER),
                    "record-run",
                    "--transcript",
                    str(transcript),
                    "--host-executable",
                    str(host),
                    "--host-fd",
                    str(descriptor),
                    "--host-proof",
                    str(proof),
                    "--started-at",
                    "2026-09-11T00:00:00Z",
                    "--completed-at",
                    "2026-09-11T00:00:01Z",
                    "--exit-code",
                    "0",
                    "--invocation-argument=codex",
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
                pass_fds=(descriptor,),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO_ROOT / "src")},
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            metadata = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(metadata["host_version"], "9.8.7")
            self.assertEqual(metadata["host_identity"]["inode"], os.fstat(descriptor).st_ino)
            self.assertNotEqual(metadata["host_identity"]["inode"], host.stat().st_ino)
        finally:
            os.close(descriptor)

    def test_prepare_task_exposes_prompt_and_public_inputs_but_not_oracles(self) -> None:
        destination = self.root / "sanitised"
        task = REPO_ROOT / "tests/agent-experience/tasks/hosted-install-holdout.json"
        prepared = ObserverTests.invoke(
            self,
            "prepare-task",
            "--repo-root",
            REPO_ROOT,
            "--task",
            task,
            "--destination",
            destination,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        package = json.loads((destination / ".evaluation/task.json").read_text(encoding="utf-8"))
        self.assertEqual(set(package), {"schema_version", "id", "workflow", "prompt", "public_inputs"})
        self.assertNotIn("allowed_changes", package)
        self.assertFalse((destination / "tests/agent-experience/tasks").exists())
        self.assertTrue((destination / "site/.generated/directory/catalog.json").is_file())

    def test_freeze_workspace_rejects_symlinks_and_detaches_observation_bytes(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "answer.json").write_text('{"answer": 1}\n', encoding="utf-8")
        frozen = self.root / "frozen"
        completed = ObserverTests.invoke(
            self,
            "freeze-workspace",
            "--workspace",
            source,
            "--destination",
            frozen,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        (source / "answer.json").write_text('{"answer": 2}\n', encoding="utf-8")
        self.assertEqual(json.loads((frozen / "answer.json").read_text(encoding="utf-8"))["answer"], 1)

        unsafe = self.root / "unsafe"
        unsafe.mkdir()
        (unsafe / "leak").symlink_to(source / "answer.json")
        rejected = ObserverTests.invoke(
            self,
            "freeze-workspace",
            "--workspace",
            unsafe,
            "--destination",
            self.root / "unsafe-frozen",
        )
        self.assertEqual(rejected.returncode, 3)

    def test_aggregate_rejects_an_incomplete_task_set(self) -> None:
        observations = self.root / "observations"
        observations.mkdir()
        task_paths = sorted(
            path
            for path in (REPO_ROOT / "tests/agent-experience/tasks").glob("*.json")
            if not path.name.endswith(".schema.json")
        )
        no_metrics = {
            "attempts": 0,
            "success_rate": None,
            "tool_calls": None,
            "human_interventions": None,
            "duration_ms": None,
            "token_usage": None,
        }
        for task_path in task_paths[:-1]:
            task = json.loads(task_path.read_text(encoding="utf-8"))
            _write_json(
                observations / f"{task['id']}.json",
                {
                    "schema_version": 1,
                    "task_id": task["id"],
                    "workflow": task["workflow"],
                    "tuning": task["tuning"],
                    "status": "not_run",
                    "observed_at": "2026-09-11T00:00:00Z",
                    "checks": [
                        {
                            "name": "not run",
                            "category": "task_definition",
                            "status": "not_run",
                            "reason": "fixture",
                            "evidence": {},
                        }
                    ],
                    "metrics": no_metrics,
                },
            )
        isolation = self.root / "isolation.json"
        _write_json(
            isolation,
            {
                "temporary_home": True,
                "temporary_config_root": True,
                "temporary_project": True,
                "inherited_skills": False,
                "inherited_profile": False,
                "inherited_hooks": False,
                "inherited_mcp": False,
                "public_inputs_only": True,
                "minimal_authentication": True,
            },
        )
        aggregated = ObserverTests.invoke(
            self,
            "aggregate",
            "--observations-dir",
            observations,
            "--isolation-metadata",
            isolation,
            "--tasks-dir",
            REPO_ROOT / "tests/agent-experience/tasks",
            "--output",
            self.root / "evaluation.json",
        )
        self.assertEqual(aggregated.returncode, 3)
        self.assertIn("does not cover", aggregated.stderr)


if __name__ == "__main__":
    unittest.main()
