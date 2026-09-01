from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hwskill.digest import content_digest
from hwskill.models import SkillRecord
from hwskill import registry as registry_module
from hwskill.skill_export import (
    ExportError,
    export_skills,
    resolve_skill_selectors,
    skills_for_profiles,
)


ROOT = Path(__file__).parents[1]


class SkillExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.destination = self.base / "skills"

    def tearDown(self):
        self.temp.cleanup()

    def record(self, skill_id: str) -> SkillRecord:
        path = self.base / skill_id.replace("/", "-")
        path.mkdir()
        (path / "SKILL.md").write_text(skill_id, encoding="utf-8")
        return SkillRecord(
            skill_id=skill_id,
            name=skill_id.rsplit("/", 1)[-1],
            description="fixture",
            layer="l1",
            source_kind="upstream",
            source_id="fixture",
            revision="1",
            license="MIT",
            content_digest=content_digest(path),
            path=path,
        )

    def select_two(self) -> tuple[SkillRecord, SkillRecord]:
        by_id = {item.skill_id: item for item in registry_module.validate_registry(ROOT)}
        return (
            by_id["local/chinese-thinking"],
            by_id["superpowers/systematic-debugging"],
        )

    def test_selectors_accept_ids_unique_names_mixed_and_multiple(self):
        records = registry_module.validate_registry(ROOT)

        selected = resolve_skill_selectors(
            "local/chinese-thinking,systematic-debugging,chinese-thinking",
            records,
        )

        self.assertEqual(
            [item.skill_id for item in selected],
            ["local/chinese-thinking", "superpowers/systematic-debugging"],
        )

    def test_ambiguous_name_lists_candidate_ids(self):
        records = (self.record("one/shared"), self.record("two/shared"))

        with self.assertRaisesRegex(ExportError, "one/shared.*two/shared"):
            resolve_skill_selectors("shared", records)

    def test_profile_selection_unions_multiple_profiles_in_input_order(self):
        selected = skills_for_profiles(
            "codex-demo,personal-baseline", ROOT, registry_module.validate_registry(ROOT)
        )

        self.assertEqual(
            [item.skill_id for item in selected],
            [
                "superpowers/systematic-debugging",
                "superpowers/test-driven-development",
                "local/gitcode-pr-review-fetch",
                "local/chinese-thinking",
                "local/gitcode-discussion-fetch",
            ],
        )

    def test_export_copies_complete_payload_without_root_governance(self):
        source, _ = self.select_two()

        results = export_skills((source,), self.destination)

        target = self.destination / source.name
        self.assertEqual(results[0].status, "CREATED")
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertFalse((target / "skill.yaml").exists())
        for child in source.path.iterdir():
            if child.name != "skill.yaml":
                self.assertEqual((target / child.name).is_dir(), child.is_dir())
        self.assertEqual(content_digest(target), source.content_digest)

    def test_identical_destination_is_unchanged(self):
        source, _ = self.select_two()
        export_skills((source,), self.destination)

        results = export_skills((source,), self.destination)

        self.assertEqual(results[0].status, "UNCHANGED")

    def test_conflict_preflight_writes_nothing(self):
        first, second = self.select_two()
        changed = self.destination / second.name
        changed.mkdir(parents=True)
        (changed / "SKILL.md").write_text("changed", encoding="utf-8")

        with self.assertRaisesRegex(ExportError, "refusing to overwrite"):
            export_skills((first, second), self.destination)

        self.assertFalse((self.destination / first.name).exists())

    def test_duplicate_target_names_and_source_digest_drift_are_rejected(self):
        first = self.record("one/shared")
        second = self.record("two/shared")
        with self.assertRaisesRegex(ExportError, "target name collision"):
            export_skills((first, second), self.destination)

        drifted = SkillRecord(
            **{
                **first.__dict__,
                "content_digest": "sha256:stale",
            }
        )
        with self.assertRaisesRegex(ExportError, "source digest mismatch"):
            export_skills((drifted,), self.destination)
        self.assertFalse(self.destination.exists())

    def test_existing_destination_with_extra_governance_is_a_conflict(self):
        source, _ = self.select_two()
        export_skills((source,), self.destination)
        target = self.destination / source.name
        (target / "skill.yaml").write_text("extra", encoding="utf-8")

        with self.assertRaisesRegex(ExportError, "refusing to overwrite"):
            export_skills((source,), self.destination)


if __name__ == "__main__":
    unittest.main()
