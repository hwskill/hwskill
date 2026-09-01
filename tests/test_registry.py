import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

import yaml

from hwskill.registry import (
    RegistryValidationError,
    build_catalog,
    validate_registry,
    write_catalog,
)


SOURCE_SKILL = Path(__file__).parents[1] / "skills-src/l1/local/chinese-thinking"


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name)
        target = self.repo / "skills-src/l1/local/chinese-thinking"
        target.parent.mkdir(parents=True)
        shutil.copytree(SOURCE_SKILL, target)
        governance_path = target / "skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        source = governance["source"]
        governance["source"] = {
            "kind": "upstream",
            "source_id": source["source_id"],
            "revision": source["revision"],
            "upstream_path": source["upstream_path"],
        }
        governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_validation_rejects_digest_drift(self):
        skill = self.repo / "skills-src/l1/local/chinese-thinking/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RegistryValidationError, "content_digest"):
            validate_registry(self.repo)

    def test_catalog_is_sorted_and_reproducible(self):
        first = json.dumps(build_catalog(self.repo), ensure_ascii=False, sort_keys=True)
        second = json.dumps(build_catalog(self.repo), ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)
        ids = [item["id"] for item in json.loads(first)["skills"]]
        self.assertEqual(ids, sorted(ids))

    def test_validation_marks_governed_skills_as_upstream(self):
        records = validate_registry(self.repo)

        self.assertEqual(records[0].source_kind, "upstream")
        self.assertEqual(records[0].source_id, "local-agents-skills")

    def test_check_detects_stale_catalog(self):
        (self.repo / "registry").mkdir()
        (self.repo / "registry/catalog.json").write_text("{}\n", encoding="utf-8")
        self.assertFalse(write_catalog(self.repo, check=True))

    def test_validation_rejects_unknown_profile_skill(self):
        profiles = self.repo / "profiles"
        profiles.mkdir()
        (profiles / "broken.yaml").write_text(yaml.safe_dump({
            "schema_version": 1,
            "id": "broken",
            "description": "invalid reference",
            "skills": ["missing/skill"],
        }), encoding="utf-8")
        with self.assertRaisesRegex(RegistryValidationError, "unknown skill in profile"):
            validate_registry(self.repo)

    def test_validation_accepts_strict_manual_provenance(self):
        governance_path = self.repo / "skills-src/l1/local/chinese-thinking/skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        governance["source"] = {"kind": "manual"}
        governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")

        record = validate_registry(self.repo)[0]
        catalog = build_catalog(self.repo)

        self.assertEqual((record.source_kind, record.source_id, record.revision), ("manual", None, "manual"))
        self.assertEqual(catalog["skills"][0]["source_kind"], "manual")
        self.assertIsNone(catalog["skills"][0]["source_id"])
        self.assertEqual(catalog["skills"][0]["revision"], "manual")

    def test_validation_rejects_unknown_source_kind(self):
        governance_path = self.repo / "skills-src/l1/local/chinese-thinking/skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        governance["source"] = {"kind": "other", "source_id": "source", "revision": "revision", "upstream_path": "path"}
        governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(RegistryValidationError, "source kind"):
            validate_registry(self.repo)


if __name__ == "__main__":
    unittest.main()
