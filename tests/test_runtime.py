from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from hwskill.digest import content_digest
from hwskill.frontmatter import parse_skill_markdown
from hwskill.loader import LoadError, load_skill
from hwskill.profiles import ProfileError, bind_profile, resolve_profiles
from hwskill.projects import find_project
from hwskill.search import search_skills


REGISTRY = Path(__file__).parents[1]


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "demo"
        (self.project / ".hwskills").mkdir(parents=True)
        (self.project / ".hwskills/profile.yaml").write_text(
            yaml.safe_dump({"schema_version": 1, "profiles": ["codex-demo"]}), encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_find_project_prefers_git_root(self):
        (self.project / ".git").mkdir()
        nested = self.project / "a/b"
        nested.mkdir(parents=True)
        self.assertEqual(find_project(nested), self.project)

    def test_resolution_search_and_load_stay_profile_scoped(self):
        catalog = resolve_profiles(self.project, REGISTRY)
        self.assertEqual(set(catalog.skill_ids), {
            "superpowers/systematic-debugging",
            "superpowers/test-driven-development",
            "local/gitcode-pr-review-fetch",
        })
        results = search_skills(catalog, "debug failing test")
        self.assertEqual(results[0].skill_id, "superpowers/systematic-debugging")
        loaded = load_skill(catalog, "superpowers/systematic-debugging")
        metadata, _ = parse_skill_markdown(loaded.content)
        runtime = metadata["x-hwskill-runtime"]
        self.assertEqual(set(runtime), {
            "id", "revision", "content_digest", "skill_dir", "skill_file", "registry_root",
        })
        self.assertEqual(runtime["content_digest"], loaded.content_digest)
        self.assertEqual(content_digest(Path(runtime["skill_dir"])), loaded.content_digest)
        self.assertEqual(Path(runtime["skill_file"]), Path(runtime["skill_dir"]) / "SKILL.md")
        self.assertEqual(Path(runtime["registry_root"]), REGISTRY)
        raw = load_skill(catalog, "superpowers/systematic-debugging", raw=True)
        self.assertEqual(raw.content, Path(raw.skill_file).read_text(encoding="utf-8"))
        with self.assertRaisesRegex(LoadError, "not in the Effective Skill Catalog"):
            load_skill(catalog, "superpowers/brainstorming")

    def test_resolution_rejects_lock_drift(self):
        bind_profile(self.project, REGISTRY, "codex-demo")
        lock = self.project / ".hwskills/lock.yaml"
        data = yaml.safe_load(lock.read_text(encoding="utf-8"))
        data["catalog_digest"] = "sha256:stale"
        lock.write_text(yaml.safe_dump(data), encoding="utf-8")
        with self.assertRaisesRegex(ProfileError, "lock does not match"):
            resolve_profiles(self.project, REGISTRY)


if __name__ == "__main__":
    unittest.main()
