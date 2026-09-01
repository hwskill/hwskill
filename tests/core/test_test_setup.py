from __future__ import annotations

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
        from hwskill.test_setup import inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", "hwskill/test:latest")
        runner = _Runner({docker_info: _completed(docker_info), image: _completed(image, "sha256:image\n")})
        report = inspect_test_setup(self.config, runner, environment={})

        host = next(item for item in report.checks if item.name == "host-cli")
        self.assertEqual(host.status, "BLOCKED")
        self.assertNotIn("missing", host.detail)

    def test_current_model_is_displayed_and_successful_probe_makes_ready(self) -> None:
        from hwskill.test_setup import inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", "hwskill/test:latest")
        version = ("codex", "--version")
        probe = (
            "codex", "exec", "--model", "gpt-5.6-terra", "-c",
            'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.",
        )
        runner = _Runner({
            docker_info: _completed(docker_info), image: _completed(image, "sha256:image\n"),
            version: _completed(version, "0.147.0\n"), probe: _completed(probe, '{"type":"message"}\n'),
        })
        report = inspect_test_setup(
            self.config, runner, environment={"CODEX_API_KEY": "credential-sentinel"},
        )

        self.assertEqual(report.status, "READY")
        configured = next(item for item in report.checks if item.name == "configured-model")
        self.assertIn("gpt-5.6-terra", configured.detail)
        self.assertIn("high", configured.detail)
        self.assertNotIn("credential-sentinel", "\n".join(item.detail for item in report.checks))

    def test_failed_availability_probe_blocks_setup(self) -> None:
        from hwskill.test_setup import inspect_test_setup

        docker_info = ("docker", "info", "--format", "{{.ServerVersion}}")
        image = ("docker", "image", "inspect", "--format", "{{.Id}}", "hwskill/test:latest")
        version = ("codex", "--version")
        probe = (
            "codex", "exec", "--model", "gpt-5.6-terra", "-c",
            'model_reasoning_effort="high"', "--json", "--skip-git-repo-check", "Reply with exactly READY.",
        )
        runner = _Runner({
            docker_info: _completed(docker_info), image: _completed(image, "sha256:image\n"),
            version: _completed(version), probe: _completed(probe, returncode=1),
        })
        report = inspect_test_setup(
            self.config, runner, environment={"CODEX_API_KEY": "credential-sentinel"},
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

    def test_unusable_current_model_requires_explicit_replacement(self) -> None:
        from hwskill.test_setup import configure_test_setup, SetupCheck, SetupReport

        current = SetupReport("BLOCKED", (SetupCheck("model-availability", "BLOCKED", "unavailable"),))
        with self.assertRaises(ValueError):
            configure_test_setup(
                self.config, check=False, inspect=lambda *_args, **_kwargs: current,
                runner=object(), write=lambda _config: None, prompt=lambda _message: "",
            )


if __name__ == "__main__":
    unittest.main()
