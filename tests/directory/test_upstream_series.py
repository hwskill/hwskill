from __future__ import annotations

from pathlib import Path
import unittest

from hwskill.directory.yaml_io import load_yaml


ROOT = Path(__file__).parents[2]
SUPERPOWERS_REF = "5bf4e78011075bcfc0dc295f0724994cd123ee71"
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


class UpstreamSeriesTests(unittest.TestCase):
    def test_superpowers_inventory(self) -> None:
        paths = sorted((ROOT / "entries").glob("l*/superpowers/*.yaml"))
        entries = {path.stem: (path, load_yaml(path)) for path in paths}
        self.assertEqual({name: entry["layer"] for name, (_, entry) in entries.items()}, SUPERPOWERS)

        for name, layer in SUPERPOWERS.items():
            with self.subTest(name=name):
                path, entry = entries[name]
                self.assertEqual(path, ROOT / "entries" / layer / "superpowers" / f"{name}.yaml")
                self.assertEqual(entry["id"], f"superpowers/{name}")
                locator = entry["source"]["locator"]
                self.assertEqual(locator["repository"], "https://github.com/obra/superpowers.git")
                self.assertEqual(locator["path"], f"skills/{name}")
                self.assertEqual(locator["requested_ref"], SUPERPOWERS_REF)
                self.assertEqual(entry["install"]["method"], "upstream")
                self.assertEqual(entry["install"]["instructions_url"], f"https://github.com/obra/superpowers/blob/{SUPERPOWERS_REF}/README.md")
                self.assertEqual(entry["license"]["identifier"], "MIT")
                self.assertEqual(entry["license"]["url"], f"https://github.com/obra/superpowers/blob/{SUPERPOWERS_REF}/LICENSE")
                self.assertEqual(entry["owner"], "Jesse Vincent")
                self.assertEqual(entry["lifecycle"], "active")
                self.assertTrue(entry["purposes"])
                self.assertTrue(entry["examples"])
                self.assertTrue(entry["limitations"])
                self.assertTrue(any("安装和行为未运行" in item for item in entry["limitations"]))


if __name__ == "__main__":
    unittest.main()
