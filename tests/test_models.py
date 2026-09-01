from pathlib import Path
import unittest

from hwskill.models import SkillRecord


class SkillRecordProvenanceTest(unittest.TestCase):
    def record(self, **changes) -> SkillRecord:
        values = {
            "skill_id": "example/skill",
            "name": "skill",
            "description": "fixture",
            "layer": "l1",
            "source_kind": "manual",
            "source_id": None,
            "revision": "manual",
            "license": "MIT",
            "content_digest": "sha256:" + "a" * 64,
            "path": Path("/tmp/skill"),
        }
        values.update(changes)
        return SkillRecord(**values)

    def test_manual_provenance_requires_no_source_id_and_manual_revision(self):
        self.assertEqual(self.record().revision, "manual")
        with self.assertRaisesRegex(ValueError, "manual.*source_id"):
            self.record(source_id="legacy-source")
        with self.assertRaisesRegex(ValueError, "manual.*revision"):
            self.record(revision="working-tree")

    def test_upstream_provenance_requires_a_source_id_and_non_manual_revision(self):
        self.assertEqual(
            self.record(source_kind="upstream", source_id="upstream", revision="a" * 40).source_id,
            "upstream",
        )
        with self.assertRaisesRegex(ValueError, "upstream.*source_id"):
            self.record(source_kind="upstream", source_id=None, revision="a" * 40)
        with self.assertRaisesRegex(ValueError, "upstream.*revision"):
            self.record(source_kind="upstream", source_id="upstream", revision="manual")

    def test_rejects_unknown_source_kind_at_runtime(self):
        with self.assertRaisesRegex(ValueError, "source_kind"):
            self.record(source_kind="local")


if __name__ == "__main__":
    unittest.main()
