from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).parents[1]


class IntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name)
        for name in ("skills-src", "sources", "profiles", "registry"):
            shutil.copytree(ROOT / name, self.repo / name)

    def tearDown(self) -> None:
        self.temp.cleanup()

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
            tuple(sorted(report.issues, key=lambda issue: (issue.path, issue.code, issue.message))),
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


if __name__ == "__main__":
    unittest.main()
