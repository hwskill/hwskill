from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from hwskill.digest import content_digest
from hwskill.profiles import set_profiles
from hwskill.scopes import project_scope


ROOT = Path(__file__).parents[1]


class IntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.external = TemporaryDirectory()
        self.repo = Path(self.temp.name)
        for name in ("skills-src", "sources", "profiles", "registry"):
            shutil.copytree(ROOT / name, self.repo / name)

    def tearDown(self) -> None:
        self.temp.cleanup()
        self.external.cleanup()

    def make_orphan_skill(self, relative_path: str) -> None:
        skill = self.repo / relative_path / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: orphan\ndescription: missing governance\n---\n",
            encoding="utf-8",
        )

    def read_source(self, name: str = "superpowers") -> dict:
        return yaml.safe_load((self.repo / "sources" / f"{name}.yaml").read_text(encoding="utf-8"))

    def write_source(self, source: dict, name: str = "superpowers") -> None:
        (self.repo / "sources" / f"{name}.yaml").write_text(
            yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def update_governance(self, relative_path: str, update) -> dict:
        """Apply a provenance-only fixture change while preserving a valid local digest."""
        path = self.repo / relative_path / "skill.yaml"
        governance = yaml.safe_load(path.read_text(encoding="utf-8"))
        update(governance)
        path.write_text(yaml.safe_dump(governance, allow_unicode=True, sort_keys=False), encoding="utf-8")
        governance["content_digest"] = content_digest(path.parent)
        path.write_text(yaml.safe_dump(governance, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return governance

    def test_integrity_collects_orphan_skill_and_stale_catalog_together(self) -> None:
        """Removing governance or regenerating Catalog independently must be reported."""
        from hwskill.integrity import check_integrity

        self.make_orphan_skill("skills-src/l1/team/orphan")
        (self.repo / "registry/catalog.json").write_text("{}\n", encoding="utf-8")

        report = check_integrity(self.repo)

        self.assertEqual(
            {issue.code for issue in report.issues},
            {"orphan-skill", "catalog-stale"},
        )

    def test_integrity_orders_independent_parser_failures_deterministically(self) -> None:
        """A bad source manifest cannot hide an unrelated orphan Skill."""
        from hwskill.integrity import check_integrity

        self.make_orphan_skill("skills-src/l1/team/orphan")
        (self.repo / "sources/broken.yaml").write_text("source: [\n", encoding="utf-8")

        report = check_integrity(self.repo)

        self.assertEqual(
            report.issues,
            tuple(sorted(report.issues)),
        )
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"invalid-source-manifest", "orphan-skill"},
        )

    def test_clean_inventory_has_deterministic_counts(self) -> None:
        """A clean repository exposes its complete Skill, source, and Profile inventory."""
        from hwskill.integrity import check_integrity

        report = check_integrity(self.repo)

        self.assertTrue(report.ok)
        self.assertEqual((report.skill_count, report.source_count, report.profile_count), (17, 1, 3))
        self.assertEqual(report.issues, ())

    def test_require_integrity_exposes_the_complete_report(self) -> None:
        """Callers receive every issue when they choose an exception-based gate."""
        from hwskill.integrity import IntegrityError, require_integrity

        self.make_orphan_skill("skills-src/l1/team/orphan")
        with self.assertRaises(IntegrityError) as raised:
            require_integrity(self.repo)

        self.assertFalse(raised.exception.report.ok)
        self.assertIn("orphan-skill", {issue.code for issue in raised.exception.report.issues})

    def test_inventory_rejects_a_symlinked_skill_payload_before_registry_parsing(self) -> None:
        """A payload link must not let a focused validator read outside the repository."""
        from hwskill.integrity import check_integrity

        outside = self.repo.parent / "outside-payload.txt"
        outside.write_text("outside repository\n", encoding="utf-8")
        payload = self.repo / "skills-src/l1/local/chinese-thinking/external.txt"
        payload.symlink_to(outside)

        report = check_integrity(self.repo)

        self.assertIn("unsafe-path", {issue.code for issue in report.issues})
        self.assertNotIn("invalid-registry", {issue.code for issue in report.issues})

    def test_symlinked_registry_is_not_read_as_an_invalid_catalog(self) -> None:
        """An unsafe Catalog parent stops parsing before external JSON is reached."""
        from hwskill.integrity import check_integrity

        external_registry = Path(self.external.name) / "registry"
        external_registry.mkdir()
        (external_registry / "catalog.json").write_text("{ invalid json", encoding="utf-8")
        shutil.rmtree(self.repo / "registry")
        (self.repo / "registry").symlink_to(external_registry, target_is_directory=True)

        report = check_integrity(self.repo)

        self.assertIn("unsafe-path", {issue.code for issue in report.issues})
        self.assertNotIn("invalid-catalog", {issue.code for issue in report.issues})

    def test_symlinked_tests_parent_is_not_read_as_a_test_manifest(self) -> None:
        """A linked tests directory cannot inject an external malformed manifest."""
        from hwskill.integrity import check_integrity

        external_tests = Path(self.external.name) / "tests"
        external_manifest = external_tests / "skills/example/test.yaml"
        external_manifest.parent.mkdir(parents=True)
        external_manifest.write_text("[ invalid yaml", encoding="utf-8")
        (self.repo / "tests").symlink_to(external_tests, target_is_directory=True)

        report = check_integrity(self.repo)

        self.assertIn("unsafe-path", {issue.code for issue in report.issues})
        self.assertNotIn("invalid-test-manifest", {issue.code for issue in report.issues})

    def test_repeated_inventory_checks_report_one_unsafe_path(self) -> None:
        """One physical link must not emit one unsafe finding per inventory pass."""
        from hwskill.integrity import check_integrity

        external_skills = Path(self.external.name) / "skills"
        external_skills.mkdir()
        shutil.rmtree(self.repo / "skills-src")
        (self.repo / "skills-src").symlink_to(external_skills, target_is_directory=True)

        report = check_integrity(self.repo)

        self.assertEqual(
            len([issue for issue in report.issues if issue.code == "unsafe-path" and issue.path == "skills-src"]),
            1,
        )

    def test_symlinked_repo_root_does_not_read_external_invalid_catalog(self) -> None:
        """The entrypoint itself must not resolve a linked repository root."""
        from hwskill.integrity import IntegrityError, check_integrity, require_integrity

        (self.repo / "registry/catalog.json").write_text("{ invalid json", encoding="utf-8")
        linked_root = Path(self.external.name) / "linked-repository"
        linked_root.symlink_to(self.repo, target_is_directory=True)

        report = check_integrity(linked_root)

        self.assertEqual((report.skill_count, report.source_count, report.profile_count), (0, 0, 0))
        self.assertEqual([issue.code for issue in report.issues], ["unsafe-path"])
        self.assertEqual(report.issues[0].path, ".")
        with self.assertRaises(IntegrityError) as raised:
            require_integrity(linked_root)
        self.assertEqual(raised.exception.report, report)

    def test_broken_repo_root_link_is_a_stable_unsafe_issue(self) -> None:
        """A broken root link is rejected without any parser traceback."""
        from hwskill.integrity import check_integrity

        linked_root = Path(self.external.name) / "broken-repository"
        linked_root.symlink_to(Path(self.external.name) / "missing-target", target_is_directory=True)

        report = check_integrity(linked_root)

        self.assertEqual((report.skill_count, report.source_count, report.profile_count), (0, 0, 0))
        self.assertEqual([issue.code for issue in report.issues], ["unsafe-path"])
        self.assertEqual(report.issues[0].path, ".")

    def test_non_directory_repo_root_is_a_stable_unsafe_issue(self) -> None:
        """A regular file cannot be treated as an empty repository inventory."""
        from hwskill.integrity import check_integrity

        root_file = Path(self.external.name) / "not-a-repository"
        root_file.write_text("not a directory", encoding="utf-8")

        report = check_integrity(root_file)

        self.assertEqual((report.skill_count, report.source_count, report.profile_count), (0, 0, 0))
        self.assertEqual([issue.code for issue in report.issues], ["unsafe-path"])
        self.assertEqual(report.issues[0].path, ".")

    def test_integrity_reports_source_digest_drift_without_fetching(self) -> None:
        """The checked-in source digest, not a remote checkout, owns snapshot integrity."""
        from hwskill.integrity import check_integrity

        source = self.read_source()
        source["resolved"]["skills"][0]["content_digest"] = "sha256:" + "0" * 64
        self.write_source(source)

        report = check_integrity(self.repo)

        self.assertIn("source-skill-digest-mismatch", {issue.code for issue in report.issues})

    def test_integrity_reports_manual_skill_claimed_by_a_resolved_source(self) -> None:
        """Manual ownership cannot coexist with a source manifest resolved entry."""
        from hwskill.integrity import check_integrity

        source = self.read_source()
        item = source["resolved"]["skills"][0]
        skill = self.repo / "skills-src/l1/superpowers/brainstorming"
        governance = self.update_governance(
            skill.relative_to(self.repo).as_posix(),
            lambda data: data.__setitem__("source", {"kind": "manual"}),
        )
        item["content_digest"] = governance["content_digest"]
        self.write_source(source)

        report = check_integrity(self.repo)

        self.assertIn("manual-skill-resolved", {issue.code for issue in report.issues})

    def test_integrity_reports_an_upstream_skill_missing_from_all_resolved_sources(self) -> None:
        """Every upstream governance record must have exactly one source ownership entry."""
        from hwskill.integrity import check_integrity

        self.update_governance(
            "skills-src/l1/local/chinese-thinking",
            lambda data: data.__setitem__("source", {
                "kind": "upstream", "source_id": "missing", "revision": "a" * 40,
                "upstream_path": "chinese-thinking",
            }),
        )

        report = check_integrity(self.repo)

        self.assertIn("upstream-skill-unresolved", {issue.code for issue in report.issues})

    def test_integrity_reports_duplicate_source_ownership(self) -> None:
        """One resolved Skill ID must not be owned by two source manifests."""
        from hwskill.integrity import check_integrity

        duplicate = self.read_source()
        self.write_source(duplicate, "duplicate")

        report = check_integrity(self.repo)

        self.assertIn("duplicate-source-skill", {issue.code for issue in report.issues})

    def test_duplicate_source_issues_keep_the_same_canonical_path_when_inventory_reverses(self) -> None:
        """Duplicate groups must not expose the filesystem traversal order in their issue path."""
        from hwskill import integrity

        self.write_source(self.read_source(), "a-source")
        baseline = integrity.check_integrity(self.repo)
        original_inventory_files = integrity._inventory_files

        def reversed_inventory(*args, **kwargs):
            yield from reversed(tuple(original_inventory_files(*args, **kwargs)))

        with patch.object(integrity, "_inventory_files", side_effect=reversed_inventory):
            reversed_report = integrity.check_integrity(self.repo)

        self.assertEqual(baseline.issues, reversed_report.issues)
        duplicate_issues = [
            issue for issue in baseline.issues
            if issue.code in {"duplicate-source-id", "duplicate-source-skill"}
        ]
        self.assertTrue(duplicate_issues)
        self.assertTrue(all(issue.path == "sources/a-source.yaml" for issue in duplicate_issues))

    def test_integrity_reports_two_ids_for_one_path_inside_an_invalid_source(self) -> None:
        """Raw source inspection retains path-to-ID ownership evidence after parser rejection."""
        from hwskill.integrity import check_integrity

        source = self.read_source()
        duplicate = dict(source["resolved"]["skills"][0])
        duplicate["id"] = "other/brainstorming"
        source["resolved"]["skills"].append(duplicate)
        self.write_source(source)

        report = check_integrity(self.repo)

        conflict = next(issue for issue in report.issues if issue.code == "duplicate-source-path")
        self.assertIn("sources/superpowers.yaml", conflict.message)
        self.assertIn("superpowers/brainstorming", conflict.message)
        self.assertIn("other/brainstorming", conflict.message)

    def test_integrity_reports_two_ids_for_one_path_across_duplicate_source_manifests(self) -> None:
        """Source-path ownership remains unique even when the duplicate lives in another file."""
        from hwskill.integrity import check_integrity

        duplicate = self.read_source()
        duplicate["resolved"]["skills"][0]["id"] = "other/brainstorming"
        self.write_source(duplicate, "duplicate")

        report = check_integrity(self.repo)

        conflict = next(issue for issue in report.issues if issue.code == "duplicate-source-path")
        self.assertIn("sources/duplicate.yaml", conflict.message)
        self.assertIn("sources/superpowers.yaml", conflict.message)
        self.assertIn("superpowers/brainstorming", conflict.message)
        self.assertIn("other/brainstorming", conflict.message)

    def test_integrity_reports_duplicate_source_id_from_an_invalid_manifest(self) -> None:
        """A syntactically usable source ID survives unrelated manifest schema failure."""
        from hwskill.integrity import check_integrity

        invalid = self.read_source()
        invalid["unexpected"] = "schema error"
        self.write_source(invalid, "invalid")

        report = check_integrity(self.repo)

        self.assertTrue({"invalid-source-manifest", "duplicate-source-id"} <= {
            issue.code for issue in report.issues
        })

    def test_integrity_reports_resolved_ignore_overlap_even_when_manifest_is_invalid(self) -> None:
        """Raw overlap inspection keeps the actionable conflict visible after parser rejection."""
        from hwskill.integrity import check_integrity

        source = self.read_source()
        source["upstream"]["ignore"] = [{"path": "brainstorming", "reason": "conflict"}]
        self.write_source(source)

        report = check_integrity(self.repo)

        self.assertTrue({"invalid-source-manifest", "source-resolved-ignore-overlap"} <= {
            issue.code for issue in report.issues
        })

    def test_integrity_reports_source_metadata_mismatches_independently(self) -> None:
        """Source, governance, and physical Skill location expose distinct bad edges."""
        from hwskill.integrity import check_integrity

        source = self.read_source()
        source["resolved"]["revision"] = "0" * 40
        source["resolved"]["skills"][0]["layer"] = "l2"
        self.write_source(source)
        self.update_governance(
            "skills-src/l1/superpowers/brainstorming",
            lambda data: data["source"].update({"source_id": "wrong", "upstream_path": "wrong-path"}),
        )

        report = check_integrity(self.repo)

        codes = {issue.code for issue in report.issues}
        self.assertTrue({
            "source-skill-source-mismatch", "source-skill-path-mismatch",
            "source-skill-layer-mismatch", "source-skill-revision-mismatch",
        } <= codes)

    def test_integrity_reports_a_resolved_id_mismatch_by_its_source_path(self) -> None:
        """A source path can identify the governed Skill even when its resolved ID drifts."""
        from hwskill.integrity import check_integrity

        source = self.read_source()
        source["resolved"]["skills"][0]["id"] = "superpowers/renamed-brainstorming"
        self.write_source(source)

        report = check_integrity(self.repo)

        self.assertIn("source-skill-id-mismatch", {issue.code for issue in report.issues})

    def test_integrity_reports_precise_catalog_entry_differences_and_one_stale_issue(self) -> None:
        """Catalog diagnostics distinguish its generated-byte drift from individual bad entries."""
        from hwskill.integrity import check_integrity

        catalog_path = self.repo / "registry/catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        removed = catalog["skills"].pop(0)
        catalog["skills"][0]["description"] = "wrong description"
        catalog["skills"].append({**removed, "id": "extra/skill"})
        catalog_path.write_text(json.dumps(catalog, sort_keys=True), encoding="utf-8")

        report = check_integrity(self.repo)

        codes = [issue.code for issue in report.issues]
        self.assertTrue({"catalog-extra-skill", "catalog-missing-skill", "catalog-skill-mismatch"} <= set(codes))
        self.assertEqual(codes.count("catalog-stale"), 1)

    def test_integrity_aggregates_repository_profile_pair_failures(self) -> None:
        """Repository profile bindings must carry a matching, current lock pair."""
        from hwskill.integrity import check_integrity

        stale_project = self.repo / "examples/stale-lock"
        stale_project.mkdir(parents=True)
        stale_target = project_scope(stale_project)
        set_profiles(stale_target, self.repo, ("codex-demo",))
        stale_lock = yaml.safe_load((stale_project / ".hwskills/lock.yaml").read_text(encoding="utf-8"))
        stale_lock["catalog_digest"] = "sha256:stale"
        (stale_project / ".hwskills/lock.yaml").write_text(
            yaml.safe_dump(stale_lock, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        missing_lock = self.repo / "examples/missing-lock/.hwskills"
        missing_lock.mkdir(parents=True)
        (missing_lock / "profile.yaml").write_text(
            "schema_version: 1\nprofiles:\n- codex-demo\n", encoding="utf-8"
        )

        missing_profile = self.repo / "examples/missing-profile/.hwskills"
        missing_profile.mkdir(parents=True)
        (missing_profile / "lock.yaml").write_text(
            "schema_version: 1\ncatalog_digest: sha256:stale\nskills: []\n", encoding="utf-8"
        )

        malformed_profile = self.repo / "examples/malformed-profile/.hwskills"
        malformed_profile.mkdir(parents=True)
        (malformed_profile / "profile.yaml").write_text("profiles: [\n", encoding="utf-8")
        (malformed_profile / "lock.yaml").write_text("skills: []\n", encoding="utf-8")

        malformed_lock = self.repo / "examples/malformed-lock/.hwskills"
        malformed_lock.mkdir(parents=True)
        (malformed_lock / "profile.yaml").write_text(
            "schema_version: 1\nprofiles:\n- codex-demo\n", encoding="utf-8"
        )
        (malformed_lock / "lock.yaml").write_text("skills: [\n", encoding="utf-8")

        report = check_integrity(self.repo)

        self.assertTrue({
            "missing-profile-lock", "missing-profile-selection", "stale-profile-lock",
            "invalid-profile-selection", "invalid-lock",
        } <= {issue.code for issue in report.issues})

    def test_integrity_reports_unknown_skill_in_repository_profile_definition(self) -> None:
        """A definition cannot silently retain a deleted or renamed Skill ID."""
        from hwskill.integrity import check_integrity

        profile_path = self.repo / "profiles/codex-demo.yaml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["skills"].append("missing/renamed-skill")
        profile_path.write_text(
            yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        report = check_integrity(self.repo)

        self.assertIn("unknown-profile-skill", {issue.code for issue in report.issues})

    def test_repository_profile_scan_ignores_artifacts_but_checks_examples(self) -> None:
        """Generated artifacts are not bindings, while checked-in examples remain bindings."""
        from hwskill.integrity import check_integrity

        artifact_project = self.repo / "artifacts/run-1"
        artifact_project.mkdir(parents=True)
        set_profiles(project_scope(artifact_project), self.repo, ("codex-demo",))
        artifact_lock = artifact_project / ".hwskills/lock.yaml"
        artifact_data = yaml.safe_load(artifact_lock.read_text(encoding="utf-8"))
        artifact_data["catalog_digest"] = "sha256:stale"
        artifact_lock.write_text(
            yaml.safe_dump(artifact_data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        example_project = self.repo / "examples/stale-example"
        example_project.mkdir(parents=True)
        set_profiles(project_scope(example_project), self.repo, ("codex-demo",))
        example_lock = example_project / ".hwskills/lock.yaml"
        example_data = yaml.safe_load(example_lock.read_text(encoding="utf-8"))
        example_data["catalog_digest"] = "sha256:stale"
        example_lock.write_text(
            yaml.safe_dump(example_data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        report = check_integrity(self.repo)

        stale_paths = [
            issue.path for issue in report.issues if issue.code == "stale-profile-lock"
        ]
        self.assertEqual(stale_paths, ["examples/stale-example/.hwskills/lock.yaml"])

    def test_repository_profile_binding_types_are_invalid_not_missing(self) -> None:
        """Directories and links named as bindings never masquerade as absent files."""
        from hwskill.integrity import check_integrity

        profile_directory = self.repo / "examples/profile-directory/.hwskills"
        profile_directory.mkdir(parents=True)
        (profile_directory / "profile.yaml").mkdir()
        (profile_directory / "lock.yaml").write_text("skills: []\n", encoding="utf-8")

        lock_directory = self.repo / "examples/lock-directory/.hwskills"
        lock_directory.mkdir(parents=True)
        (lock_directory / "profile.yaml").write_text(
            "schema_version: 1\nprofiles:\n- codex-demo\n", encoding="utf-8"
        )
        (lock_directory / "lock.yaml").mkdir()

        external_profile = Path(self.external.name) / "profile.yaml"
        external_profile.write_text("schema_version: 1\nprofiles: []\n", encoding="utf-8")
        linked_profile = self.repo / "examples/profile-link/.hwskills"
        linked_profile.mkdir(parents=True)
        (linked_profile / "profile.yaml").symlink_to(external_profile)
        (linked_profile / "lock.yaml").write_text("skills: []\n", encoding="utf-8")

        report = check_integrity(self.repo)

        codes_by_path = {
            path: {issue.code for issue in report.issues if issue.path == path}
            for path in (
                "examples/profile-directory/.hwskills/profile.yaml",
                "examples/lock-directory/.hwskills/lock.yaml",
                "examples/profile-link/.hwskills/profile.yaml",
            )
        }
        self.assertEqual(codes_by_path["examples/profile-directory/.hwskills/profile.yaml"], {
            "invalid-profile-selection",
        })
        self.assertEqual(codes_by_path["examples/lock-directory/.hwskills/lock.yaml"], {
            "invalid-lock",
        })
        self.assertEqual(codes_by_path["examples/profile-link/.hwskills/profile.yaml"], {
            "invalid-profile-selection",
        })
        self.assertNotIn("missing-profile-lock", {issue.code for issue in report.issues})
        self.assertNotIn("missing-profile-selection", {issue.code for issue in report.issues})

    def test_repository_profile_scan_prunes_generated_directories_at_every_depth(self) -> None:
        """Offline generated-directory defaults match ignored build/runtime trees, not examples."""
        from hwskill.integrity import check_integrity

        def write_stale_pair(relative: str) -> None:
            project = self.repo / relative
            project.mkdir(parents=True)
            set_profiles(project_scope(project), self.repo, ("codex-demo",))
            lock_path = project / ".hwskills/lock.yaml"
            lock_data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
            lock_data["catalog_digest"] = "sha256:stale"
            lock_path.write_text(
                yaml.safe_dump(lock_data, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )

        for relative in (
            ".runtime-deps/run", "nested/.runtime-deps/run",
            ".worktrees/run", "nested/.worktrees/run",
            "build/run", "nested/build/run",
            "package.egg-info/run", "nested/package.egg-info/run",
        ):
            write_stale_pair(relative)
        write_stale_pair("examples/stale-example")

        report = check_integrity(self.repo)

        self.assertEqual(
            [issue.path for issue in report.issues if issue.code == "stale-profile-lock"],
            ["examples/stale-example/.hwskills/lock.yaml"],
        )

    def test_repository_profile_scan_covers_simple_gitignore_directory_patterns(self) -> None:
        """Offline pruning stays synchronized with the repository's simple ignore patterns."""
        from hwskill.integrity import _REPOSITORY_PROFILE_SCAN_EXCLUDED_DIRECTORY_PATTERNS

        simple_patterns = {
            line.strip().rstrip("/")
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "!"))
        }

        self.assertTrue(simple_patterns <= _REPOSITORY_PROFILE_SCAN_EXCLUDED_DIRECTORY_PATTERNS)

    def test_repository_profile_scan_prunes_nested_python_bytecode_directories(self) -> None:
        """The .gitignore bytecode glob excludes generated bindings at every depth."""
        from hwskill.integrity import check_integrity

        def write_stale_pair(relative: str) -> None:
            project = self.repo / relative
            project.mkdir(parents=True)
            set_profiles(project_scope(project), self.repo, ("codex-demo",))
            lock_path = project / ".hwskills/lock.yaml"
            lock_data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
            lock_data["catalog_digest"] = "sha256:stale"
            lock_path.write_text(
                yaml.safe_dump(lock_data, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )

        for suffix in ("pyc", "pyo", "pyd"):
            write_stale_pair(f"nested/bogus.{suffix}/run")
        write_stale_pair("examples/stale-example")

        report = check_integrity(self.repo)

        self.assertEqual(
            [issue.path for issue in report.issues if issue.code == "stale-profile-lock"],
            ["examples/stale-example/.hwskills/lock.yaml"],
        )

    def test_test_manifest_reference_headers_report_unknown_duplicate_and_malformed_targets(self) -> None:
        """Reference validation catches stale rename targets without parsing or running cases."""
        from hwskill.integrity import validate_test_manifest_references

        def write_manifest(relative: str, body: str) -> None:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

        write_manifest(
            "tests/skills/unknown/test.yaml",
            "schema_version: 1\ntarget:\n  kind: skill\n  id: missing/renamed-skill\ncases: []\n",
        )
        write_manifest(
            "tests/profiles/unknown/test.yaml",
            "schema_version: 1\ntarget:\n  kind: profile\n  id: missing-profile\ncases: []\n",
        )
        write_manifest(
            "tests/skills/a/test.yaml",
            "schema_version: 1\ntarget:\n  kind: skill\n  id: local/chinese-thinking\ncases: []\n",
        )
        write_manifest(
            "tests/skills/b/test.yaml",
            "schema_version: 1\ntarget:\n  kind: skill\n  id: local/chinese-thinking\ncases: []\n",
        )
        write_manifest(
            "tests/skills/bad-schema/test.yaml",
            "schema_version: 2\ntarget:\n  kind: skill\n  id: local/chinese-thinking\ncases: []\n",
        )

        issues = validate_test_manifest_references(
            self.repo,
            {"local/chinese-thinking"},
            {"codex-demo"},
        )

        self.assertTrue({
            "unknown-test-skill", "unknown-test-profile", "duplicate-test-target", "invalid-test-manifest",
        } <= {issue.code for issue in issues})
        duplicate = next(issue for issue in issues if issue.code == "duplicate-test-target")
        self.assertEqual(duplicate.path, "tests/skills/a/test.yaml")


if __name__ == "__main__":
    unittest.main()
