from __future__ import annotations

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml


class TestManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, relative: str, data: object) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def write_raw_manifest(self, text: str) -> Path:
        path = self.repo / "tests/skills/team/review/test.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def skill_manifest(self, cases: list[dict], **overrides: object) -> Path:
        data: dict[str, object] = {
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": cases,
        }
        data.update(overrides)
        return self.write_manifest("tests/skills/team/review/test.yaml", data)

    @staticmethod
    def valid_case(**overrides: object) -> dict[str, object]:
        case: dict[str, object] = {
            "id": "review",
            "steps": [{"type": "command", "command": "python3 -m unittest -v"}],
            "post_check": {"type": "command", "command": "python3 check.py"},
        }
        case.update(overrides)
        return case

    def test_loads_immutable_collection_with_deterministic_action_ids(self) -> None:
        from hwskill.test_manifest import (
            AgentAction,
            CommandAction,
            load_test_collection,
        )

        collection = load_test_collection(
            self.skill_manifest([self.valid_case(
                prepare={"type": "command", "command": "python3 prepare.py"},
                steps=[
                    {"type": "command", "command": "python3 one.py"},
                    {"id": "ask", "type": "agent", "prompt": "inspect the output"},
                ],
                post_check={"type": "agent", "prompt": "return a verdict"},
            )]),
            self.repo,
        )

        self.assertEqual(collection.target.kind, "skill")
        self.assertEqual(collection.target.target_id, "team/review")
        self.assertEqual(collection.cases[0].case_id, "review")
        self.assertIsInstance(collection.cases[0].prepare, CommandAction)
        self.assertEqual(collection.cases[0].prepare.action_id, "prepare")
        self.assertEqual([action.action_id for action in collection.cases[0].steps], ["step-1", "ask"])
        self.assertIsInstance(collection.cases[0].steps[1], AgentAction)
        self.assertEqual(collection.cases[0].post_check.action_id, "post-check")
        self.assertTrue(collection.fixtures_dir.is_relative_to(self.repo))
        with self.assertRaisesRegex(Exception, "cannot assign"):
            collection.target.kind = "profile"

    def test_action_workdir_overrides_case_workdir_without_resolution(self) -> None:
        from hwskill.test_manifest import load_test_collection

        collection = load_test_collection(self.skill_manifest([self.valid_case(
            workdir="workspace",
            steps=[{
                "id": "test",
                "type": "command",
                "workdir": "workspace/project",
                "command": "python3 -m unittest -v",
            }],
        )]), self.repo)

        case = collection.cases[0]
        self.assertEqual(case.workdir, "workspace")
        self.assertEqual(case.steps[0].workdir, "workspace/project")

    def test_rejects_invalid_schema_shapes_and_unknown_keys(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        invalid = [
            ({"unexpected": True}, "unknown"),
            ({"schema_version": 2}, "schema_version"),
            ({"schema_version": True}, "schema_version"),
            ({"target": {"kind": "other", "id": "team/review"}}, "target"),
            ({"cases": [self.valid_case(unknown=True)]}, "unknown"),
            ({"cases": [self.valid_case(steps=[])]}, "steps"),
            ({"cases": [self.valid_case(post_check=None)]}, "post_check"),
            ({"cases": [self.valid_case(steps=[{"type": "unknown"}])]}, "action"),
            ({"cases": [self.valid_case(steps=[{"type": "command", "command": " "}])]}, "command"),
            ({"cases": [self.valid_case(steps=[{"type": "agent", "prompt": " "}])]}, "prompt"),
            ({"cases": [self.valid_case(steps=[{"type": "command", "command": "ok", "workdir": None}])]}, "workdir"),
            ({"cases": [self.valid_case(steps=[{"type": "command", "command": "ok", "prompt": "no"}])]}, "unknown"),
        ]
        for override, message in invalid:
            data: dict[str, object] = {
                "schema_version": 1,
                "target": {"kind": "skill", "id": "team/review"},
                "cases": [self.valid_case()],
            }
            data.update(override)
            path = self.write_manifest("tests/skills/team/review/test.yaml", data)
            with self.subTest(message=message):
                with self.assertRaisesRegex(TestManifestError, message):
                    load_test_collection(path, self.repo)

    def test_rejects_duplicate_yaml_keys_at_every_schema_level(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        manifests = {
            "schema_version": """
                schema_version: 1
                schema_version: 1
                target: {kind: skill, id: team/review}
                cases: []
            """,
            "id": """
                schema_version: 1
                target:
                  kind: skill
                  id: team/review
                  id: team/review
                cases: []
            """,
            "case id": """
                schema_version: 1
                target: {kind: skill, id: team/review}
                cases:
                  - id: review
                    id: review
                    steps: [{type: command, command: 'true'}]
                    post_check: {type: command, command: 'true'}
            """,
            "command": """
                schema_version: 1
                target: {kind: skill, id: team/review}
                cases:
                  - id: review
                    steps:
                      - type: command
                        command: first
                        command: second
                    post_check: {type: command, command: 'true'}
            """,
        }
        for key, text in manifests.items():
            path = self.write_raw_manifest(text)
            with self.subTest(key=key):
                with self.assertRaisesRegex(TestManifestError, f"{path}.*{key.split()[-1]}"):
                    load_test_collection(path, self.repo)

    def test_rejects_yaml_merge_keys_before_they_can_bypass_duplicate_key_validation(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        path = self.write_raw_manifest("""
            schema_version: 1
            target: {kind: skill, id: team/review}
            defaults: &command
              type: command
              command: 'true'
            cases:
              - id: review
                steps:
                  - <<: *command
                post_check: {type: command, command: 'true'}
        """)

        with self.assertRaisesRegex(TestManifestError, f"{path}.*merge"):
            load_test_collection(path, self.repo)

    def test_existing_fixtures_must_be_a_symlink_free_directory(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        path = self.skill_manifest([])
        fixtures = path.parent / "fixtures"
        external = Path(self.temp.name) / "external-fixtures"
        external.mkdir()

        fixtures.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(TestManifestError, "fixtures.*symlink"):
            load_test_collection(path, self.repo)
        fixtures.unlink()

        fixtures.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(TestManifestError, "fixtures.*directory"):
            load_test_collection(path, self.repo)
        fixtures.unlink()

        os.mkfifo(fixtures)
        with self.assertRaisesRegex(TestManifestError, "fixtures.*directory"):
            load_test_collection(path, self.repo)
        fixtures.unlink()

        fixtures.mkdir()
        (fixtures / "nested").mkdir()
        (fixtures / "nested" / "outside").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(TestManifestError, "fixtures.*symlink"):
            load_test_collection(path, self.repo)

    def test_nested_fixtures_allow_only_regular_files_and_real_directories(self) -> None:
        from hwskill.test_manifest import load_test_collection

        path = self.skill_manifest([])
        fixtures = path.parent / "fixtures"
        nested = fixtures / "nested" / "deeper"
        nested.mkdir(parents=True)
        (fixtures / "top-level.txt").write_text("top level\n", encoding="utf-8")
        (nested / "payload.txt").write_text("nested\n", encoding="utf-8")

        collection = load_test_collection(path, self.repo)

        self.assertEqual(collection.fixtures_dir, fixtures)

    def test_rejects_a_nested_fixture_fifo_with_relative_path_and_type(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        path = self.skill_manifest([])
        fixtures = path.parent / "fixtures"
        fixtures.mkdir()
        fifo = fixtures / "nested" / "input.fifo"
        fifo.parent.mkdir()
        os.mkfifo(fifo)

        with self.assertRaisesRegex(TestManifestError, r"nested/input\.fifo.*FIFO"):
            load_test_collection(path, self.repo)

    def test_rejects_a_nested_socket_fixture_mode_deterministically(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        path = self.skill_manifest([])
        fixtures = path.parent / "fixtures"
        fixtures.mkdir()
        socket_path = fixtures / "nested" / "service.sock"
        socket_path.parent.mkdir()
        socket_path.write_text("placeholder", encoding="utf-8")
        real_lstat = os.lstat

        def socket_lstat(candidate: Path | str) -> os.stat_result:
            if Path(candidate) == socket_path:
                return os.stat_result((stat.S_IFSOCK | 0o600,) * 10)
            return real_lstat(candidate)

        with patch("hwskill.test_manifest.os.lstat", side_effect=socket_lstat):
            with self.assertRaisesRegex(TestManifestError, r"nested/service\.sock.*socket"):
                load_test_collection(path, self.repo)

    def test_scalar_schema_fields_reject_list_mapping_and_null_as_manifest_errors(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        malformed = (
            lambda value: {"schema_version": value},
            lambda value: {"target": {"kind": value, "id": "team/review"}},
            lambda value: {"target": {"kind": "skill", "id": value}},
            lambda value: {"cases": [self.valid_case(id=value)]},
            lambda value: {"cases": [self.valid_case(description=value)]},
            lambda value: {"cases": [self.valid_case(workdir=value)]},
            lambda value: {"cases": [self.valid_case(steps=[{"id": value, "type": "command", "command": "true"}])]},
            lambda value: {"cases": [self.valid_case(steps=[{"type": value, "command": "true"}])]},
            lambda value: {"cases": [self.valid_case(steps=[{"type": "command", "workdir": value, "command": "true"}])]},
            lambda value: {"cases": [self.valid_case(steps=[{"type": "command", "command": value}])]},
            lambda value: {"cases": [self.valid_case(steps=[{"type": "agent", "prompt": value}])]},
        )
        for build in malformed:
            for value in ([], {"bad": "value"}, None):
                data: dict[str, object] = {
                    "schema_version": 1,
                    "target": {"kind": "skill", "id": "team/review"},
                    "cases": [self.valid_case()],
                }
                data.update(build(value))
                path = self.write_manifest("tests/skills/team/review/test.yaml", data)
                with self.subTest(build=build, value=value):
                    with self.assertRaises(TestManifestError):
                        load_test_collection(path, self.repo)

    def test_rejects_duplicate_case_and_action_ids_across_case_sections(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        invalid_cases = (
            [self.valid_case(), self.valid_case()],
            [self.valid_case(
                prepare={"id": "same", "type": "command", "command": "prepare"},
                steps=[{"id": "same", "type": "command", "command": "step"}],
            )],
            [self.valid_case(
                prepare={"type": "command", "command": "prepare"},
                steps=[{"id": "prepare", "type": "command", "command": "step"}],
            )],
        )
        for cases in invalid_cases:
            path = self.skill_manifest(cases)
            with self.subTest(cases=cases):
                with self.assertRaisesRegex(TestManifestError, "duplicate"):
                    load_test_collection(path, self.repo)

    def test_rejects_ambiguous_workdirs(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        for workdir in ("", " ", "/absolute", "../escape", "project/../escape", "folder\\child"):
            path = self.skill_manifest([self.valid_case(workdir=workdir)])
            with self.subTest(workdir=workdir):
                with self.assertRaisesRegex(TestManifestError, "workdir"):
                    load_test_collection(path, self.repo)

        dot = load_test_collection(self.skill_manifest([self.valid_case(workdir=".")]), self.repo)
        self.assertEqual(dot.cases[0].workdir, ".")

    def test_load_requires_a_regular_repo_contained_manifest_without_symlinks(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        valid = {"schema_version": 1, "target": {"kind": "skill", "id": "team/review"}, "cases": []}
        outside = Path(self.temp.name) / "outside.yaml"
        outside.write_text(yaml.safe_dump(valid), encoding="utf-8")
        linked = self.repo / "tests/skills/team/review/test.yaml"
        linked.parent.mkdir(parents=True)
        linked.symlink_to(outside)

        with self.assertRaisesRegex(TestManifestError, "symlink"):
            load_test_collection(linked, self.repo)
        with self.assertRaisesRegex(TestManifestError, "inside repository"):
            load_test_collection(outside, self.repo)

    def test_load_rejects_a_repository_root_below_a_symlinked_ancestor(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        external_repo = Path(self.temp.name) / "external" / "repo"
        manifest = external_repo / "tests/skills/team/review/test.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(yaml.safe_dump({
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [],
        }), encoding="utf-8")
        linked_parent = self.repo / "linked-parent"
        linked_parent.symlink_to(external_repo.parent, target_is_directory=True)
        linked_repo = linked_parent / "repo"

        with self.assertRaisesRegex(TestManifestError, "symlink"):
            load_test_collection(linked_repo / "tests/skills/team/review/test.yaml", linked_repo)

    def test_rejects_manifest_paths_that_do_not_match_the_declared_target(self) -> None:
        from hwskill.test_manifest import TestManifestError, load_test_collection

        wrong_skill = self.write_manifest("tests/skills/team/other/test.yaml", {
            "schema_version": 1,
            "target": {"kind": "skill", "id": "team/review"},
            "cases": [],
        })
        wrong_profile = self.write_manifest("tests/profiles/demo/test.yaml", {
            "schema_version": 1,
            "target": {"kind": "profile", "id": "other"},
            "cases": [],
        })
        wrong_root = self.write_manifest("tests/other/test.yaml", {
            "schema_version": 1,
            "target": {"kind": "profile", "id": "other"},
            "cases": [],
        })

        for path in (wrong_skill, wrong_profile, wrong_root):
            with self.subTest(path=path):
                with self.assertRaisesRegex(TestManifestError, "path"):
                    load_test_collection(path, self.repo)

    def test_discovery_is_sorted_and_rejects_symlink_aliases(self) -> None:
        from hwskill.test_manifest import TestManifestError, discover_test_collections

        self.write_manifest("tests/profiles/demo/test.yaml", {
            "schema_version": 1, "target": {"kind": "profile", "id": "demo"}, "cases": [],
        })
        self.write_manifest("tests/skills/team/review/test.yaml", {
            "schema_version": 1, "target": {"kind": "skill", "id": "team/review"}, "cases": [],
        })
        collections = discover_test_collections(self.repo)
        self.assertEqual(
            [(item.target.kind, item.target.target_id) for item in collections],
            [("profile", "demo"), ("skill", "team/review")],
        )

        duplicate = self.write_manifest("tests/skills/team/duplicate/test.yaml", {
            "schema_version": 1, "target": {"kind": "skill", "id": "team/duplicate"}, "cases": [],
        })
        alias = self.repo / "tests/skills/team/alias/test.yaml"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(duplicate)
        with self.assertRaisesRegex(TestManifestError, "symlink"):
            discover_test_collections(self.repo)


if __name__ == "__main__":
    unittest.main()
