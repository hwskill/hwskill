from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import unittest

from hwskill.directory.catalog import build_source_url
from hwskill.directory.translations import load_translation, translation_path
from hwskill.directory.yaml_io import load_yaml


ROOT = Path(__file__).parents[2]

NON_SERIES = {
    "community/performance-patterns",
    "data-engineering/spark-and-distributed-processing",
    "local/gitcode-discussion-fetch",
    "local/gitcode-pr-review-fetch",
}
SUPERPOWERS: set[str] = set()
MATTPOCOCK: set[str] = set()


class TranslationInventoryTests(unittest.TestCase):
    def test_translation_inventory_is_exact_and_preserves_document_contract(self) -> None:
        expected = NON_SERIES | SUPERPOWERS | MATTPOCOCK
        entries = {
            entry["id"]: entry
            for path in (ROOT / "entries").rglob("*.yaml")
            for entry in [load_yaml(path)]
        }
        actual_paths = sorted((ROOT / "translations").rglob("*.md"))
        actual = {
            path.relative_to(ROOT / "translations").with_suffix("").as_posix()
            for path in actual_paths
        }

        self.assertEqual(actual, expected)
        source_urls: list[str] = []
        for skill_id in sorted(expected):
            with self.subTest(skill_id=skill_id):
                document = load_translation(translation_path(ROOT, skill_id))
                self.assertEqual(document["skill_id"], skill_id)
                self.assertEqual(document["schema_version"], 1)
                self.assertLessEqual(date.fromisoformat(document["translated_at"]), date.today())
                self.assertTrue(document["body"].strip())
                fences = re.findall(r"(?m)^\s*```", document["body"])
                self.assertEqual(len(fences) % 2, 0)
                source_urls.append(build_source_url(entries[skill_id]))

        self.assertEqual(len(source_urls), len(expected))
        self.assertEqual(len(set(source_urls)), len(expected))
        self.assertTrue(all(url.endswith("/SKILL.md") for url in source_urls))


if __name__ == "__main__":
    unittest.main()
