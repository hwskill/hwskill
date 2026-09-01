from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


class DockerTestRunnerCommandTests(unittest.TestCase):
    def test_standard_image_definition_is_pinned_and_copies_only_declared_sources(self) -> None:
        dockerfile = (ROOT / "docker/test/Dockerfile").read_text(encoding="utf-8")

        self.assertRegex(dockerfile.splitlines()[0], r"^FROM node:[^ ]+@sha256:[0-9a-f]{64}$")
        self.assertIn("@openai/codex@0.147.0", dockerfile)
        self.assertIn("@anthropic-ai/claude-code@2.1.141", dockerfile)
        self.assertIn("opencode-ai@1.14.48", dockerfile)
        self.assertNotIn("COPY .", dockerfile)
        self.assertNotRegex(dockerfile, r"(?im)^COPY .*\.(?:codex|claude)|^COPY .*home|^COPY .*credentials")
        self.assertIn('org.hwskill.test.schema="1"', dockerfile)
        self.assertIn('org.opencontainers.image.version="0.1.0"', dockerfile)
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for sensitive in (".codex", ".claude", ".config", ".local", "credentials", "*.pem", "*.key", "*.iid"):
            self.assertIn(sensitive, ignored)

    def test_python_dependency_lock_contains_only_exact_non_editable_requirements(self) -> None:
        lines = [
            line.strip() for line in (ROOT / "docker/test/requirements.lock").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertIn("PyYAML==6.0.3", lines)
        self.assertIn("mcp==1.29.1", lines)
        self.assertTrue(all("==" in line and not line.startswith(("-e", "git+", "http:")) for line in lines))

    def test_entrypoint_verifies_fixed_cli_versions_before_worker(self) -> None:
        entrypoint = (ROOT / "docker/test/entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('codex-cli 0.147.0', entrypoint)
        self.assertIn('2.1.141 (Claude Code)', entrypoint)
        self.assertIn('1.14.48', entrypoint)
        self.assertIn('exec "$@"', entrypoint)

    def test_documented_worker_smoke_uses_the_production_runtime_environment(self) -> None:
        readme = (ROOT / "docker/test/README.md").read_text(encoding="utf-8")

        self.assertIn('--env HOME=/workspace/home', readme)
        self.assertIn('--env HWSKILL_REGISTRY_ROOT=/registry', readme)
        self.assertIn('--pids-limit 512', readme)

    def test_docker_run_mounts_registry_read_only_and_artifacts_writable_without_secrets(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner, DockerTestRequest, ImageInfo
        from hwskill.test_configuration import HostModel, TestConfiguration

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            artifacts = root / "artifacts"
            workspace = root / "workspace"
            request = root / "request.json"
            for path in (repo / "tests", artifacts, workspace):
                path.mkdir(parents=True)
            request.write_text("{}", encoding="utf-8")
            config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
            runner = DockerTestRunner(repo, config, image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "a" * 64))
            value = DockerTestRequest(request, artifacts, workspace, "none", (("CODEX_API_KEY", "credential-sentinel"),))

            argv = runner.build_run_command(value)

            joined = " ".join(argv)
            self.assertIn(f"src={repo},dst=/registry,readonly", joined)
            self.assertIn(f"src={repo / 'tests'},dst=/tests,readonly", joined)
            self.assertIn(f"src={artifacts},dst=/artifacts", joined)
            self.assertIn(f"src={workspace},dst=/workspace", joined)
            self.assertIn("--network none", joined)
            self.assertIn("--env CODEX_API_KEY", joined)
            self.assertNotIn("credential-sentinel", joined)
            self.assertIn("--cap-drop ALL", joined)
            self.assertIn("no-new-privileges", joined)
            self.assertNotIn("docker.sock", joined)

    def test_agent_mode_uses_controlled_default_network_and_fixed_login_mount(self) -> None:
        from hwskill.docker_test_runner import CredentialFile, DockerTestRunner, DockerTestRequest, ImageInfo
        from hwskill.test_configuration import HostModel, TestConfiguration

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            auth = root / "auth.json"
            for path in (repo / "tests", root / "artifacts", root / "workspace"):
                path.mkdir(parents=True)
            auth.write_text("secret file", encoding="utf-8")
            request_path = root / "request.json"
            request_path.write_text("{}", encoding="utf-8")
            config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
            runner = DockerTestRunner(repo, config, image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "b" * 64), uid=123, gid=456)
            request = DockerTestRequest(
                request_path, root / "artifacts", root / "workspace", "agent", (),
                (CredentialFile(auth, "/credentials/codex/auth.json"),),
            )

            argv = runner.build_run_command(request)
            joined = " ".join(argv)

            self.assertIn("--network bridge", joined)
            self.assertIn("--user 123:456", joined)
            self.assertIn(f"src={auth},dst=/credentials/codex/auth.json,readonly", joined)
            self.assertNotIn("secret file", joined)
            self.assertNotIn("--privileged", argv)

    def test_claude_host_store_preserves_hidden_credentials_filename(self) -> None:
        from hwskill.test_cli import _environment_credential_material

        with TemporaryDirectory() as directory:
            home = Path(directory)
            store = home / ".claude" / ".credentials.json"
            store.parent.mkdir()
            store.write_text('{"token":"secret"}', encoding="utf-8")

            material = _environment_credential_material("claude-code", {}, home=home)

        self.assertEqual(material.credential_files[0].source, store)
        self.assertEqual(
            material.credential_files[0].destination,
            "/credentials/claude-code/.credentials.json",
        )

    def test_mount_rejects_comma_in_repository_or_credential_source(self) -> None:
        from hwskill.docker_test_runner import (
            CredentialFile, DockerRunnerUnavailable, DockerTestRequest, DockerTestRunner, ImageInfo,
        )
        from hwskill.test_configuration import HostModel, TestConfiguration

        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
            identity = ImageInfo("hwskill-test:0.1.0", "sha256:" + "b" * 64)
            request_path = root / "request.json"
            request_path.write_text("{}", encoding="utf-8")
            for path in (root / "artifacts", root / "workspace"):
                path.mkdir()

            comma_repo = root / "repo,ambiguous"
            (comma_repo / "tests").mkdir(parents=True)
            runner = DockerTestRunner(comma_repo, config, image=identity)
            request = DockerTestRequest(request_path, root / "artifacts", root / "workspace", "none")
            with self.assertRaisesRegex(DockerRunnerUnavailable, "mount path"):
                runner.build_run_command(request)

            repo = root / "repo"
            (repo / "tests").mkdir(parents=True)
            comma_auth = root / "auth,ambiguous.json"
            comma_auth.write_text("secret", encoding="utf-8")
            runner = DockerTestRunner(repo, config, image=identity)
            request = DockerTestRequest(
                request_path, root / "artifacts", root / "workspace", "agent", (),
                (CredentialFile(comma_auth, "/credentials/codex/auth.json"),),
            )
            with self.assertRaisesRegex(DockerRunnerUnavailable, "mount path"):
                runner.build_run_command(request)

    def test_mount_rejects_control_characters_in_source_and_destination(self) -> None:
        from hwskill.docker_test_runner import DockerRunnerUnavailable, _mount

        for source in (Path("line\nbreak"), Path("tab\tpath"), Path("nul\x00path")):
            with self.subTest(source=repr(str(source))):
                with self.assertRaisesRegex(DockerRunnerUnavailable, "mount path"):
                    _mount(source, "/registry")
        with self.assertRaisesRegex(DockerRunnerUnavailable, "mount path"):
            _mount(Path("safe"), "/bad,destination")

    def test_build_uses_fixed_context_labels_and_returns_verified_digest(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner
        from hwskill.test_configuration import HostModel, TestConfiguration

        calls = []

        def execute(argv, **kwargs):
            calls.append(tuple(argv))
            if argv[:2] == ("docker", "build"):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(
                argv, 0,
                json.dumps([{
                    "Id": "sha256:" + "c" * 64,
                    "Config": {"Labels": {
                        "org.hwskill.test.schema": "1",
                        "org.opencontainers.image.version": "0.1.0",
                    }},
                }]), "",
            )

        config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
        runner = DockerTestRunner(Path.cwd(), config, command_runner=execute)

        identity = runner.build()

        self.assertEqual(identity.digest, "sha256:" + "c" * 64)
        build = calls[0]
        self.assertEqual(build[:2], ("docker", "build"))
        self.assertTrue(build[build.index("--file") + 1].endswith("/docker/test/Dockerfile"))
        self.assertEqual(build[-1], str(Path.cwd().resolve()))

    def test_inspect_rejects_wrong_image_label_without_building(self) -> None:
        from hwskill.docker_test_runner import DockerRunnerUnavailable, DockerTestRunner
        from hwskill.test_configuration import HostModel, TestConfiguration

        calls = []

        def execute(argv, **kwargs):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(
                argv, 0,
                json.dumps([{"Id": "sha256:" + "d" * 64, "Config": {"Labels": {"org.hwskill.test.schema": "0"}}}]), "",
            )

        config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
        runner = DockerTestRunner(Path.cwd(), config, command_runner=execute)

        with self.assertRaises(DockerRunnerUnavailable):
            runner.inspect_image()
        self.assertFalse(any(call[:2] == ("docker", "build") for call in calls))

    def test_docker_client_keeps_only_connection_configuration_from_the_host(self) -> None:
        from hwskill.docker_test_runner import _docker_client_environment

        with patch.dict(os.environ, {
            "DOCKER_CONFIG": "/tmp/docker-config", "DOCKER_HOST": "unix:///run/docker.sock",
            "UNRELATED_SECRET": "must-not-forward",
        }, clear=False):
            environment = _docker_client_environment()

        self.assertEqual(environment["DOCKER_CONFIG"], "/tmp/docker-config")
        self.assertEqual(environment["DOCKER_HOST"], "unix:///run/docker.sock")
        self.assertNotIn("UNRELATED_SECRET", environment)


class WorkerRequestValidationTests(unittest.TestCase):
    def _request(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "selections": [{"kind": "core", "path": "tests/core"}],
            "host": "codex",
            "model": "gpt-5.6-terra",
            "reasoning": "high",
            "timeout_seconds": 30,
            "credential_environment": ["CODEX_API_KEY"],
        }

    def test_worker_accepts_strict_request_without_host_paths_or_secret_values(self) -> None:
        from hwskill.test_worker import load_worker_request

        with TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(self._request()), encoding="utf-8")
            request = load_worker_request(path)

        self.assertEqual(request.selections[0].path, "tests/core")
        self.assertEqual(request.credential_environment, ("CODEX_API_KEY",))
        self.assertNotIn("credential-sentinel", repr(request))

    def test_worker_rejects_duplicate_json_keys(self) -> None:
        from hwskill.test_worker import WorkerRequestError, load_worker_request

        with TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(WorkerRequestError, "duplicate"):
                load_worker_request(path)

    def test_worker_rejects_unknown_keys_and_path_traversal(self) -> None:
        from hwskill.test_worker import WorkerRequestError, load_worker_request

        for mutation in ("unknown", "traversal"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                payload = self._request()
                if mutation == "unknown":
                    payload["repository"] = "/host/private"
                else:
                    payload["selections"] = [{"kind": "core", "path": "tests/core/../secret.py"}]
                path = Path(directory) / "request.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(WorkerRequestError):
                    load_worker_request(path)

    def test_worker_rejects_symlink_request_and_oversized_request(self) -> None:
        from hwskill.test_worker import WorkerRequestError, load_worker_request

        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(json.dumps(self._request()), encoding="utf-8")
            link = root / "request.json"
            link.symlink_to(target)
            with self.assertRaises(WorkerRequestError):
                load_worker_request(link)
            large = root / "large.json"
            large.write_bytes(b" " * (64 * 1024 + 1))
            with self.assertRaisesRegex(WorkerRequestError, "maximum"):
                load_worker_request(large)

    def test_worker_rejects_unapproved_credential_environment_name(self) -> None:
        from hwskill.test_worker import WorkerRequestError, load_worker_request

        payload = self._request()
        payload["credential_environment"] = ["PATH"]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(WorkerRequestError):
                load_worker_request(path)


class DockerExecutionTests(unittest.TestCase):
    def _config(self):
        from hwskill.test_configuration import HostModel, TestConfiguration

        return TestConfiguration("docker", "codex", {"codex": HostModel("gpt-5.6-terra", "high")})

    def test_run_writes_secret_free_request_and_parses_actual_worker_result(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner
        from hwskill.test_runner import TestEnvironment

        captured_request = {}
        captured_argv = []

        def execute(argv, **kwargs):
            captured_argv.append(tuple(argv))
            if argv[:3] == ("docker", "image", "inspect"):
                return subprocess.CompletedProcess(argv, 0, json.dumps([{
                    "Id": "sha256:" + "e" * 64,
                    "Config": {"Labels": {
                        "org.hwskill.test.schema": "1",
                        "org.opencontainers.image.version": "0.1.0",
                    }},
                }]), "")
            mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]
            request_mount = next(value for value in mounts if "dst=/run/request.json" in value)
            request_path = Path(request_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            captured_request.update(json.loads(request_path.read_text(encoding="utf-8")))
            artifact_mount = next(value for value in mounts if "dst=/artifacts" in value)
            artifact_root = Path(artifact_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            (artifact_root / "result.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "PASS",
                "runner": "docker",
                "host": "codex",
                "model": "gpt-5.6-terra",
                "host_version": "0.147.0",
                "blocked_reason": None,
                "collections": [{
                    "kind": "core", "target_id": "core", "status": "PASS",
                    "cases": [{
                        "case_id": "core", "status": "PASS", "artifact_dir": "core/core",
                        "actions": [{
                            "action_id": "unittest", "status": "completed", "exit_code": 0,
                            "artifact_dir": "core/core/actions/unittest",
                        }],
                    }],
                }],
            }), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "hostile credential-sentinel", "")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "tests" / "core").mkdir(parents=True)
            (repo / "docker" / "test").mkdir(parents=True)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            environment = TestEnvironment(
                repo, "docker", "codex", "gpt-5.6-terra", "high",
                secret_values=("credential-sentinel",),
                environment_variables=(("CODEX_API_KEY", "credential-sentinel"),),
            )
            result = DockerTestRunner(repo, self._config(), command_runner=execute).run(
                (Path("tests/core"),), environment, artifacts,
            )

            self.assertEqual(result.status, "PASS")
            self.assertEqual(result.host_version, "0.147.0")
            self.assertEqual(result.collections[0].cases[0].actions[0].artifact_dir, artifacts / "core/core/actions/unittest")
            self.assertEqual(captured_request["credential_environment"], ["CODEX_API_KEY"])
            self.assertNotIn("credential-sentinel", json.dumps(captured_request))
            self.assertNotIn("credential-sentinel", " ".join(captured_argv[-1]))
            self.assertNotIn("credential-sentinel", "".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in artifacts.rglob("*") if path.is_file()
            ))

    def test_run_blocks_without_result_and_never_falls_back_local(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner, ImageInfo
        from hwskill.test_runner import TestEnvironment

        def execute(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 125, "", "daemon unavailable")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "tests" / "core").mkdir(parents=True)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            environment = TestEnvironment(repo, "docker", "codex", "model", "high")
            runner = DockerTestRunner(
                repo, self._config(), image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "f" * 64),
                command_runner=execute,
            )

            result = runner.run((Path("tests/core"),), environment, artifacts)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.collections, ())
        self.assertIn("Docker", result.blocked_reason)

    def test_result_reader_rejects_a_symlink_instead_of_following_it(self) -> None:
        from hwskill.docker_test_runner import DockerRunnerUnavailable, _load_result

        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text(json.dumps({
                "schema_version": 1, "status": "PASS", "runner": "docker", "host": "codex",
                "model": "model", "host_version": "0.147.0", "blocked_reason": None,
                "collections": [],
            }), encoding="utf-8")
            link = root / "result.json"
            link.symlink_to(target)

            with self.assertRaises(DockerRunnerUnavailable):
                _load_result(link, root)

    def test_agent_selection_enables_network_while_command_only_selection_does_not(self) -> None:
        from hwskill.docker_test_runner import selection_network_mode
        from hwskill.test_manifest import AgentAction, CommandAction, TestCase, TestCollection, TestTarget

        command = CommandAction("check", "true")
        command_case = TestCase("case", None, None, None, (command,), command)
        agent = AgentAction("agent", "do work")
        agent_case = TestCase("agent-case", None, None, None, (agent,), command)
        base = dict(manifest_path=Path("tests/skills/ns/name/test.yaml"), target=TestTarget("skill", "ns/name"), fixtures_dir=Path("fixtures"))

        self.assertEqual(selection_network_mode((TestCollection(cases=(command_case,), **base),)), "none")
        self.assertEqual(selection_network_mode((TestCollection(cases=(agent_case,), **base),)), "agent")

    def test_cli_constructs_standard_docker_boundary_instead_of_placeholder(self) -> None:
        from argparse import Namespace
        from io import StringIO
        from hwskill import test_cli

        with TemporaryDirectory() as directory:
            repo = Path(directory)
            manifest = repo / "tests/skills/local/example/test.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                "schema_version: 1\ntarget: {kind: skill, id: local/example}\ncases:\n"
                "  - id: one\n    steps: [{type: command, command: 'true'}]\n"
                "    post_check: {type: command, command: 'true'}\n",
                encoding="utf-8",
            )
            args = Namespace(
                test_target="tests/skills/local/example/test.yaml", test_id=None,
                runner="docker", host=None, base=None, check=False, json=True,
                model=None, reasoning=None,
            )
            boundary = Mock()
            boundary.run.return_value = test_cli.TestRunResult(
                "PASS", repo / "artifacts", (), "docker", "codex", "gpt-5.6-terra", "0.147.0",
            )
            with patch("hwskill.test_cli.load_test_configuration", return_value=self._config()), patch(
                "hwskill.test_cli.DockerTestRunner", return_value=boundary,
            ) as runner_type:
                code = test_cli.run_test_command(args, repo, StringIO(), StringIO())

        self.assertEqual(code, 0)
        runner_type.assert_called_once()
        boundary.run.assert_called_once()

    def test_setup_check_inspects_but_never_builds_image_and_non_check_builds(self) -> None:
        from argparse import Namespace
        from io import StringIO
        from hwskill import test_cli
        from hwskill.docker_test_runner import ImageInfo
        from hwskill.test_setup import SetupCheck, SetupReport

        report = SetupReport("BLOCKED", (SetupCheck("model-availability", "BLOCKED", "unavailable"),))
        image = ImageInfo("hwskill-test:0.1.0", "sha256:" + "1" * 64)
        for check in (True, False):
            with self.subTest(check=check), TemporaryDirectory() as directory:
                repo = Path(directory)
                args = Namespace(
                    test_target="setup", test_id=None, runner="docker", host=None, base=None,
                    check=check, json=True, model=None, reasoning=None,
                )
                docker = Mock()
                docker.inspect_image.return_value = image
                docker.build.return_value = image
                configurator = Mock(return_value=report)
                with patch("hwskill.test_cli.load_test_configuration", return_value=self._config()), patch(
                    "hwskill.test_cli.DockerTestRunner", return_value=docker,
                ):
                    test_cli.run_test_command(
                        args, repo, StringIO(), StringIO(), setup_configurator=configurator,
                        setup_prompt=lambda _message: "keep",
                    )
                if check:
                    docker.inspect_image.assert_called_once()
                    docker.build.assert_not_called()
                else:
                    docker.build.assert_called_once()
                self.assertIs(configurator.call_args.kwargs["image_identity"], image)


