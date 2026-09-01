from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


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


if __name__ == "__main__":
    unittest.main()
