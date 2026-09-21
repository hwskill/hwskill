from __future__ import annotations

from pathlib import Path
import unittest

from hwskill.directory.yaml_io import load_yaml


ROOT = Path(__file__).parents[2]
SUPERPOWERS = {
    "brainstorming": "l1",
    "diagnosing-superpowers": "l1",
    "dispatching-parallel-agents": "l1",
    "executing-plans": "l1",
    "subagent-driven-development": "l2",
    "using-superpowers": "l1",
    "writing-plans": "l1",
    "writing-skills": "l1",
    "finishing-a-development-branch": "l2",
    "receiving-code-review": "l2",
    "requesting-code-review": "l2",
    "systematic-debugging": "l2",
    "test-driven-development": "l2",
    "using-git-worktrees": "l2",
    "verification-before-completion": "l2",
}


MATT_ENGINEERING = {
    "ask-matt", "code-review", "codebase-design", "diagnosing-bugs",
    "domain-modeling", "grill-with-docs", "implement",
    "improve-codebase-architecture", "prototype", "research",
    "resolving-merge-conflicts", "setup-matt-pocock-skills", "tdd",
    "to-spec", "to-tickets", "triage", "wayfinder", "wizard",
}
MATT_PRODUCTIVITY = {
    "grill-me", "grilling", "handoff", "teach", "to-questionnaire",
    "wait-what", "writing-for-agents",
}


class UpstreamSeriesTests(unittest.TestCase):
    def test_superpowers_inventory(self) -> None:
        paths = sorted((ROOT / "entries").glob("l*/superpowers/*.yaml"))
        entries = {path.stem: (path, load_yaml(path)) for path in paths}
        self.assertEqual({name: entry["layer"] for name, (_, entry) in entries.items()}, SUPERPOWERS)

        for name, layer in SUPERPOWERS.items():
            with self.subTest(name=name):
                path, entry = entries[name]
                self.assertEqual(path, ROOT / "entries" / layer / "superpowers" / f"{name}.yaml")
                self.assertEqual(entry["schema_version"], 2)
                self.assertEqual(entry["id"], f"superpowers/{name}")
                locator = entry["source"]["locator"]
                self.assertEqual(locator["repository"], "https://github.com/obra/superpowers.git")
                self.assertEqual(locator["path"], f"skills/{name}")
                self.assertNotIn("requested_ref", locator)
                self.assertNotIn("ref", locator)
                self.assertEqual(entry["install"]["method"], "upstream")
                self.assertEqual(entry["install"]["instructions_url"], "https://github.com/obra/superpowers/blob/HEAD/README.md")
                self.assertEqual(entry["license"]["identifier"], "MIT")
                self.assertEqual(entry["license"]["url"], "https://github.com/obra/superpowers/blob/HEAD/LICENSE")
                self.assertEqual(entry["owner"], "Jesse Vincent")
                self.assertEqual(entry["lifecycle"], "active")
                self.assertTrue(entry["purposes"])
                self.assertTrue(entry["examples"])
                self.assertTrue(entry["limitations"])
                self.assertFalse(any("未运行" in item or "未验证" in item for item in entry["limitations"]))


    def test_executing_plans_includes_runtime_dependencies(self) -> None:
        entry = load_yaml(ROOT / "entries/l1/superpowers/executing-plans.yaml")
        self.assertEqual(set(entry["install"]["included_skills"]), {
            "writing-plans",
            "test-driven-development",
            "systematic-debugging",
            "verification-before-completion",
            "requesting-code-review",
            "finishing-a-development-branch",
        })


    def test_mattpocock_inventory(self) -> None:
        paths = sorted((ROOT / "entries").glob("l*/mattpocock/*.yaml"))
        entries = {path.stem: (path, load_yaml(path)) for path in paths}
        self.assertEqual(set(entries), MATT_ENGINEERING | MATT_PRODUCTIVITY)

        for name in sorted(entries):
            with self.subTest(name=name):
                category = "engineering" if name in MATT_ENGINEERING else "productivity"
                layer = "l2" if category == "engineering" else "l1"
                path, entry = entries[name]
                self.assertEqual(path, ROOT / "entries" / layer / "mattpocock" / f"{name}.yaml")
                self.assertEqual(entry["schema_version"], 2)
                self.assertEqual(entry["id"], f"mattpocock/{name}")
                locator = entry["source"]["locator"]
                self.assertEqual(locator["repository"], "https://github.com/mattpocock/skills.git")
                self.assertEqual(locator["path"], f"skills/{category}/{name}")
                self.assertNotIn("requested_ref", locator)
                self.assertNotIn("ref", locator)
                self.assertFalse(locator["path"].startswith(("skills/in-progress/", "skills/misc/", "skills/deprecated/")))
                self.assertEqual(entry["install"]["method"], "upstream")
                self.assertEqual(entry["install"]["instructions_url"], "https://github.com/mattpocock/skills/blob/HEAD/README.md")
                self.assertEqual(entry["license"]["identifier"], "MIT")
                self.assertEqual(entry["license"]["url"], "https://github.com/mattpocock/skills/blob/HEAD/LICENSE")
                self.assertEqual(entry["owner"], "Matt Pocock")
                self.assertEqual(entry["lifecycle"], "active")
                self.assertTrue(entry["purposes"] and entry["examples"] and entry["limitations"])
                self.assertFalse(any("未运行" in item or "未验证" in item for item in entry["limitations"]))


    def test_series_recommendations_cover_exact_inventory(self) -> None:
        from hwskill.directory.recommendations import load_recommendation

        superpowers = load_recommendation(ROOT / "recommendations/superpowers-engineering-workflow.md")
        matt = load_recommendation(ROOT / "recommendations/matt-pocock-composable-engineering.md")
        self.assertEqual({item["id"] for item in superpowers["skills"]}, {f"superpowers/{name}" for name in SUPERPOWERS})
        self.assertEqual({item["id"] for item in matt["skills"]}, {f"mattpocock/{name}" for name in MATT_ENGINEERING | MATT_PRODUCTIVITY})
        self.assertEqual(superpowers["status"], "ready")
        self.assertEqual(matt["status"], "ready")
        self.assertEqual(superpowers["body_format"], "markdown")
        self.assertEqual(matt["body_format"], "markdown")
        self.assertTrue(superpowers["summary"] and matt["summary"])
        self.assertEqual(superpowers["evidence"][0]["url"], "https://github.com/obra/superpowers")
        self.assertEqual(matt["evidence"][0]["url"], "https://github.com/mattpocock/skills")
        self.assertTrue(superpowers["evidence"][0]["observed_at"].startswith("2026-09-20"))
        self.assertTrue(matt["evidence"][0]["observed_at"].startswith("2026-09-20"))
        for document in (superpowers, matt):
            for heading in ("## 定位", "## 工作流", "## 适用场景", "## 成本与限制", "## 与另一个系列的比较", "## 组合边界", "## 验证状态"):
                self.assertIn(heading, document["body"])


if __name__ == "__main__":
    unittest.main()