class WorkerExecutionTests(unittest.TestCase):
    def test_claude_worker_discovers_hidden_credentials_in_config_directory(self) -> None:
        from hwskill.test_worker import WorkerRequest, _credential_material

        request = WorkerRequest((), "claude-code", "model", "high", 30, ())
        with patch("hwskill.test_worker._credential_file_secrets", return_value=("secret",)) as inspect_store:
            environment, available, secrets = _credential_material(request)

        inspect_store.assert_called_once_with(Path("/credentials/claude-code/.credentials.json"))
        self.assertEqual(environment, (("CLAUDE_CONFIG_DIR", "/credentials/claude-code"),))
        self.assertTrue(available)
        self.assertEqual(secrets, ("secret",))

    def test_core_executor_uses_the_same_network_guard_prefix(self) -> None:
        from io import StringIO
        from hwskill.test_cli import _run_core_path
        from hwskill.test_runner import TestEnvironment

        class Process:
            def __init__(self) -> None:
                self.stdout = StringIO("")
                self.stderr = StringIO("")
                self.returncode = 0
                self.pid = 999999

            def wait(self, timeout=None):
                return self.returncode

        with TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            core = repo / "tests/core"
            core.mkdir(parents=True)
            (core / "test_one.py").write_text(
                "import unittest\nclass One(unittest.TestCase):\n def test_one(self): pass\n",
                encoding="utf-8",
            )
            artifacts = Path(directory) / "artifacts"
            artifacts.mkdir()
            environment = TestEnvironment(
                repo, "docker", "codex", "model", "high", command_prefix=("guard", "--"),
            )
            with patch("hwskill.test_cli.subprocess.Popen", return_value=Process()) as popen:
                result = _run_core_path(Path("tests/core"), repo, artifacts, environment)

        self.assertEqual(result.status, "PASS")
        self.assertEqual(popen.call_args.args[0][:2], ("guard", "--"))

    def test_command_executor_uses_worker_network_guard_prefix(self) -> None:
        from hwskill.test_artifacts import ActionResult
        from hwskill.test_manifest import CommandAction
        from hwskill import network_guard
        from hwskill.test_runner import ActionContext, CommandExecutor, TestEnvironment

        process = Mock(returncode=0)
        process.communicate.return_value = ("", "")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            artifacts = root / "artifacts"
            workspace.mkdir()
            artifacts.mkdir()
            environment = TestEnvironment(
                root, "docker", "codex", "model", "high",
                command_prefix=("python", str(Path(network_guard.__file__).resolve()), "--"),
            )
            with patch("hwskill.test_runner.subprocess.Popen", return_value=process) as popen:
                result = CommandExecutor().run(
                    CommandAction("command", "true"), ActionContext(workspace, artifacts, environment),
                )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            popen.call_args.args[0],
            ("python", str(Path(network_guard.__file__).resolve()), "--", "/bin/bash", "-lc", "true"),
        )

    def test_worker_denies_network_syscalls_for_command_actions_even_in_agent_capable_container(self) -> None:
        from hwskill.test_worker import execute_worker, load_worker_request

        manifest = """\
schema_version: 1
target: {kind: skill, id: local/network}
cases:
  - id: isolated
    steps:
      - type: command
        command: >-
          python -c "import pathlib,socket; s=socket.socket(); pathlib.Path('network-open').write_text('bad')"
    post_check:
      type: command
      command: test ! -e network-open
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "registry"
            collection = repo / "tests/skills/local/network"
            collection.mkdir(parents=True)
            (collection / "test.yaml").write_text(manifest, encoding="utf-8")
            artifacts = root / "artifacts"
            workspace = root / "workspace"
            artifacts.mkdir()
            workspace.mkdir()
            request_path = root / "request.json"
            request_path.write_text(json.dumps({
                "schema_version": 1,
                "selections": [{"kind": "skill", "path": "tests/skills/local/network/test.yaml"}],
                "host": "codex", "model": "model", "reasoning": "high",
                "timeout_seconds": 30, "credential_environment": [],
            }), encoding="utf-8")

            status = execute_worker(
                load_worker_request(request_path), repo_root=repo, tests_root=repo / "tests",
                artifact_root=artifacts, workspace_root=workspace,
                host_version_probe=lambda _host: "0.147.0",
            )

        self.assertEqual(status, 0)

    def test_command_collection_executes_through_shared_runner_and_writes_strict_result(self) -> None:
        from hwskill.test_worker import execute_worker, load_worker_request

        manifest = """\
