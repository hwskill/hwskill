from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


class _Runner:
    def __init__(self, responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, *, timeout: float):
        command = tuple(argv)
        self.calls.append(command)
        return self.responses.get(command, subprocess.CompletedProcess(command, 1, "", "missing"))


def _completed(argv: tuple[str, ...], stdout: str = "ok\n", returncode: int = 0):
    return subprocess.CompletedProcess(argv, returncode, stdout, "failure" if returncode else "")


IMAGE_DIGEST = "sha256:" + hashlib.sha256(b"hwskill-standard-test-image").hexdigest()
IMAGE_NAME = f"hwskill/test@{IMAGE_DIGEST}"


class TestTestSetup(unittest.TestCase):
    def setUp(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration

        self.config = TestConfiguration(
            runner="docker", default_host="codex",
            hosts={"codex": HostModel("gpt-5.6-terra", "high")},
        )

    def test_docker_missing_is_reported_first_and_blocked(self) -> None:
        from hwskill.test_setup import inspect_test_setup

        report = inspect_test_setup(self.config, _Runner({}), environment={})

        self.assertEqual(report.status, "BLOCKED")
        self.assertEqual(report.checks[0].name, "docker-daemon")
        self.assertEqual(report.checks[0].status, "BLOCKED")
        self.assertNotIn("missing", report.checks[0].detail)

    def test_missing_host_cli_is_reported_without_leaking_command_output(self) -> None:
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        runner = _Runner({docker_info: _completed(docker_info, "24.0.7\n"), image: _completed(image, IMAGE_DIGEST + "\n")})
        report = inspect_test_setup(self.config, runner, environment={}, expected_image=ExpectedImage(IMAGE_NAME, IMAGE_DIGEST))

        host = next(item for item in report.checks if item.name == "host-cli")
        self.assertEqual(host.status, "BLOCKED")
        self.assertNotIn("missing", host.detail)

    def test_current_model_is_displayed_and_successful_probe_makes_ready(self) -> None:
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        version = ("codex", "--version")
        probe = (
            "codex", "exec", "--model", "gpt-5.6-terra", "-c",
            'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.",
        )
        runner = _Runner({
            docker_info: _completed(docker_info, "24.0.7\n"), image: _completed(image, IMAGE_DIGEST + "\n"),
            version: _completed(version, "codex-cli 0.147.0\n"), probe: _completed(probe, '{"type":"message"}\n'),
        })
        report = inspect_test_setup(
            self.config, runner, environment={"CODEX_API_KEY": "credential-sentinel"},
            expected_image=ExpectedImage(IMAGE_NAME, IMAGE_DIGEST),
        )

        self.assertEqual(report.status, "READY")
        configured = next(item for item in report.checks if item.name == "configured-model")
        self.assertIn("gpt-5.6-terra", configured.detail)
        self.assertIn("high", configured.detail)
        self.assertNotIn("credential-sentinel", "\n".join(item.detail for item in report.checks))

    def test_failed_availability_probe_blocks_setup(self) -> None:
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        version = ("codex", "--version")
        probe = (
            "codex", "exec", "--model", "gpt-5.6-terra", "-c",
            'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.",
        )
        runner = _Runner({
            docker_info: _completed(docker_info, "24.0.7\n"), image: _completed(image, IMAGE_DIGEST + "\n"),
            version: _completed(version, "codex-cli 0.147.0\n"), probe: _completed(probe, returncode=1),
        })
        report = inspect_test_setup(
            self.config, runner, environment={"CODEX_API_KEY": "credential-sentinel"},
            expected_image=ExpectedImage(IMAGE_NAME, IMAGE_DIGEST),
        )

        self.assertEqual(report.status, "BLOCKED")
        self.assertEqual(next(item for item in report.checks if item.name == "model-availability").status, "BLOCKED")

    def test_check_mode_never_writes_or_builds_and_usable_model_defaults_to_keep(self) -> None:
        from hwskill.test_setup import configure_test_setup, SetupCheck, SetupReport

        current = SetupReport("READY", (SetupCheck("model-availability", "READY", "available"),))
        writes = []
        prompts = []
        report = configure_test_setup(
            self.config, check=True, inspect=lambda *_args, **_kwargs: current,
            runner=object(), write=lambda config: writes.append(config),
            prompt=lambda message: prompts.append(message) or "replace",
        )

        self.assertIs(report, current)
        self.assertEqual(writes, [])
        self.assertEqual(prompts, [])

    def test_check_mode_never_invokes_a_callable_image_identity(self) -> None:
        from hwskill.test_setup import configure_test_setup, SetupCheck, SetupReport

        class ExplosiveIdentity:
            def __init__(self) -> None:
                self.invoked = False

            def __call__(self):
                self.invoked = True
                raise AssertionError("image identity providers must not be invoked")

        current = SetupReport("READY", (SetupCheck("model-availability", "READY", "available"),))
        identity = ExplosiveIdentity()
        writes = []
        prompts = []
        with self.assertRaisesRegex(TypeError, "already-resolved ImageIdentity"):
            configure_test_setup(
                self.config, check=True, runner=object(), image_identity=identity,
                inspect=lambda *_args, **_kwargs: current,
                write=lambda config: writes.append(config),
                prompt=lambda message: prompts.append(message) or "replace",
            )

        self.assertFalse(identity.invoked)
        self.assertEqual(writes, [])
        self.assertEqual(prompts, [])

    def test_unusable_current_model_requires_explicit_replacement(self) -> None:
        from hwskill.test_setup import configure_test_setup, SetupCheck, SetupReport

        current = SetupReport("BLOCKED", (SetupCheck("model-availability", "BLOCKED", "unavailable"),))
        with self.assertRaises(ValueError):
            configure_test_setup(
                self.config, check=False, inspect=lambda *_args, **_kwargs: current,
                runner=object(), write=lambda _config: None, prompt=lambda _message: "",
            )

    def test_blocked_replacement_is_not_persisted_and_preserves_current_file(self) -> None:
        from hwskill.test_configuration import HostModel, write_test_configuration
        from hwskill.test_setup import configure_test_setup, SetupCheck, SetupReport

        replacement = type(self.config)(
            runner="docker", default_host="codex",
            hosts={"codex": HostModel("replacement-model", "high")},
        )
        current_report = SetupReport("BLOCKED", (SetupCheck("model-availability", "BLOCKED", "unavailable"),))
        replacement_report = SetupReport("BLOCKED", (SetupCheck("model-availability", "BLOCKED", "unavailable"),))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            write_test_configuration(self.config, path=path)
            before = path.read_text(encoding="utf-8")
            report = configure_test_setup(
                self.config, replacement=replacement, runner=object(),
                inspect=lambda config, _runner: current_report if config == self.config else replacement_report,
                write=lambda config: write_test_configuration(config, path=path),
            )
            self.assertIs(report, replacement_report)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_image_requires_injected_exact_expected_digest(self) -> None:
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        version = ("codex", "--version")
        runner = _Runner({
            docker_info: _completed(docker_info, "24.0.7\n"),
            image: _completed(image, IMAGE_DIGEST + "\n"),
            version: _completed(version, "codex-cli 0.147.0\n"),
        })
        missing = inspect_test_setup(self.config, runner, environment={"CODEX_API_KEY": "set"})
        mismatch = inspect_test_setup(
            self.config, runner, environment={"CODEX_API_KEY": "set"},
            expected_image=ExpectedImage(IMAGE_NAME, "sha256:" + "0" * 64),
        )
        self.assertEqual(next(item for item in missing.checks if item.name == "standard-image").status, "BLOCKED")
        self.assertEqual(next(item for item in mismatch.checks if item.name == "standard-image").status, "BLOCKED")

    def test_hostile_subprocess_output_never_enters_report(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        sentinel = "credential-sentinel"
        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        for host, executable, version in (
            ("codex", "codex", "codex-cli 0.147.0"),
            ("claude-code", "claude", "2.1.141 (Claude Code)"),
            ("opencode", "opencode", "1.14.48"),
        ):
            with self.subTest(host=host):
                config = TestConfiguration("docker", host, {host: HostModel("safe-model", "high")})
                runner = _Runner({
                    docker_info: _completed(docker_info, f"24.0.7 {sentinel}\n"),
                    image: _completed(image, f"{IMAGE_DIGEST} {sentinel}\n"),
                    (executable, "--version"): _completed((executable, "--version"), f"{version} {sentinel}\n"),
                })
                report = inspect_test_setup(
                    config, runner, environment={"CODEX_API_KEY": sentinel},
                    expected_image=ExpectedImage(IMAGE_NAME, IMAGE_DIGEST),
                )
                self.assertNotIn(sentinel, "\n".join(item.detail for item in report.checks))
                self.assertEqual(next(item for item in report.checks if item.name == "docker-daemon").status, "BLOCKED")
                self.assertEqual(next(item for item in report.checks if item.name == "host-cli").status, "BLOCKED")

    def test_host_versions_must_match_the_verified_grammar_and_version(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        for host, executable, malformed, incompatible in (
            ("codex", "codex", "codex 0.147.0", "codex-cli 0.0.1"),
            ("claude-code", "claude", "2.1.141", "2.1.140 (Claude Code)"),
            ("opencode", "opencode", "version=1.14.48", "1.14.47"),
        ):
            for output in (malformed, incompatible):
                with self.subTest(host=host, output=output):
                    config = TestConfiguration("docker", host, {host: HostModel("safe-model", "high")})
                    runner = _Runner({
                        docker_info: _completed(docker_info, "24.0.7\n"),
                        image: _completed(image, IMAGE_DIGEST + "\n"),
                        (executable, "--version"): _completed((executable, "--version"), output + "\n"),
                    })
                    report = inspect_test_setup(
                        config, runner, environment={"CODEX_API_KEY": "set"},
                        expected_image=ExpectedImage(IMAGE_NAME, IMAGE_DIGEST),
                    )
                    self.assertEqual(next(item for item in report.checks if item.name == "host-cli").status, "BLOCKED")

    def test_real_host_version_shapes_are_accepted(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration
        from hwskill.test_setup import ExpectedImage, inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        for host, executable, version, environment in (
            ("codex", "codex", "codex-cli 0.147.0", {"CODEX_API_KEY": "set"}),
            ("claude-code", "claude", "2.1.141 (Claude Code)", {"ANTHROPIC_API_KEY": "set"}),
            ("opencode", "opencode", "1.14.48", {"OPENCODE_API_KEY": "set"}),
        ):
            with self.subTest(host=host):
                config = TestConfiguration("docker", host, {host: HostModel("safe-model", "high")})
                probe = {
                    "codex": ("codex", "exec", "--model", "safe-model", "-c", 'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY."),
                    "claude-code": ("claude", "--print", "--output-format", "json", "--model", "safe-model", "Reply with exactly READY."),
                    "opencode": ("opencode", "run", "--format", "json", "--model", "safe-model", "Reply with exactly READY."),
                }[host]
                runner = _Runner({
                    docker_info: _completed(docker_info, "24.0.7\n"),
                    image: _completed(image, IMAGE_DIGEST + "\n"),
                    (executable, "--version"): _completed((executable, "--version"), version + "\n"),
                    probe: _completed(probe, '{"type":"message"}\n'),
                })
                report = inspect_test_setup(
                    config, runner, environment=environment,
                    expected_image=ExpectedImage(IMAGE_NAME, IMAGE_DIGEST),
                )
                self.assertEqual(report.status, "READY")

    def test_configure_uses_one_authoritative_identity_with_real_inspection(self) -> None:
        from hwskill.test_configuration import HostModel, write_test_configuration
        from hwskill.test_setup import ExpectedImage, configure_test_setup

        current = type(self.config)("docker", "codex", {"codex": HostModel("old-model", "high")})
        replacement = type(self.config)("docker", "codex", {"codex": HostModel("new-model", "high")})
        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        version = ("codex", "--version")
        old_probe = ("codex", "exec", "--model", "old-model", "-c", 'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.")
        new_probe = ("codex", "exec", "--model", "new-model", "-c", 'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.")
        runner = _Runner({
            docker_info: _completed(docker_info, "24.0.7\n"),
            image: _completed(image, IMAGE_DIGEST + "\n"),
            version: _completed(version, "codex-cli 0.147.0\n"),
            old_probe: _completed(old_probe, returncode=1),
            new_probe: _completed(new_probe, '{"type":"message"}\n'),
        })
        identity = ExpectedImage(IMAGE_NAME, IMAGE_DIGEST)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            write_test_configuration(current, path=path)
            report = configure_test_setup(
                current, replacement=replacement, runner=runner,
                image_identity=identity,
                inspection_kwargs={"environment": {"CODEX_API_KEY": "set"}},
                write=lambda config: write_test_configuration(config, path=path),
            )
            self.assertEqual(report.status, "READY")
            self.assertIn("new-model", path.read_text(encoding="utf-8"))

    def test_configure_identity_mismatch_preserves_current_file(self) -> None:
        from hwskill.test_configuration import HostModel, write_test_configuration
        from hwskill.test_setup import ExpectedImage, configure_test_setup

        current = type(self.config)("docker", "codex", {"codex": HostModel("old-model", "high")})
        replacement = type(self.config)("docker", "codex", {"codex": HostModel("new-model", "high")})
        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME)
        version = ("codex", "--version")
        old_probe = ("codex", "exec", "--model", "old-model", "-c", 'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.")
        new_probe = ("codex", "exec", "--model", "new-model", "-c", 'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.")
        runner = _Runner({
            docker_info: _completed(docker_info, "24.0.7\n"),
            image: _completed(image, IMAGE_DIGEST + "\n"),
            version: _completed(version, "codex-cli 0.147.0\n"),
            old_probe: _completed(old_probe, returncode=1),
            new_probe: _completed(new_probe, '{"type":"message"}\n'),
        })
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            write_test_configuration(current, path=path)
            before = path.read_text(encoding="utf-8")
            report = configure_test_setup(
                current, replacement=replacement, runner=runner,
                image_identity=ExpectedImage(IMAGE_NAME, "sha256:" + "0" * 64),
                inspection_kwargs={"environment": {"CODEX_API_KEY": "set"}},
                write=lambda config: write_test_configuration(config, path=path),
            )
            self.assertEqual(report.status, "BLOCKED")
            self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
