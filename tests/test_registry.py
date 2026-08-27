import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

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

    def test_check_detects_stale_catalog(self):
        (self.repo / "registry").mkdir()
        (self.repo / "registry/catalog.json").write_text("{}\n", encoding="utf-8")
        self.assertFalse(write_catalog(self.repo, check=True))


if __name__ == "__main__":
    unittest.main()
