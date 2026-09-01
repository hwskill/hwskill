from __future__ import annotations

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class TestTestConfiguration(unittest.TestCase):
    def test_xdg_path_is_used_before_home_config(self) -> None:
        from hwskill.test_configuration import TestConfigurationError, test_config_path

        with TemporaryDirectory() as directory:
            xdg = Path(directory) / "xdg"
            home = Path(directory) / "home"
            self.assertEqual(
                test_config_path(environment={"XDG_CONFIG_HOME": str(xdg)}, home=home),
                xdg / "hwskill" / "test.yaml",
            )
            self.assertEqual(
                test_config_path(environment={}, home=home),
                home / ".config" / "hwskill" / "test.yaml",
            )
            for invalid in ("relative/config", "~/config"):
                with self.subTest(invalid=invalid), self.assertRaises(TestConfigurationError):
                    test_config_path(environment={"XDG_CONFIG_HOME": invalid}, home=home)

    def test_configuration_records_model_but_never_credentials(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration, write_test_configuration

        with TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "hwskill" / "test.yaml"
            write_test_configuration(TestConfiguration(
                runner="docker",
                default_host="codex",
                hosts={"codex": HostModel("gpt-5.6-terra", "high")},
            ), path=path)

            text = path.read_text(encoding="utf-8")
            self.assertIn("gpt-5.6-terra", text)
            self.assertNotIn("api_key", text.lower())
            self.assertNotIn("token", text.lower())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_credential_sentinel_is_not_persisted_from_environment(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration, write_test_configuration

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CODEX_API_KEY": "credential-sentinel"}, clear=False,
        ):
            path = Path(directory) / "test.yaml"
            write_test_configuration(TestConfiguration(
                runner="local", default_host="codex",
                hosts={"codex": HostModel("gpt-5.6-terra", "low")},
            ), path=path)
            self.assertNotIn("credential-sentinel", path.read_text(encoding="utf-8"))

    def test_load_rejects_malformed_duplicate_or_unknown_configuration(self) -> None:
        from hwskill.test_configuration import load_test_configuration

        invalid = (
            "runner: docker\ndefault_host: codex\nhosts: [not-a-map]\n",
            "schema_version: 1\nschema_version: 1\nrunner: docker\ndefault_host: codex\nhosts: {}\n",
            "schema_version: 1\nrunner: docker\ndefault_host: codex\nhosts: {}\ncredential: should-not-exist\n",
            "schema_version: 1\nrunner: docker\ndefault_host: missing\nhosts: {}\n",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            for text in invalid:
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_test_configuration(path=path)

    def test_load_and_write_reject_symlinked_config_paths(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration, load_test_configuration, write_test_configuration

        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.yaml"
            target.write_text("not a configuration", encoding="utf-8")
            link = root / "test.yaml"
            link.symlink_to(target)
            config = TestConfiguration(
                runner="docker", default_host="codex",
                hosts={"codex": HostModel("gpt-5.6-terra", "high")},
            )
            with self.assertRaises(ValueError):
                load_test_configuration(path=link)
            with self.assertRaises(ValueError):
                write_test_configuration(config, path=link)

    def test_load_rejects_anchors_and_aliases_at_any_depth(self) -> None:
        from hwskill.test_configuration import load_test_configuration

        text = (
            "schema_version: 1\nrunner: docker\ndefault_host: codex\nhosts:\n"
            "  codex: &model\n    model: gpt-5.6-terra\n    reasoning: high\n"
            "  claude-code: *model\n"
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_test_configuration(path=path)

    def test_load_caps_config_size_and_rejects_unsupported_reasoning(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration, load_test_configuration, write_test_configuration

        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            path.write_bytes(b"#" * (64 * 1024 + 1))
            with self.assertRaises(ValueError):
                load_test_configuration(path=path)
            with self.assertRaises(ValueError):
                write_test_configuration(TestConfiguration(
                    runner="docker", default_host="codex",
                    hosts={"codex": HostModel("gpt-5.6-terra", "unbounded")},
                ), path=path)

    def test_model_uses_a_safe_single_cli_argument_grammar(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration, write_test_configuration

        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            for model in ("gpt-5.6-terra", "minimax-cn-coding-plan/MiniMax-M2.5", "provider:model@2026.09"):
                with self.subTest(valid=model):
                    write_test_configuration(TestConfiguration(
                        runner="docker", default_host="codex",
                        hosts={"codex": HostModel(model, "high")},
                    ), path=path)
            for model in ("model name", "model\nnext", "model\tname", "model;command"):
                with self.subTest(invalid=model), self.assertRaises(ValueError):
                    write_test_configuration(TestConfiguration(
                        runner="docker", default_host="codex",
                        hosts={"codex": HostModel(model, "high")},
                    ), path=path)

    def test_resolve_host_model_uses_default_unless_explicit_override(self) -> None:
        from hwskill.test_configuration import HostModel, TestConfiguration, resolve_host_model

        config = TestConfiguration(
            runner="docker", default_host="codex",
            hosts={
                "codex": HostModel("gpt-5.6-terra", "high"),
                "claude-code": HostModel("claude-model", "medium"),
            },
        )
        self.assertEqual(resolve_host_model(config), ("codex", HostModel("gpt-5.6-terra", "high")))
        self.assertEqual(
            resolve_host_model(config, host="claude-code"),
            ("claude-code", HostModel("claude-model", "medium")),
        )

    def test_atomic_write_failure_preserves_existing_file_and_removes_temporary(self) -> None:
        from hwskill.test_configuration import (
            AtomicFileOperations, HostModel, TestConfiguration, write_test_configuration,
        )

        def reject_replace(*_args, **_kwargs):
            raise OSError("injected replacement failure")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            old = "old configuration remains complete\n"
            path.write_text(old, encoding="utf-8")
            config = TestConfiguration(
                runner="docker", default_host="codex",
                hosts={"codex": HostModel("gpt-5.6-terra", "high")},
            )
            with self.assertRaises(OSError):
                write_test_configuration(
                    config, path=path,
                    operations=AtomicFileOperations(replace=reject_replace),
                )
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            self.assertEqual(list(Path(directory).glob(".test.yaml.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