schema_version: 1
target:
  kind: skill
  id: local/example
cases:
  - id: works
    steps:
      - type: command
        command: printf ok > produced.txt
    post_check:
      type: command
      command: test -f produced.txt
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "registry"
            collection = repo / "tests" / "skills" / "local" / "example"
            collection.mkdir(parents=True)
            (collection / "test.yaml").write_text(manifest, encoding="utf-8")
            artifacts = root / "artifacts"
            workspace = root / "workspace"
            artifacts.mkdir()
            workspace.mkdir()
            request_path = root / "request.json"
            payload = {
                "schema_version": 1,
                "selections": [{"kind": "skill", "path": "tests/skills/local/example/test.yaml"}],
                "host": "codex", "model": "gpt-5.6-terra", "reasoning": "high",
                "timeout_seconds": 30, "credential_environment": [],
            }
            request_path.write_text(json.dumps(payload), encoding="utf-8")

            status = execute_worker(
                load_worker_request(request_path), repo_root=repo, tests_root=repo / "tests",
                artifact_root=artifacts, workspace_root=workspace,
                host_version_probe=lambda _host: "0.147.0",
            )

            result = json.loads((artifacts / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["host_version"], "0.147.0")
            self.assertFalse(Path(result["collections"][0]["cases"][0]["artifact_dir"]).is_absolute())
            context = json.loads(next(artifacts.rglob("context.json")).read_text(encoding="utf-8"))
            self.assertTrue(Path(context["workspace"]).is_relative_to(workspace))

    def test_worker_blocks_when_mount_root_is_a_symlink(self) -> None:
        from hwskill.test_worker import WorkerRequest, WorkerSelection, execute_worker

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            tests = repo / "tests"
            tests.mkdir(parents=True)
            artifacts_real = root / "artifacts-real"
            artifacts_real.mkdir()
            artifacts = root / "artifacts"
            artifacts.symlink_to(artifacts_real, target_is_directory=True)
            workspace = root / "workspace"
            workspace.mkdir()
            request = WorkerRequest((WorkerSelection("core", "tests/core"),), "codex", "model", "high", 30, ())

            status = execute_worker(
                request, repo_root=repo, tests_root=tests, artifact_root=artifacts,
                workspace_root=workspace, host_version_probe=lambda _host: "0.147.0",
            )

        self.assertEqual(status, 3)


if __name__ == "__main__":
    unittest.main()
