from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

import yaml

from hwskill.digest import content_digest
from hwskill.git_source import GitSourceError, ResolvedTrack
from hwskill.source_manifest import (
    IgnoredSkill,
    ResolvedSourceSkill,
    SourceDefaults,
    UpstreamConfig,
    UpstreamSource,
    load_source_manifest,
    write_source_manifest,
)


class FakeGit:
    def __init__(self, upstream: Path, revision: str = "b" * 40) -> None:
        self.upstream = upstream
        self.revision = revision
        self.calls = 0
        self.fail_for: set[str] = set()

    def materialize(self, repository: str, track: str, destination: Path) -> ResolvedTrack:
        self.calls += 1
        if repository in self.fail_for:
            raise GitSourceError(f"cannot materialize {repository}")
        for child in self.upstream.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        return ResolvedTrack(track=track, kind="tag" if track.startswith("refs/tags/") else "branch", commit=self.revision)


class SourceMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        self._upstream_skill("changed-skill", "changed-skill", "new body")
        self._upstream_skill("same-skill", "same-skill", "same body")
        self._upstream_skill("new-skill", "new-skill", "new skill")
        self._upstream_skill("ignored-skill", "ignored-skill", "ignored")
        self.git = FakeGit(self.upstream)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _upstream_skill(self, path: str, name: str, body: str) -> Path:
        skill = self.upstream / "skills" / path
        skill.mkdir(parents=True, exist_ok=True)
        skill.joinpath("SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} description\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return skill

    def _skill(self, skill_id: str, layer: str, *, body: str, source: dict[str, str]) -> Path:
        namespace, name = skill_id.split("/", 1)
        skill = self.repo / "skills-src" / layer / namespace / name
        skill.mkdir(parents=True, exist_ok=True)
        skill.joinpath("SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} description\n---\n\n{body}\n",
            encoding="utf-8",
        )
        skill.joinpath("skill.yaml").write_text(yaml.safe_dump({
            "schema_version": 1,
            "id": skill_id,
            "name": name,
            "description": f"{name} description",
            "layer": layer,
            "status": "experimental",
            "source": source,
            "license": "MIT",
            "content_digest": content_digest(skill),
        }, sort_keys=False), encoding="utf-8")
        return skill

    def _source(self, *, track: str = "refs/heads/main", revision: str = "a" * 40) -> UpstreamSource:
        old_changed = self._skill(
            "team/changed-skill", "l1", body="old body",
            source={"source_id": "team", "revision": revision, "upstream_path": "changed-skill"},
        )
        same = self._skill(
            "team/same-skill", "l3", body="same body",
            source={"source_id": "team", "revision": revision, "upstream_path": "same-skill"},
        )
        deleted = self._skill(
            "team/deleted-skill", "l2", body="deleted body",
            source={"source_id": "team", "revision": revision, "upstream_path": "deleted-skill"},
        )
        source = UpstreamSource(
            source_id="team",
            upstream=UpstreamConfig("https://team.example/team.git", track, "skills", (IgnoredSkill("ignored-skill", "not selected"),)),
            defaults=SourceDefaults("team", "l2", "MIT"),
            resolved_revision=revision,
            skills=(
                ResolvedSourceSkill("changed-skill", "team/changed-skill", "l1", content_digest(old_changed)),
                ResolvedSourceSkill("same-skill", "team/same-skill", "l3", content_digest(same)),
                ResolvedSourceSkill("deleted-skill", "team/deleted-skill", "l2", content_digest(deleted)),
            ),
        )
        path = self.repo / "sources/team.yaml"
        write_source_manifest(path, source)
        return source

    def test_inspection_partitions_paths_by_content_not_revision(self) -> None:
        from hwskill.source_maintenance import inspect_source

        source = self._source()
        inspection = inspect_source(self.repo, source, self.git)

        self.assertEqual([item.path for item in inspection.added], ["new-skill"])
        self.assertEqual([item.path for item in inspection.updated], ["changed-skill"])
        self.assertEqual(inspection.removed, ("deleted-skill",))
        self.assertEqual(inspection.ignored, ("ignored-skill",))
        self.assertEqual(inspection.new_revision, "b" * 40)
        self.assertEqual(self.git.calls, 1)

    def test_fixed_tag_move_fails_during_inspection(self) -> None:
        from hwskill.source_maintenance import inspect_source

        source = self._source(track="refs/tags/v1")
        with self.assertRaisesRegex(GitSourceError, "tag moved"):
            inspect_source(self.repo, source, self.git)

    def test_update_stages_payload_provenance_catalog_and_removed_ignore(self) -> None:
        from hwskill.source_maintenance import UpdatePolicies, plan_update_sources

        self._source()
        plan = plan_update_sources(
            self.repo, ("team",), UpdatePolicies("include", "remove"), self.git,
        )
        plan.apply()

        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(source.resolved_revision, "b" * 40)
        self.assertEqual([skill.path for skill in source.skills], ["changed-skill", "new-skill", "same-skill"])
        self.assertEqual(source.skills[0].layer, "l1")
        self.assertEqual(source.skills[1].layer, "l2")
        self.assertEqual([item.path for item in source.upstream.ignore], ["deleted-skill", "ignored-skill"])
        self.assertFalse((self.repo / "skills-src/l2/team/deleted-skill").exists())
        changed_governance = yaml.safe_load((self.repo / "skills-src/l1/team/changed-skill/skill.yaml").read_text(encoding="utf-8"))
        self.assertEqual(changed_governance["source"]["revision"], "b" * 40)
        catalog = yaml.safe_load((self.repo / "registry/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in catalog["skills"]], ["team/changed-skill", "team/new-skill", "team/same-skill"])

    def test_add_materializes_default_layer_and_ignores_unselected_skill(self) -> None:
        from hwskill.source_maintenance import SourceAddRequest, SourceSelection, plan_add_source

        request = SourceAddRequest(
            "team", "https://team.example/team.git", "refs/heads/main", "skills", SourceDefaults("team", "l2", "MIT"),
        )
        plan = plan_add_source(
            self.repo, request, SourceSelection(("new-skill",), ("ignored-skill",)), self.git,
        )
        plan.apply()

        source = load_source_manifest(self.repo / "sources/team.yaml")
        self.assertEqual(source.skills[0].skill_id, "team/new-skill")
        self.assertEqual(source.skills[0].layer, "l2")
        self.assertTrue((self.repo / "skills-src/l2/team/new-skill/SKILL.md").is_file())
        self.assertEqual([item.path for item in source.upstream.ignore], ["changed-skill", "ignored-skill", "same-skill"])

    def test_add_accepts_a_fixed_tag_without_a_prior_resolved_revision(self) -> None:
        from hwskill.source_maintenance import SourceAddRequest, SourceSelection, plan_add_source

        request = SourceAddRequest(
            "team", "https://team.example/team.git", "refs/tags/v1", "skills", SourceDefaults("team", "l2", "MIT"),
        )
        plan = plan_add_source(self.repo, request, SourceSelection(("new-skill",), ()), self.git)
        plan.apply()

        self.assertEqual(load_source_manifest(self.repo / "sources/team.yaml").resolved_revision, "b" * 40)

    def test_ignore_allows_future_rejects_resolved_and_unignore_reports_manual_adopt(self) -> None:
        from hwskill.source_maintenance import SourceMaintenanceError, plan_ignore_change

        self._source()
        future = plan_ignore_change(self.repo, "team", "future-skill", "add")
        future.apply()
        with self.assertRaisesRegex(SourceMaintenanceError, "resolved"):
            plan_ignore_change(self.repo, "team", "same-skill", "add")

        self._skill("team/ignored-skill", "l2", body="manual", source={"kind": "manual"})
        with self.assertRaisesRegex(SourceMaintenanceError, r"hwskill source adopt team team/ignored-skill --path ignored-skill"):
            plan_ignore_change(self.repo, "team", "ignored-skill", "remove", self.git)

    def test_delete_requires_profile_choice_and_manualizes_without_source_ignore(self) -> None:
        from hwskill.source_maintenance import SourceMaintenanceError, plan_delete_source

        self._source()
        (self.repo / "profiles").mkdir()
        (self.repo / "profiles/demo.yaml").write_text(yaml.safe_dump({
            "schema_version": 1, "id": "demo", "description": "demo", "skills": ["team/same-skill"],
        }), encoding="utf-8")
        with self.assertRaisesRegex(SourceMaintenanceError, "Profile"):
            plan_delete_source(self.repo, "team", "delete", False)

        plan = plan_delete_source(self.repo, "team", "manualize", False)
        plan.apply()
        self.assertFalse((self.repo / "sources/team.yaml").exists())
        self.assertEqual(
            yaml.safe_load((self.repo / "skills-src/l3/team/same-skill/skill.yaml").read_text(encoding="utf-8"))["source"],
            {"kind": "manual"},
        )
        self.assertEqual(yaml.safe_load((self.repo / "profiles/demo.yaml").read_text(encoding="utf-8"))["skills"], ["team/same-skill"])

    def test_all_update_resolves_every_source_before_any_candidate_is_applied(self) -> None:
        from hwskill.source_maintenance import UpdatePolicies, plan_update_sources

        self._source()
        second = UpstreamSource(
            "other", UpstreamConfig("https://team.example/other.git", "refs/heads/main", "skills", ()),
            SourceDefaults("other", "l1", "MIT"), "a" * 40, (),
        )
        write_source_manifest(self.repo / "sources/other.yaml", second)
        self.git.fail_for.add("https://team.example/other.git")
        original = (self.repo / "sources/team.yaml").read_bytes()

        with self.assertRaisesRegex(GitSourceError, "cannot materialize"):
            plan_update_sources(self.repo, None, UpdatePolicies("include", "remove"), self.git)

        self.assertEqual((self.repo / "sources/team.yaml").read_bytes(), original)
        self.assertEqual(self.git.calls, 1)


if __name__ == "__main__":
    unittest.main()
