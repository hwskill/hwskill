from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from io import StringIO
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
        self.assertIn('if [ "$#" -eq 1 ] && [ "$1" = preflight ]', entrypoint)

    def test_documented_worker_smoke_uses_the_production_runtime_environment(self) -> None:
        readme = (ROOT / "docker/test/README.md").read_text(encoding="utf-8")

        self.assertIn('--env HOME=/workspace/home', readme)
        self.assertIn('--env HWSKILL_REGISTRY_ROOT=/registry', readme)
        self.assertIn('--pids-limit 512', readme)
        self.assertIn('dst=/export', readme)
        self.assertIn('/artifacts:rw,nosuid,nodev,noexec,size=256m,mode=0700', readme)

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
            runner = DockerTestRunner(
                repo, config, image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "a" * 64), uid=123, gid=456,
            )
            value = DockerTestRequest(request, artifacts, workspace, "none", (("CODEX_API_KEY", "credential-sentinel"),))

            argv = runner.build_run_command(value)

            joined = " ".join(argv)
            self.assertIn(f"src={repo},dst=/registry,readonly", joined)
            self.assertIn(f"src={repo / 'tests'},dst=/tests,readonly", joined)
            self.assertIn(f"src={artifacts},dst=/export", joined)
            self.assertIn("--tmpfs /artifacts:rw,nosuid,nodev,noexec,size=256m,mode=0700,uid=123,gid=456", joined)
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

    def test_claude_minimax_adapter_uses_only_its_fixed_credential_destination(self) -> None:
        from hwskill.docker_test_runner import CredentialFile, DockerTestRunner, DockerTestRequest, ImageInfo
        from hwskill.test_configuration import HostModel, TestConfiguration

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            for path in (repository / "tests", root / "artifacts", root / "workspace"):
                path.mkdir(parents=True)
            request_path = root / "request.json"
            request_path.write_text("{}", encoding="utf-8")
            credential = root / "minimax.json"
            credential.write_text('{"minimax-cn-coding-plan":{"type":"api","key":"secret"}}', encoding="utf-8")
            runner = DockerTestRunner(
                repository, TestConfiguration("docker", "claude-code", {"claude-code": HostModel("model", "high")}),
                image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "d" * 64), uid=123, gid=456,
            )
            argv = runner.build_run_command(DockerTestRequest(
                request_path, root / "artifacts", root / "workspace", "agent", (),
                (CredentialFile(credential, "/credentials/minimax-auth.json"),),
            ))

        self.assertIn("dst=/credentials/minimax-auth.json,readonly", " ".join(argv))

    def test_minimax_credential_destination_is_rejected_for_non_claude_hosts(self) -> None:
        """The special adapter mount cannot be repurposed by another host request."""
        from hwskill.docker_test_runner import CredentialFile, DockerRunnerUnavailable, DockerTestRunner, DockerTestRequest, ImageInfo
        from hwskill.test_configuration import HostModel, TestConfiguration

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            for path in (repository / "tests", root / "artifacts", root / "workspace"):
                path.mkdir(parents=True)
            request_path = root / "request.json"
            request_path.write_text("{}", encoding="utf-8")
            credential = root / "minimax.json"
            credential.write_text('{"minimax-cn-coding-plan":{"type":"api","key":"secret"}}', encoding="utf-8")
            runner = DockerTestRunner(
                repository, TestConfiguration("docker", "opencode", {"opencode": HostModel("model", "high")}),
                image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "e" * 64), uid=123, gid=456,
            )
            request = DockerTestRequest(
                request_path, root / "artifacts", root / "workspace", "agent", (),
                (CredentialFile(credential, "/credentials/minimax-auth.json"),),
            )

            with self.assertRaisesRegex(DockerRunnerUnavailable, "unsupported credential"):
                runner.build_run_command(request)

    def test_preflight_uses_same_digest_without_credentials_or_business_mounts(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner, ImageInfo
        from hwskill.test_configuration import HostModel, TestConfiguration

        digest = "sha256:" + "9" * 64
        config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
        runner = DockerTestRunner(Path.cwd(), config, image=ImageInfo("hwskill-test:0.1.0", digest), uid=123, gid=456)

        argv = runner.build_preflight_command(container_name="hwskill-preflight-" + "a" * 32)
        joined = " ".join(argv)

        self.assertEqual(argv[-2:], (digest, "preflight"))
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop ALL", joined)
        self.assertNotIn("--mount", argv)
        self.assertNotIn("CODEX_API_KEY", joined)
        self.assertNotIn("/workspace", joined)
        self.assertNotIn("/export", joined)
        self.assertNotIn("/credentials", joined)

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
        self.assertNotEqual(build[-1], str(Path.cwd().resolve()))
        self.assertIn("hwskill-build-context-", Path(build[-1]).name)

    def test_build_context_contains_only_tracked_allowlisted_files(self) -> None:
        from hwskill.docker_test_runner import DockerRunnerUnavailable, DockerTestRunner
        from hwskill.test_configuration import HostModel, TestConfiguration

        observed = {}
        with TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
            for name in ("README.md", "install.sh", "pyproject.toml", ".gitignore", ".dockerignore"):
                (repo / name).write_text(name, encoding="utf-8")
            for name in ("examples", "profiles", "registry", "scripts", "skills-src", "sources", "src", "tests"):
                path = repo / name / "tracked.txt"
                path.parent.mkdir()
                path.write_text(name, encoding="utf-8")
            dockerfile = repo / "docker/test/Dockerfile"
            dockerfile.parent.mkdir(parents=True)
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            (dockerfile.parent / "entrypoint.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (dockerfile.parent / "requirements.lock").write_text("", encoding="utf-8")
            subprocess.run(("git", "add", "."), cwd=repo, check=True)
            (repo / ".env").write_text("root-sentinel", encoding="utf-8")
            (repo / "src/untracked.token").write_text("nested-sentinel", encoding="utf-8")

            def execute(argv, **kwargs):
                if argv[:2] == ("docker", "build"):
                    context = Path(argv[-1])
                    observed["mode"] = stat.S_IMODE(context.stat().st_mode)
                    observed["files"] = tuple(
                        path.relative_to(context).as_posix() for path in context.rglob("*") if path.is_file()
                    )
                    observed["content"] = "\n".join(
                        path.read_text(encoding="utf-8", errors="replace")
                        for path in context.rglob("*") if path.is_file()
                    )
                    observed["dockerfile"] = argv[argv.index("--file") + 1]
                    observed["docker_host"] = kwargs["env"].get("DOCKER_HOST")
                    return subprocess.CompletedProcess(argv, 1, "", "build stopped by test")
                raise AssertionError(argv)

            config = TestConfiguration("docker", "codex", {"codex": HostModel("model", "high")})
            with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote.example:2376"}, clear=False):
                with self.assertRaises(DockerRunnerUnavailable):
                    DockerTestRunner(repo, config, command_runner=execute).build()

        self.assertEqual(observed["mode"], 0o700)
        self.assertNotIn(".env", observed["files"])
        self.assertNotIn("src/untracked.token", observed["files"])
        self.assertNotIn("sentinel", observed["content"])
        self.assertNotEqual(observed["dockerfile"], str(dockerfile))
        self.assertEqual(observed["docker_host"], "tcp://remote.example:2376")

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
            "host_version": "0.147.0",
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

    def test_worker_rejects_a_host_version_not_bound_to_the_standard_image(self) -> None:
        from hwskill.test_worker import WorkerRequestError, load_worker_request

        payload = self._request()
        payload["host_version"] = "forged-version"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(WorkerRequestError, "host_version"):
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
            if argv[-1] == "preflight":
                return subprocess.CompletedProcess(argv, 0, "", "")
            mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]
            request_mount = next(value for value in mounts if "dst=/run/request.json" in value)
            request_path = Path(request_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            captured_request.update(json.loads(request_path.read_text(encoding="utf-8")))
            artifact_mount = next(value for value in mounts if "dst=/export" in value)
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
                    "kind": "core", "target_id": "core", "selection_path": "tests/core", "status": "PASS",
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
            self.assertEqual(captured_request["host_version"], "0.147.0")
            runs = [argv for argv in captured_argv if argv[:2] == ("docker", "run")]
            self.assertEqual(runs[0][-2:], ("sha256:" + "e" * 64, "preflight"))
            self.assertEqual(runs[1][-5:], ("python", "-m", "hwskill.test_worker", "--request", "/run/request.json"))
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

    def test_preflight_failure_never_starts_the_credentialed_worker(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner, ImageInfo
        from hwskill.test_runner import TestEnvironment

        calls = []

        def execute(argv, **kwargs):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 1, "", "version mismatch")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "tests/core").mkdir(parents=True)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            runner = DockerTestRunner(
                repo, self._config(), image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "f" * 64),
                command_runner=execute,
            )

            result = runner.run(
                (Path("tests/core"),), TestEnvironment(repo, "docker", "codex", "model", "high"), artifacts,
            )

        runs = [argv for argv in calls if argv[:2] == ("docker", "run")]
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][-1], "preflight")

    def test_timeout_cleans_only_the_unguessable_named_container_without_secrets(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner, ImageInfo
        from hwskill.test_runner import TestEnvironment

        calls = []
        def execute(argv, **kwargs):
            calls.append((tuple(argv), kwargs))
            if argv[:2] == ("docker", "run"):
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if argv[:3] == ("docker", "container", "ls"):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "tests/core").mkdir(parents=True)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            environment = TestEnvironment(
                repo, "docker", "codex", "model", "high",
                environment_variables=(("CODEX_API_KEY", "secret-sentinel"),), timeout_seconds=1,
            )
            runner = DockerTestRunner(
                repo, self._config(), image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "f" * 64),
                command_runner=execute,
            )
            result = runner.run((Path("tests/core"),), environment, artifacts)

        self.assertEqual(result.status, "BLOCKED")
        run = next(argv for argv, _kwargs in calls if argv[:2] == ("docker", "run"))
        name = run[run.index("--name") + 1]
        self.assertRegex(name, r"^hwskill-preflight-[0-9a-f]{32}$")
        self.assertIn(("docker", "stop", "--time", "5", name), [argv for argv, _kwargs in calls])
        self.assertIn(("docker", "rm", "--force", name), [argv for argv, _kwargs in calls])
        self.assertIn(
            ("docker", "container", "ls", "--all", "--quiet", "--filter", f"name=^/{name}$"),
            [argv for argv, _kwargs in calls],
        )
        cleanup_calls = [item for item in calls if item[0][:2] != ("docker", "run")]
        self.assertNotIn("secret-sentinel", repr(cleanup_calls))
        self.assertEqual(run[-1], "preflight")
        self.assertNotIn("--mount", run)
        self.assertNotIn("CODEX_API_KEY", " ".join(run))

    def test_outer_timeout_budget_counts_every_case_and_action(self) -> None:
        from hwskill.docker_test_runner import _result_expectations, _selection_timeout_budget
        from hwskill.test_manifest import CommandAction, TestCase, TestCollection, TestTarget

        action = CommandAction("step", "true")
        post = CommandAction("check", "true")
        cases = tuple(TestCase(f"case-{index}", None, None, None, (action,), post) for index in range(3))
        collection = TestCollection(
            Path("tests/skills/local/example/test.yaml"), TestTarget("skill", "local/example"),
            cases, Path("tests/skills/local/example/fixtures"),
        )

        self.assertEqual(_selection_timeout_budget((collection,), 10), 110)
        targeted = _result_expectations((Path("tests/core/test_models.py"),), Path.cwd())
        self.assertEqual(targeted[0].cases[0].case_id, "tests/core/test_models.py")

    def test_forged_pass_for_unrelated_selection_is_blocked(self) -> None:
        from hwskill.docker_test_runner import DockerTestRunner, ImageInfo
        from hwskill.test_runner import TestEnvironment

        def execute(argv, **kwargs):
            if argv[-1] == "preflight":
                return subprocess.CompletedProcess(argv, 0, "", "")
            mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]
            artifact_mount = next(value for value in mounts if "dst=/export" in value)
            artifact_root = Path(artifact_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            (artifact_root / "result.json").write_text(json.dumps({
                "schema_version": 1, "status": "PASS", "runner": "docker", "host": "codex",
                "model": "model", "host_version": "0.147.0", "blocked_reason": None,
                "collections": [{
                    "kind": "profile", "target_id": "unrelated", "selection_path": "tests/core", "status": "PASS",
                    "cases": [{"case_id": "fake", "status": "PASS", "artifact_dir": "fake",
                               "actions": [{"action_id": "fake", "status": "completed", "exit_code": 0,
                                            "artifact_dir": "fake/action"}]}],
                }],
            }), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "tests/core").mkdir(parents=True)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            runner = DockerTestRunner(
                repo, self._config(), image=ImageInfo("hwskill-test:0.1.0", "sha256:" + "f" * 64),
                command_runner=execute,
            )
            result = runner.run(
                (Path("tests/core"),), TestEnvironment(repo, "docker", "codex", "model", "high"), artifacts,
            )

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.collections, ())

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

    def test_result_binding_rejects_missing_duplicate_extra_and_inconsistent_evidence(self) -> None:
        from hwskill.docker_test_runner import (
            DockerRunnerUnavailable, _ExpectedCase, _ExpectedCollection, _load_result,
        )

        valid = {
            "schema_version": 1, "status": "PASS", "runner": "docker", "host": "codex",
            "model": "model", "host_version": "0.147.0", "blocked_reason": None,
            "collections": [{
                "kind": "core", "target_id": "core", "selection_path": "tests/core", "status": "PASS",
                "cases": [{"case_id": "core", "status": "PASS", "artifact_dir": "core",
                           "actions": [{"action_id": "unittest", "status": "completed", "exit_code": 0,
                                        "artifact_dir": "core/action"}]}],
            }],
        }
        expected = (_ExpectedCollection("tests/core", "core", "core", (_ExpectedCase("core", ("unittest",), True),)),)
        mutations = {}
        mutations["missing"] = json.loads(json.dumps(valid))
        mutations["missing"]["collections"][0]["cases"][0]["actions"] = []
        mutations["duplicate"] = json.loads(json.dumps(valid))
        mutations["duplicate"]["collections"][0]["cases"][0]["actions"] *= 2
        mutations["extra"] = json.loads(json.dumps(valid))
        mutations["extra"]["collections"] *= 2
        mutations["wrong-path"] = json.loads(json.dumps(valid))
        mutations["wrong-path"]["collections"][0]["selection_path"] = "tests/core/test_other.py"
        mutations["inconsistent"] = json.loads(json.dumps(valid))
        mutations["inconsistent"]["collections"][0]["cases"][0]["actions"][0]["exit_code"] = 7

        for name, payload in mutations.items():
            with self.subTest(name=name), TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "result.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(DockerRunnerUnavailable):
                    _load_result(path, root, expected)

    def test_agent_post_check_result_is_recomputed_from_strict_final_response(self) -> None:
        from hwskill.docker_test_runner import (
            DockerRunnerUnavailable, _ExpectedCase, _ExpectedCollection, _load_result,
        )

        payload = {
            "schema_version": 1, "status": "PASS", "runner": "docker", "host": "codex",
            "model": "model", "host_version": "0.147.0", "blocked_reason": None,
            "collections": [{
                "kind": "skill", "target_id": "local/agent", "selection_path": "tests/skills/local/agent/test.yaml",
                "status": "PASS", "cases": [{
                    "case_id": "agent-case", "status": "PASS", "artifact_dir": "skill/case",
                    "actions": [{"action_id": "judge", "status": "completed", "exit_code": 0,
                                 "artifact_dir": "skill/case/actions/judge"}],
                }],
            }],
        }
        expected = (_ExpectedCollection(
            "tests/skills/local/agent/test.yaml", "skill", "local/agent",
            (_ExpectedCase("agent-case", ("judge",), False),),
        ),)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result_path = root / "result.json"
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DockerRunnerUnavailable):
                _load_result(result_path, root, expected)

            response = root / "skill/case/actions/judge/final-response.md"
            response.parent.mkdir(parents=True)
            response.write_text('{"result":"pass","evidence":["verified"]}', encoding="utf-8")
            accepted = _load_result(result_path, root, expected)

            response.write_text('{"result":"fail","evidence":["failed"]}', encoding="utf-8")
            with self.assertRaises(DockerRunnerUnavailable):
                _load_result(result_path, root, expected)

        self.assertEqual(accepted.status, "PASS")

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
    def test_allow_network_guard_keeps_export_isolated_and_supports_local_socketpair(self) -> None:
        from hwskill import network_guard

        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            workspace = root / "workspace"
            exported = root / "export"
            workspace.mkdir()
            exported.mkdir()
            (exported / "secret").write_text("must-not-read", encoding="utf-8")
            probe = (
                "import pathlib,socket; left,right=socket.socketpair(); left.close(); right.close(); "
                "out=pathlib.Path('probe'); "
                "\ntry: pathlib.Path(%r).read_text(); out.write_text('export-readable')"
                "\nexcept OSError: out.write_text('socket-ok-export-denied')"
            ) % str(exported / "secret")
            completed = subprocess.run(
                (
                    sys.executable, str(Path(network_guard.__file__).resolve()), "--allow-network",
                    "--read-write", str(workspace), "--", sys.executable, "-c", probe,
                ), cwd=workspace, text=True, capture_output=True, check=False,
                env={"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(workspace)},
            )
            observed = (workspace / "probe").read_text(encoding="utf-8") if (workspace / "probe").exists() else ""

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(observed, "socket-ok-export-denied")

    def test_docker_guard_denies_agent_the_sibling_evidence_snapshot_but_allows_post_check_read(self) -> None:
        from hwskill import network_guard

        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            pool = root / "workspace-pool"
            workspace = pool / "case"
            evidence = pool / "evidence"
            workspace.mkdir(parents=True)
            evidence.mkdir()
            (evidence / "trusted.txt").write_text("sealed", encoding="utf-8")
            agent_probe = (
                "import pathlib; source=pathlib.Path(%r); "
                "\ntry: source.read_text(); pathlib.Path('agent-result').write_text('leaked')"
                "\nexcept OSError: pathlib.Path('agent-result').write_text('denied')"
            ) % str(evidence / "trusted.txt")
            agent = subprocess.run(
                (sys.executable, str(Path(network_guard.__file__).resolve()),
                 "--read-write", str(workspace), "--", sys.executable, "-c", agent_probe),
                cwd=workspace, text=True, capture_output=True, check=False,
                env={"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(workspace)},
            )
            post_probe = (
                "import pathlib; evidence=pathlib.Path(%r); "
                "assert evidence.read_text() == 'sealed'; "
                "\ntry: evidence.write_text('mutated')"
                "\nexcept OSError: pathlib.Path('post-result').write_text('readonly')"
                "\nelse: raise SystemExit('evidence write unexpectedly succeeded')"
            ) % str(evidence / "trusted.txt")
            post = subprocess.run(
                (sys.executable, str(Path(network_guard.__file__).resolve()),
                 "--read-write", str(workspace), "--read-only", str(evidence), "--",
                 sys.executable, "-c", post_probe),
                cwd=workspace, text=True, capture_output=True, check=False,
                env={"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(workspace)},
            )
            agent_result = (workspace / "agent-result").read_text(encoding="utf-8")
            post_result = (workspace / "post-result").read_text(encoding="utf-8")

        self.assertEqual(agent.returncode, 0, agent.stderr)
        self.assertEqual(agent_result, "denied")
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual(post_result, "readonly")

    def test_worker_exports_no_raw_secret_while_preserving_agent_business_changes(self) -> None:
        from hwskill.test_artifacts import ActionResult
        from hwskill.test_worker import WorkerRequest, WorkerSelection, execute_worker

        sentinel = "raw-secret-" + "x" * 8192

        class MaliciousAgent:
            def run(self, action, context):
                (context.workspace / "business.txt").write_text("business-change\n", encoding="utf-8")
                (context.workspace / "raw-secret.bin").write_bytes(b"prefix" + sentinel.encode() + b"suffix")
                (context.artifact_dir / "agent-leak.bin").write_bytes(b"agent:" + sentinel.encode())
                return ActionResult(action.action_id, "completed", 0, context.artifact_dir)

        manifest = """\
schema_version: 1
target: {kind: skill, id: local/export-isolation}
cases:
  - id: mixed
    steps:
      - {type: agent, id: agent, prompt: change files}
      - type: command
        id: verify
        command: >-
          test "$(cat business.txt)" = business-change &&
          ! cp raw-secret.bin EXPORT_ROOT/direct-secret.bin &&
          cat raw-secret.bin
    post_check:
      type: command
      id: post
      command: >-
        test -f business.txt &&
        cp business.txt "$HWSKILL_TEST_ARTIFACTS/business.txt" &&
        cp raw-secret.bin "$HWSKILL_TEST_ARTIFACTS/copied-secret.bin"
"""
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            repo = root / "registry"
            target = repo / "tests/skills/local/export-isolation"
            target.mkdir(parents=True)
            staging = root / "artifacts-staging"
            exported = root / "export"
            workspace = root / "workspace"
            for path in (staging, exported, workspace):
                path.mkdir()
            (target / "test.yaml").write_text(
                manifest.replace("EXPORT_ROOT", str(exported)), encoding="utf-8",
            )
            request = WorkerRequest(
                (WorkerSelection("skill", "tests/skills/local/export-isolation/test.yaml"),),
                "codex", "0.147.0", "model", "high", 30, ("CODEX_API_KEY",),
            )
            with patch.dict(os.environ, {"CODEX_API_KEY": sentinel}, clear=False):
                status = execute_worker(
                    request, repo_root=repo, tests_root=repo / "tests", artifact_root=staging,
                    export_root=exported, workspace_root=workspace,
                    agent_executor=MaliciousAgent(),
                )

            exported_files = [path for path in exported.rglob("*") if path.is_file()]
            persisted = b"\n".join(path.read_bytes() for path in exported_files)
            result = json.loads((exported / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 3)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertNotIn(sentinel.encode(), persisted)
        self.assertTrue(any(path.name == "business.txt" for path in exported_files))
        self.assertIn(b"business-change", persisted)

    def test_artifact_export_rejects_links_and_redacts_binary_cross_block_secret(self) -> None:
        from hwskill.test_worker import _export_artifacts

        sentinel = b"cross-boundary-secret"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            exported = root / "exported"
            staging.mkdir()
            exported.mkdir()
            (staging / "binary.bin").write_bytes(b"a" * (64 * 1024 - 5) + sentinel + b"tail")
            (staging / "link").symlink_to("binary.bin")
            (staging / "linked.bin").write_bytes(b"safe")
            os.link(staging / "linked.bin", staging / "hardlink")

            unsafe = _export_artifacts(staging, exported, (sentinel.decode(),))
            persisted = b"\n".join(path.read_bytes() for path in exported.rglob("*") if path.is_file())
            exported_names = {path.name for path in exported.rglob("*")}

        self.assertTrue(unsafe)
        self.assertNotIn(sentinel, persisted)
        self.assertNotIn("link", exported_names)
        self.assertNotIn("hardlink", exported_names)

    def test_guard_can_exec_the_active_python_runtime(self) -> None:
        from hwskill import network_guard

        with TemporaryDirectory() as directory:
            completed = subprocess.run(
                (sys.executable, str(Path(network_guard.__file__).resolve()),
                 "--read-write", directory, "--", sys.executable, "-c", "print('runtime-ok')"),
                text=True, capture_output=True, check=False,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "runtime-ok")

    def test_real_guard_runs_targeted_core_with_stable_repository_pythonpath(self) -> None:
        from hwskill import network_guard
        from hwskill.test_cli import _run_core_path
        from hwskill.test_runner import TestEnvironment

        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            repo = root / "registry"
            core = repo / "tests/core"
            core.mkdir(parents=True)
            (repo / "src/current_only").mkdir(parents=True)
            (repo / "src/current_only/__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
            (core / "test_current.py").write_text(
                "import unittest, current_only\n"
                "class Current(unittest.TestCase):\n"
                " def test_current(self): self.assertEqual(current_only.VALUE, 7)\n",
                encoding="utf-8",
            )
            artifacts = root / "artifacts"
            workspace = root / "workspace"
            artifacts.mkdir()
            workspace.mkdir()
            environment = TestEnvironment(
                repo, "docker", "codex", "model", "high", workspace_root=workspace,
                command_path=f"{Path(sys.executable).parent}:{os.defpath}",
                command_prefix=(
                    sys.executable, str(Path(network_guard.__file__).resolve()),
                    "--read-only", str(repo), "--read-only", str(repo / "tests"),
                    "--read-write", str(artifacts), "--read-write", str(workspace), "--",
                ),
            )
            result = _run_core_path(Path("tests/core/test_current.py"), repo, artifacts, environment)

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.cases[0].case_id, "tests/core/test_current.py")

    def test_unavailable_filesystem_isolation_is_explicitly_blocked(self) -> None:
        from contextlib import redirect_stderr
        from hwskill import network_guard

        error = StringIO()
        with patch.object(network_guard, "install_filesystem_guard", side_effect=OSError("unavailable")), redirect_stderr(error):
            code = network_guard.main(("--read-only", "/tmp", "--", "true"))

        self.assertEqual(code, 125)
        self.assertIn("isolation guard unavailable", error.getvalue())
    def test_command_environment_excludes_agent_credentials(self) -> None:
        from hwskill.test_runner import TestEnvironment, _agent_environment, _command_environment

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = TestEnvironment(
                workspace, "docker", "codex", "model", "high",
                environment_variables=(("CODEX_API_KEY", "secret-sentinel"),),
                command_environment_variables=(),
                command_path="/fixed/venv/bin:/usr/local/bin:/usr/bin:/bin",
            )

            command = _command_environment(environment, workspace)
            agent = _agent_environment(environment, workspace)

        self.assertNotIn("CODEX_API_KEY", command)
        self.assertEqual(command["PATH"], "/fixed/venv/bin:/usr/local/bin:/usr/bin:/bin")
        self.assertEqual(agent["CODEX_API_KEY"], "secret-sentinel")

    def test_command_guard_denies_parent_environment_and_credential_store(self) -> None:
        from hwskill import network_guard

        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            workspace = root / "workspace"
            artifacts = root / "artifacts"
            readonly = root / "readonly"
            denied = root / "credentials/auth.json"
            for path in (workspace, artifacts, readonly, denied.parent):
                path.mkdir(parents=True, exist_ok=True)
            denied.write_text("store-sentinel", encoding="utf-8")
            probe = (
                "import os,pathlib; out=pathlib.Path(os.environ['OUT']); values=[]; "
                "values.append(os.environ.get('CODEX_API_KEY','absent')); "
                "\ntry: values.append(open('/proc/%d/environ'%os.getppid(),'rb').read().decode(errors='ignore'))"
                "\nexcept OSError: values.append('proc-denied'); "
                f"\ntry: values.append(open({str(denied)!r}).read())"
                "\nexcept OSError: values.append('store-denied'); out.write_text('\\n'.join(values))"
            )
            completed = subprocess.run(
                (sys.executable, str(Path(network_guard.__file__).resolve()),
                 "--read-only", str(readonly), "--read-write", str(workspace),
                 "--read-write", str(artifacts), "--", "/usr/bin/python3", "-c", probe),
                text=True, capture_output=True, check=False,
                env={"PATH": os.defpath, "LANG": "C.UTF-8", "OUT": str(artifacts / "probe.txt"),
                     "CODEX_API_KEY": "env-sentinel"},
            )

            observed = (artifacts / "probe.txt").read_text(encoding="utf-8") if (artifacts / "probe.txt").exists() else ""

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(observed.splitlines(), ["env-sentinel", "proc-denied", "store-denied"])

    def test_mixed_collection_gives_credentials_only_to_agent_and_commands_cannot_recover_them(self) -> None:
        from hwskill import network_guard
        from hwskill.test_artifacts import ActionResult
        from hwskill.test_manifest import load_test_collection
        from hwskill.test_runner import TestEnvironment, _agent_environment, run_collection

        class Agent:
            observed = None

            def run(self, action, context):
                self.observed = _agent_environment(context.environment, context.workspace)
                return ActionResult(action.action_id, "completed", 0, context.artifact_dir)

        manifest = """\
schema_version: 1
target: {kind: skill, id: local/isolation}
cases:
  - id: mixed
    steps:
      - {type: agent, id: agent, prompt: observe}
      - type: command
        id: attack
        command: >-
          test -z "${CODEX_API_KEY+x}" &&
          ! grep -a 'secret-sentinel' /proc/$PPID/environ &&
          ! cat CREDENTIAL_STORE > ARTIFACT_ROOT/leak
    post_check: {type: command, id: post, command: test ! -s ARTIFACT_ROOT/leak}
"""
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            repo = root / "registry"
            target = repo / "tests/skills/local/isolation"
            target.mkdir(parents=True)
            artifacts = root / "artifacts"
            workspace = root / "workspace"
            store = root / "credentials/auth.json"
            (target / "test.yaml").write_text(
                manifest.replace("CREDENTIAL_STORE", str(store)).replace("ARTIFACT_ROOT", str(artifacts)),
                encoding="utf-8",
            )
            artifacts.mkdir()
            workspace.mkdir()
            store.parent.mkdir()
            store.write_text("store-sentinel", encoding="utf-8")
            collection = load_test_collection(target / "test.yaml", repo)
            environment = TestEnvironment(
                repo, "docker", "codex", "model", "high",
                secret_values=("secret-sentinel", "store-sentinel"),
                environment_variables=(("CODEX_API_KEY", "secret-sentinel"),),
                command_environment_variables=(),
                workspace_root=workspace,
                command_prefix=(
                    sys.executable, str(Path(network_guard.__file__).resolve()),
                    "--read-only", str(repo), "--read-only", str(repo / "tests"),
                    "--read-write", str(artifacts), "--read-write", str(workspace), "--",
                ),
            )
            agent = Agent()
            with patch.dict(os.environ, {"CODEX_API_KEY": "secret-sentinel"}):
                result = run_collection(collection, environment, artifacts, agent_executor=agent)

            persisted = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in artifacts.rglob("*") if path.is_file()
            )

        self.assertEqual(result.status, "PASS", persisted)
        self.assertEqual(agent.observed["CODEX_API_KEY"], "secret-sentinel")
        self.assertNotIn("secret-sentinel", persisted)
        self.assertNotIn("store-sentinel", persisted)
    def test_claude_worker_discovers_hidden_credentials_in_config_directory(self) -> None:
        from hwskill.test_worker import WorkerRequest, _credential_material

        request = WorkerRequest((), "claude-code", "2.1.141", "model", "high", 30, ())
        with patch("hwskill.test_worker._credential_file_secrets", return_value=("secret",)) as inspect_store:
            environment, available, secrets = _credential_material(request)

        inspect_store.assert_called_once_with(Path("/credentials/claude-code/.credentials.json"))
        self.assertEqual(environment, (("CLAUDE_CONFIG_DIR", "/credentials/claude-code"),))
        self.assertTrue(available)
        self.assertEqual(secrets, ("secret",))

    def test_claude_worker_adapts_filtered_minimax_credentials_without_exposing_the_file(self) -> None:
        from hwskill.test_worker import WorkerRequest, _credential_material

        request = WorkerRequest((), "claude-code", "2.1.141", "model", "high", 30, ())
        with patch("hwskill.test_worker._minimax_auth_token", return_value="minimax-secret") as adapter:
            environment, available, secrets = _credential_material(request)

        adapter.assert_called_once()
        self.assertEqual(environment, (
            ("ANTHROPIC_AUTH_TOKEN", "minimax-secret"),
            ("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic"),
        ))
        self.assertTrue(available)
        self.assertEqual(secrets, ("minimax-secret",))

    def test_minimax_worker_adapter_requires_the_filtered_file_schema_and_mode(self) -> None:
        """The container-side adapter refuses a bypassed wrapper file before any Agent can read it."""
        from hwskill.test_worker import _minimax_auth_token

        with TemporaryDirectory() as directory:
            source = Path(directory) / "minimax-auth.json"
            source.write_text(
                '{"minimax-cn-coding-plan":{"type":"api","key":"minimax-secret"},'
                '"other":{"type":"api","key":"must-not-mount"}}', encoding="utf-8",
            )
            source.chmod(0o600)
            self.assertIsNone(_minimax_auth_token(source))

            source.write_text(
                '{"minimax-cn-coding-plan":{"type":"api","key":"minimax-secret"}}', encoding="utf-8",
            )
            self.assertEqual(_minimax_auth_token(source), "minimax-secret")
            source.chmod(0o644)
            self.assertIsNone(_minimax_auth_token(source))

    def test_credential_store_short_raw_values_are_still_export_guarded(self) -> None:
        from hwskill.test_worker import _credential_file_secrets

        with TemporaryDirectory() as directory:
            store = Path(directory) / "auth.json"
            store.write_text('{"token":"abc","empty":""}', encoding="utf-8")

            secrets = _credential_file_secrets(store)

        self.assertEqual(secrets, ("abc",))

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

    def test_command_post_check_guard_keeps_workspace_writable_and_adds_readonly_evidence(self) -> None:
        from hwskill.test_runner import TestEnvironment, _command_prefix

        with TemporaryDirectory() as directory:
            pool = Path(directory) / "workspace-pool"
            workspace = pool / "case"
            evidence = pool / "evidence"
            pool.mkdir()
            workspace.mkdir()
            evidence.mkdir()
            environment = TestEnvironment(
                Path(directory), "docker", "codex", "model", "high", workspace_root=pool,
                command_prefix=("guard", "--read-write", str(pool), "--"),
            )
            prefix = _command_prefix(environment, workspace, evidence)

        self.assertEqual(prefix, ("guard", "--read-write", str(workspace), "--read-only", str(evidence), "--"))

    def test_command_executor_blocks_when_isolation_guard_cannot_install(self) -> None:
        from hwskill.test_manifest import CommandAction
        from hwskill.test_runner import ActionContext, CommandExecutor, TestEnvironment

        process = Mock(returncode=125)
        process.communicate.return_value = ("", "hwskill isolation guard unavailable\n")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            artifacts = root / "artifacts"
            workspace.mkdir()
            artifacts.mkdir()
            environment = TestEnvironment(root, "docker", "codex", "model", "high", command_prefix=("guard", "--"))
            with patch("hwskill.test_runner.subprocess.Popen", return_value=process):
                result = CommandExecutor().run(
                    CommandAction("command", "true"), ActionContext(workspace, artifacts, environment),
                )

        self.assertEqual(result.status, "blocked")
        self.assertIsNone(result.exit_code)

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
                "host": "codex", "host_version": "0.147.0", "model": "model", "reasoning": "high",
                "timeout_seconds": 30, "credential_environment": [],
            }), encoding="utf-8")

            status = execute_worker(
                load_worker_request(request_path), repo_root=repo, tests_root=repo / "tests",
                artifact_root=artifacts, workspace_root=workspace,
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
            exported = root / "exported"
            workspace = root / "workspace"
            artifacts.mkdir()
            exported.mkdir()
            workspace.mkdir()
            request_path = root / "request.json"
            payload = {
                "schema_version": 1,
                "selections": [{"kind": "skill", "path": "tests/skills/local/example/test.yaml"}],
                "host": "codex", "host_version": "0.147.0", "model": "gpt-5.6-terra", "reasoning": "high",
                "timeout_seconds": 30, "credential_environment": [],
            }
            request_path.write_text(json.dumps(payload), encoding="utf-8")

            status = execute_worker(
                load_worker_request(request_path), repo_root=repo, tests_root=repo / "tests",
                artifact_root=artifacts, export_root=exported, workspace_root=workspace,
            )

            result = json.loads((exported / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["host_version"], "0.147.0")
            self.assertFalse(Path(result["collections"][0]["cases"][0]["artifact_dir"]).is_absolute())
            context = json.loads(next(exported.rglob("context.json")).read_text(encoding="utf-8"))
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
            request = WorkerRequest(
                (WorkerSelection("core", "tests/core"),), "codex", "0.147.0", "model", "high", 30, (),
            )

            status = execute_worker(
                request, repo_root=repo, tests_root=tests, artifact_root=artifacts,
                workspace_root=workspace,
            )

        self.assertEqual(status, 3)


if __name__ == "__main__":
    unittest.main()
