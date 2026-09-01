from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from hwskill.digest import content_digest
from hwskill.pending_verification import VerificationResult, load_pending_verification
from hwskill.git_source import ResolvedTrack
from hwskill.source_manifest import (
    IgnoredSkill,
    ResolvedSourceSkill,
    SourceDefaults,
    UpstreamConfig,
    UpstreamSource,
    load_source_manifest,
    write_source_manifest,
)


SKILL_TEXT = "---\nname: review\ndescription: review description\n---\n\nReview carefully.\n"


class FakeGit:
    def __init__(self, upstream: Path, revision: str = "b" * 40) -> None:
        self.upstream = upstream
        self.revision = revision

    def materialize(self, repository: str, track: str, destination: Path) -> ResolvedTrack:
        shutil.copytree(self.upstream / "skills", destination / "skills", symlinks=True)
        return ResolvedTrack(track=track, kind="branch", commit=self.revision)


class HistoricalFakeGit:
    def __init__(self, snapshots: dict[str, Path], commits: dict[str, str]) -> None:
        self.snapshots = snapshots
        self.commits = commits
        self.tracks: list[str] = []

    def materialize(self, repository: str, track: str, destination: Path) -> ResolvedTrack:
        self.tracks.append(track)
        shutil.copytree(self.snapshots[track] / "skills", destination / "skills", symlinks=True)
        return ResolvedTrack(
            track=track,
            kind="commit" if len(track) == 40 else "branch",
            commit=self.commits[track],
        )


class SkillMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.upstream = self.root / "upstream"
        self._upstream_skill("review", SKILL_TEXT)
        self.git = FakeGit(self.upstream)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def apply_verified(self, plan: object) -> None:
        plan.verify_affected = lambda selection: VerificationResult(
            selection, dict(plan.candidate_digests), tuple((case_id, "PASS") for case_id in selection.required_case_ids),
        )
        plan.apply()

    def _upstream_skill(self, path: str, text: str) -> Path:
        skill = self.upstream / "skills" / path
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(text, encoding="utf-8")
        return skill

    def make_manual_skill(self, skill_id: str, *, layer: str = "l2", text: str = SKILL_TEXT) -> Path:
        namespace, name = skill_id.split("/", 1)
        skill = self.repo / "skills-src" / layer / namespace / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(text, encoding="utf-8")
        (skill / "skill.yaml").write_text(yaml.safe_dump({
            "schema_version": 1,
            "id": skill_id,
            "name": name,
            "description": "review description",
            "layer": layer,
            "status": "experimental",
            "source": {"kind": "manual"},
            "license": "MIT",
            "content_digest": content_digest(skill),
        }, sort_keys=False), encoding="utf-8")
        return skill

    def make_upstream_skill(self, skill_id: str = "team/review", *, layer: str = "l2", path: str = "review") -> Path:
        skill = self.make_manual_skill(skill_id, layer=layer)
        governance = yaml.safe_load((skill / "skill.yaml").read_text(encoding="utf-8"))
        governance["source"] = {
            "kind": "upstream", "source_id": "team", "revision": "a" * 40, "upstream_path": path,
        }
        (skill / "skill.yaml").write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")
        source = UpstreamSource(
            "team",
            UpstreamConfig("https://team.example/team.git", "refs/heads/main", "skills", ()),
            SourceDefaults("team", "l2", "MIT"),
            "a" * 40,
            (ResolvedSourceSkill(path, skill_id, layer, content_digest(skill)),),
        )
        write_source_manifest(self.repo / "sources/team.yaml", source)
        return skill

    def write_profile(self, *skill_ids: str) -> None:
        (self.repo / "profiles").mkdir(exist_ok=True)
        (self.repo / "profiles/demo.yaml").write_text(yaml.safe_dump({
            "schema_version": 1, "id": "demo", "description": "demo", "skills": list(skill_ids),
        }, sort_keys=False), encoding="utf-8")

    def write_skill_test(self, skill_id: str) -> Path:
        path = self.repo / "tests" / "skills" / "team" / "review" / "test.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({
            "schema_version": 1, "target": {"kind": "skill", "id": skill_id}, "cases": [],
        }, sort_keys=False), encoding="utf-8")
        return path

    def test_create_and_update_manual_use_repository_content_only(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_create_manual, plan_update_manual

        created = plan_create_manual(self.repo, "team/review", "l2", "review description", "MIT")
        self.assertEqual(created.selection.skill_ids, ("team/review",))
        self.assertEqual(created.selection.required_case_ids, ("skill:team/review",))
        self.apply_verified(created)
        skill = self.repo / "skills-src/l2/team/review"
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertEqual(yaml.safe_load((skill / "skill.yaml").read_text(encoding="utf-8"))["source"], {"kind": "manual"})
        with self.assertRaisesRegex(SkillMaintenanceError, "already exists"):
            plan_create_manual(self.repo, "team/review", "l2", "other", "MIT")

        (skill / "SKILL.md").write_text(SKILL_TEXT + "\nChanged.\n", encoding="utf-8")
        self.apply_verified(plan_update_manual(self.repo, "team/review"))
        governance = yaml.safe_load((skill / "skill.yaml").read_text(encoding="utf-8"))
        self.assertEqual(governance["source"], {"kind": "manual"})
        self.assertEqual(governance["content_digest"], content_digest(skill))

        upstream = self.make_upstream_skill("team/other")
        with self.assertRaisesRegex(SkillMaintenanceError, "source update.*overwrite"):
            plan_update_manual(self.repo, "team/other")
        self.assertTrue(upstream.exists())

    def test_skip_tests_accepts_authoritative_non_lowercase_underscore_skill_id(self) -> None:
        from hwskill.skill_maintenance import plan_create_manual

        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        plan = plan_create_manual(self.repo, "team/Review_Name", "l2", "review description", "MIT")

        plan.apply(skip_tests=True)

        self.assertTrue((self.repo / "skills-src/l2/team/Review_Name/SKILL.md").is_file())
        pending_record = load_pending_verification(self.repo)
        self.assertIsNotNone(pending_record)
        self.assertEqual(pending_record.selection, plan.selection)

    def test_move_changes_physical_layer_but_rename_migrates_identity_references(self) -> None:
        from hwskill.skill_maintenance import plan_move_skill, plan_rename_skill

        self.make_upstream_skill()
        self.write_profile("team/review")
        test_path = self.write_skill_test("team/review")
        plan_move_skill(self.repo, "team/review", "l1").apply()
        moved = self.repo / "skills-src/l1/team/review"
        self.assertTrue(moved.is_dir())
        self.assertFalse((self.repo / "skills-src/l2/team/review").exists())
        self.assertEqual(yaml.safe_load((moved / "skill.yaml").read_text(encoding="utf-8"))["id"], "team/review")
        self.assertEqual(load_source_manifest(self.repo / "sources/team.yaml").skills[0].layer, "l1")

        self.apply_verified(plan_rename_skill(self.repo, "team/review", "team/check"))
        renamed = self.repo / "skills-src/l1/team/check"
        self.assertTrue(renamed.is_dir())
        self.assertEqual(yaml.safe_load((renamed / "skill.yaml").read_text(encoding="utf-8"))["id"], "team/check")
        self.assertEqual(load_source_manifest(self.repo / "sources/team.yaml").skills[0].skill_id, "team/check")
        self.assertEqual(yaml.safe_load((self.repo / "profiles/demo.yaml").read_text(encoding="utf-8"))["skills"], ["team/check"])
        self.assertEqual(yaml.safe_load(test_path.read_text(encoding="utf-8"))["target"]["id"], "team/check")

    def test_delete_requires_explicit_profile_policy_and_never_deletes_test_references(self) -> None:
        from hwskill.integrity import IntegrityError
        from hwskill.skill_maintenance import SkillReferencedError, find_skill_references, plan_delete_skill

        self.make_manual_skill("team/review")
        self.write_profile("team/review")
        test_path = self.write_skill_test("team/review")
        references = find_skill_references(self.repo, "team/review")
        self.assertEqual(references.profile_ids, ("demo",))
        self.assertEqual(references.test_paths, (test_path,))
        with self.assertRaisesRegex(SkillReferencedError, "demo.*test.yaml"):
            plan_delete_skill(self.repo, "team/review", False)
        with self.assertRaisesRegex(SkillReferencedError, "test.yaml"):
            plan_delete_skill(self.repo, "team/review", True)
        test_path.unlink()
        with self.assertRaises(IntegrityError) as rejected:
            plan_delete_skill(self.repo, "team/review", True)
        self.assertIn("invalid-registry", {issue.code for issue in rejected.exception.report.issues})
        self.assertTrue((self.repo / "skills-src/l2/team/review").exists())
        self.assertEqual(yaml.safe_load((self.repo / "profiles/demo.yaml").read_text(encoding="utf-8"))["skills"], ["team/review"])

    def test_manualize_preserves_payload_and_removes_source_resolution_in_one_plan(self) -> None:
        from hwskill.skill_maintenance import plan_manualize_skill

        skill = self.make_upstream_skill()
        before = (skill / "SKILL.md").read_bytes()
        plan_manualize_skill(self.repo, "team/review").apply()
        self.assertEqual((skill / "SKILL.md").read_bytes(), before)
        self.assertEqual(yaml.safe_load((skill / "skill.yaml").read_text(encoding="utf-8"))["source"], {"kind": "manual"})
        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(source.skills, ())
        self.assertEqual([(item.path, item.reason) for item in source.upstream.ignore], [("review", "manualized")])

    def test_adopt_requires_replace_for_different_manual_content_and_stages_exact_upstream_payload(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_adopt_skill

        local = self.make_manual_skill("team/review", text=SKILL_TEXT + "\nLocal change.\n")
        source = UpstreamSource(
            "team",
            UpstreamConfig("https://team.example/team.git", "refs/heads/main", "skills", (IgnoredSkill("review", "manualized"),)),
            SourceDefaults("team", "l2", "MIT"), "0" * 40, (),
        )
        write_source_manifest(self.repo / "sources/team.yaml", source)
        before = (local / "SKILL.md").read_bytes()
        with self.assertRaisesRegex(SkillMaintenanceError, "replace=True"):
            plan_adopt_skill(self.repo, "team", "team/review", "review", False, self.git)
        self.assertEqual((local / "SKILL.md").read_bytes(), before)

        self.apply_verified(plan_adopt_skill(self.repo, "team", "team/review", "review", True, self.git))
        self.assertEqual((local / "SKILL.md").read_bytes(), (self.upstream / "skills/review/SKILL.md").read_bytes())
        governance = yaml.safe_load((local / "skill.yaml").read_text(encoding="utf-8"))
        self.assertEqual(governance["source"], {
            "kind": "upstream", "source_id": "team", "revision": "b" * 40, "upstream_path": "review",
        })
        adopted = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(adopted.upstream.ignore, ())
        self.assertEqual(adopted.skills[0].skill_id, "team/review")

    def test_adopt_uses_persisted_source_revision_without_changing_existing_skills(self) -> None:
        from hwskill.skill_maintenance import plan_adopt_skill

        old_revision = "a" * 40
        new_revision = "b" * 40
        existing = self.make_manual_skill("team/existing", text=SKILL_TEXT.replace("review", "existing"))
        governance = yaml.safe_load((existing / "skill.yaml").read_text(encoding="utf-8"))
        governance["name"] = "existing"
        governance["source"] = {
            "kind": "upstream", "source_id": "team", "revision": old_revision, "upstream_path": "existing",
        }
        (existing / "skill.yaml").write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")
        before = (existing / "SKILL.md").read_bytes()
        source = UpstreamSource(
            "team",
            UpstreamConfig("https://team.example/team.git", "refs/heads/main", "skills", (IgnoredSkill("review", "manualized"),)),
            SourceDefaults("team", "l2", "MIT"), old_revision,
            (ResolvedSourceSkill("existing", "team/existing", "l2", content_digest(existing)),),
        )
        write_source_manifest(self.repo / "sources/team.yaml", source)

        historical = self.root / "historical"
        old_review = historical / "skills/review"
        old_review.mkdir(parents=True)
        (old_review / "SKILL.md").write_text(SKILL_TEXT + "Old snapshot.\n", encoding="utf-8")
        old_existing = historical / "skills/existing"
        old_existing.mkdir()
        (old_existing / "SKILL.md").write_bytes(before)
        (self.upstream / "skills/review/SKILL.md").write_text(SKILL_TEXT + "New branch head.\n", encoding="utf-8")
        self._upstream_skill("existing", SKILL_TEXT.replace("review", "existing") + "New branch head.\n")
        git = HistoricalFakeGit(
            {old_revision: historical, "refs/heads/main": self.upstream},
            {old_revision: old_revision, "refs/heads/main": new_revision},
        )

        self.apply_verified(plan_adopt_skill(self.repo, "team", "team/review", "review", False, git))

        self.assertEqual(git.tracks, [old_revision])
        self.assertEqual((existing / "SKILL.md").read_bytes(), before)
        self.assertEqual(
            yaml.safe_load((existing / "skill.yaml").read_text(encoding="utf-8"))["source"]["revision"], old_revision,
        )
        adopted = self.repo / "skills-src/l2/team/review"
        self.assertEqual((adopted / "SKILL.md").read_text(encoding="utf-8"), SKILL_TEXT + "Old snapshot.\n")
        self.assertEqual(
            yaml.safe_load((adopted / "skill.yaml").read_text(encoding="utf-8"))["source"]["revision"], old_revision,
        )
        manifest = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(manifest.resolved_revision, old_revision)
        self.assertEqual([item.skill_id for item in manifest.skills], ["team/existing", "team/review"])

    def test_adopt_rejects_nested_upstream_symlink_before_digest(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_adopt_skill

        source = UpstreamSource(
            "team",
            UpstreamConfig("https://team.example/team.git", "refs/heads/main", "skills", (IgnoredSkill("review", "manualized"),)),
            SourceDefaults("team", "l2", "MIT"), "0" * 40, (),
        )
        write_source_manifest(self.repo / "sources/team.yaml", source)
        (self.upstream / "skills/review/outside-link").symlink_to(self.root / "outside-sentinel")

        with patch("hwskill.skill_maintenance.content_digest", side_effect=AssertionError("digest invoked")) as digest:
            with self.assertRaisesRegex(SkillMaintenanceError, "unsafe"):
                plan_adopt_skill(self.repo, "team", "team/review", "review", False, self.git)
        digest.assert_not_called()

    def test_adopt_rejects_nested_local_symlink_before_any_digest(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_adopt_skill

        local = self.make_manual_skill("team/review")
        source = UpstreamSource(
            "team",
            UpstreamConfig("https://team.example/team.git", "refs/heads/main", "skills", (IgnoredSkill("review", "manualized"),)),
            SourceDefaults("team", "l2", "MIT"), "0" * 40, (),
        )
        write_source_manifest(self.repo / "sources/team.yaml", source)
        (local / "outside-link").symlink_to(self.root / "outside-sentinel")

        with patch("hwskill.skill_maintenance.content_digest", side_effect=AssertionError("digest invoked")) as digest:
            with self.assertRaisesRegex(SkillMaintenanceError, "unsafe"):
                plan_adopt_skill(self.repo, "team", "team/review", "review", False, self.git)
        digest.assert_not_called()

    def test_find_skill_rejects_symlinked_governance_before_loader_reads_external_yaml(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_update_manual

        skill = self.make_manual_skill("team/review")
        external = self.root / "external-valid-governance.yaml"
        external.write_text(yaml.safe_dump({
            "id": "team/review", "layer": "l2", "source": {"kind": "manual"},
        }), encoding="utf-8")
        governance = skill / "skill.yaml"
        governance.unlink()
        governance.symlink_to(external)

        with patch("hwskill.skill_maintenance._load_governance", side_effect=AssertionError("loader invoked")) as loader:
            with self.assertRaisesRegex(SkillMaintenanceError, "unsafe skills-src"):
                plan_update_manual(self.repo, "team/review")
        loader.assert_not_called()

    def test_find_skill_rejects_symlinked_skills_root_before_inventory_load(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_create_manual

        external = self.root / "external-skills"
        external.mkdir()
        (self.repo / "skills-src").symlink_to(external, target_is_directory=True)

        with patch("hwskill.skill_maintenance._load_governance", side_effect=AssertionError("loader invoked")) as loader:
            with self.assertRaisesRegex(SkillMaintenanceError, "skills-src must be a real directory"):
                plan_create_manual(self.repo, "team/review", "l2", "review description", "MIT")
        loader.assert_not_called()

    def test_failed_planning_never_writes_real_tree(self) -> None:
        from hwskill.skill_maintenance import SkillMaintenanceError, plan_move_skill

        skill = self.make_manual_skill("team/review")
        before = (skill / "skill.yaml").read_bytes()
        with self.assertRaisesRegex(SkillMaintenanceError, "invalid Skill layer"):
            plan_move_skill(self.repo, "team/review", "../escape")
        self.assertEqual((skill / "skill.yaml").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
