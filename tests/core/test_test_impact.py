from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest

from hwskill.test_impact import (
    AffectedTestsBlocked,
    ImpactInventory,
    ImpactProfile,
    ImpactSkill,
    NameStatus,
    select_affected_tests,
    select_from_changes,
)


class TestImpactSelection(unittest.TestCase):
    def setUp(self) -> None:
        self.before = ImpactInventory(
            skills=(ImpactSkill("team/review", "skills-src/l1/team/review", "sha256:old", "l1"),),
            profiles=(ImpactProfile("review-workflow", ("team/review",)),),
        )
        self.after = ImpactInventory(
            skills=(ImpactSkill("team/review", "skills-src/l1/team/review", "sha256:new", "l1"),),
            profiles=(ImpactProfile("review-workflow", ("team/review",)),),
        )

    def test_skill_content_selects_skill_and_containing_profile(self) -> None:
        selection = select_from_changes(
            self.before, self.after, (NameStatus("M", "skills-src/l1/team/review/SKILL.md"),),
        )
        self.assertEqual(selection.skill_ids, ("team/review",))
        self.assertEqual(selection.profile_ids, ("review-workflow",))
        self.assertIn("skill-content:team/review", selection.reasons)
        self.assertEqual(
            tuple(path.as_posix() for path in selection.collection_paths),
            ("tests/profiles/review-workflow/test.yaml", "tests/skills/team/review/test.yaml"),
        )

    def test_new_unprofiled_skill_selects_just_its_collection(self) -> None:
        after = ImpactInventory(
            skills=(ImpactSkill("team/new", "skills-src/l1/team/new", "sha256:new", "l1"),),
            profiles=(),
        )
        selection = select_from_changes(
            ImpactInventory(), after, (NameStatus("A", "skills-src/l1/team/new/SKILL.md"),),
        )
        self.assertEqual(selection.skill_ids, ("team/new",))
        self.assertEqual(selection.profile_ids, ())

    def test_profile_membership_selects_profile(self) -> None:
        before = ImpactInventory(
            skills=(
                ImpactSkill("team/review", "skills-src/l1/team/review", "sha256:old", "l1"),
                ImpactSkill("team/new", "skills-src/l1/team/new", "sha256:new", "l1"),
            ),
            profiles=(ImpactProfile("review-workflow", ("team/review",)),),
        )
        after = ImpactInventory(
            skills=(
                ImpactSkill("team/review", "skills-src/l1/team/review", "sha256:old", "l1"),
                ImpactSkill("team/new", "skills-src/l1/team/new", "sha256:new", "l1"),
            ),
            profiles=(ImpactProfile("review-workflow", ("team/review", "team/new")),),
        )
        selection = select_from_changes(before, after, (NameStatus("M", "profiles/review-workflow.yaml"),))
        self.assertEqual(selection.skill_ids, ())
        self.assertEqual(selection.profile_ids, ("review-workflow",))

    def test_rename_selects_new_id_and_migrated_profile(self) -> None:
        after = ImpactInventory(
            skills=(ImpactSkill("team/renamed", "skills-src/l1/team/renamed", "sha256:old", "l1"),),
            profiles=(ImpactProfile("review-workflow", ("team/renamed",)),),
        )
        selection = select_from_changes(
            self.before, after,
            (NameStatus("R100", "skills-src/l1/team/review/SKILL.md", "skills-src/l1/team/renamed/SKILL.md"),),
        )
        self.assertEqual(selection.skill_ids, ("team/renamed",))
        self.assertEqual(selection.profile_ids, ("review-workflow",))

    def test_metadata_only_changes_with_unchanged_digest_select_nothing(self) -> None:
        unchanged = ImpactInventory(
            skills=(ImpactSkill("team/review", "skills-src/l2/team/review", "sha256:old", "l2"),),
            profiles=(ImpactProfile("review-workflow", ("team/review",)),),
        )
        selection = select_from_changes(self.before, unchanged, (NameStatus("M", "sources/team.yaml"),))
        self.assertFalse(selection.core)
        self.assertEqual(selection.skill_ids, ())
        self.assertEqual(selection.profile_ids, ())

    def test_test_fixture_and_runtime_changes_have_exact_targets(self) -> None:
        fixture = select_from_changes(self.before, self.before, (NameStatus("M", "tests/skills/team/review/fixtures/input.txt"),))
        self.assertEqual(fixture.skill_ids, ("team/review",))
        self.assertEqual(fixture.profile_ids, ())
        runtime = select_from_changes(self.before, self.after, (NameStatus("M", "src/hwskill/loader.py"),))
        self.assertTrue(runtime.core)
        self.assertEqual(runtime.skill_ids, ("team/review",))
        self.assertEqual(runtime.profile_ids, ("review-workflow",))

    def test_non_runtime_core_source_change_selects_core_only(self) -> None:
        selection = select_from_changes(self.before, self.before, (NameStatus("M", "src/hwskill/maintenance_transaction.py"),))
        self.assertTrue(selection.core)
        self.assertEqual(selection.skill_ids, ())
        self.assertEqual(selection.profile_ids, ())

    def test_git_selection_includes_untracked_and_parses_nul_rename(self) -> None:
        with TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
            (repo / "registry").mkdir()
            (repo / "profiles").mkdir()
            (repo / "skills-src/l1/team/review").mkdir(parents=True)
            (repo / "registry/catalog.json").write_text('{"schema_version":2,"skills":[{"id":"team/review","path":"skills-src/l1/team/review","content_digest":"sha256:old","layer":"l1"}]}', encoding="utf-8")
            (repo / "profiles/review-workflow.yaml").write_text("id: review-workflow\nskills: [team/review]\n", encoding="utf-8")
            (repo / "skills-src/l1/team/review/SKILL.md").write_text("old", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            (repo / "skills-src/l1/team/review/SKILL.md").write_text("new", encoding="utf-8")
            selection = select_affected_tests(repo, "HEAD")
            self.assertEqual(selection.skill_ids, ("team/review",))

    def test_invalid_git_base_is_distinctly_blocked(self) -> None:
        with TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "registry").mkdir()
            (repo / "registry/catalog.json").write_text('{"skills": []}', encoding="utf-8")
            with self.assertRaises(AffectedTestsBlocked):
                select_affected_tests(repo, "does-not-exist")
