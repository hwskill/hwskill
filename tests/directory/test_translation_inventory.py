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
SUPERPOWERS = {
    f"superpowers/{name}"
    for name in {
        "brainstorming",
        "diagnosing-superpowers",
        "dispatching-parallel-agents",
        "executing-plans",
        "finishing-a-development-branch",
        "receiving-code-review",
        "requesting-code-review",
        "subagent-driven-development",
        "systematic-debugging",
        "test-driven-development",
        "using-git-worktrees",
        "using-superpowers",
        "verification-before-completion",
        "writing-plans",
        "writing-skills",
    }
}
MATTPOCOCK = {
    f"mattpocock/{name}"
    for name in {
        "ask-matt",
        "code-review",
        "codebase-design",
        "diagnosing-bugs",
        "domain-modeling",
        "grill-me",
        "grill-with-docs",
        "grilling",
        "handoff",
        "implement",
        "improve-codebase-architecture",
        "prototype",
        "research",
        "resolving-merge-conflicts",
        "setup-matt-pocock-skills",
        "tdd",
        "teach",
        "to-questionnaire",
        "to-spec",
        "to-tickets",
        "triage",
        "wait-what",
        "wayfinder",
        "wizard",
        "writing-for-agents",
    }
}
SERIES_STRUCTURE = {
    "superpowers/brainstorming": (10, 2),
    "superpowers/diagnosing-superpowers": (6, 0),
    "superpowers/dispatching-parallel-agents": (14, 8),
    "superpowers/executing-plans": (13, 4),
    "superpowers/finishing-a-development-branch": (21, 26),
    "superpowers/receiving-code-review": (16, 24),
    "superpowers/requesting-code-review": (6, 4),
    "superpowers/subagent-driven-development": (15, 6),
    "superpowers/systematic-debugging": (15, 6),
    "superpowers/test-driven-development": (19, 26),
    "superpowers/using-git-worktrees": (21, 16),
    "superpowers/using-superpowers": (5, 0),
    "superpowers/verification-before-completion": (9, 14),
    "superpowers/writing-plans": (15, 10),
    "superpowers/writing-skills": (76, 42),
    "mattpocock/ask-matt": (9, 0),
    "mattpocock/code-review": (7, 0),
    "mattpocock/codebase-design": (8, 8),
    "mattpocock/diagnosing-bugs": (14, 0),
    "mattpocock/domain-modeling": (9, 4),
    "mattpocock/grill-with-docs": (0, 0),
    "mattpocock/implement": (0, 0),
    "mattpocock/improve-codebase-architecture": (5, 0),
    "mattpocock/prototype": (3, 0),
    "mattpocock/research": (0, 0),
    "mattpocock/resolving-merge-conflicts": (0, 0),
    "mattpocock/setup-matt-pocock-skills": (11, 2),
    "mattpocock/tdd": (5, 0),
    "mattpocock/to-spec": (8, 0),
    "mattpocock/to-tickets": (12, 0),
    "mattpocock/triage": (10, 4),
    "mattpocock/wayfinder": (17, 4),
    "mattpocock/wizard": (6, 0),
    "mattpocock/grill-me": (0, 0),
    "mattpocock/grilling": (0, 2),
    "mattpocock/handoff": (0, 0),
    "mattpocock/teach": (12, 0),
    "mattpocock/to-questionnaire": (7, 0),
    "mattpocock/wait-what": (0, 0),
    "mattpocock/writing-for-agents": (7, 0),
}


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
                if skill_id in SERIES_STRUCTURE:
                    expected_headings, expected_fences = SERIES_STRUCTURE[skill_id]
                    self.assertEqual(len(re.findall(r"(?m)^#{1,6} ", document["body"])), expected_headings)
                    self.assertEqual(len(fences), expected_fences)
                source_urls.append(build_source_url(entries[skill_id]))

        self.assertEqual(len(source_urls), len(expected))
        self.assertEqual(len(set(source_urls)), len(expected))
        self.assertTrue(all(url.endswith("/SKILL.md") for url in source_urls))


if __name__ == "__main__":
    unittest.main()
