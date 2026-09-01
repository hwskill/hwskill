import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

import yaml

from hwskill import registry as registry_module
from hwskill.cli import main
from hwskill.digest import content_digest
from hwskill.source_manifest import load_source_manifest


RegistryValidationError = registry_module.RegistryValidationError
build_catalog = registry_module.build_catalog
validate_registry = registry_module.validate_registry
write_catalog = registry_module.write_catalog


SOURCE_SKILL = Path(__file__).parents[2] / "skills-src/l1/local/chinese-thinking"
ROOT = Path(__file__).parents[2]
SUPERPOWERS_REVISION = "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
MANUAL_SKILL_IDS = (
    "local/chinese-thinking",
    "local/gitcode-discussion-fetch",
    "local/gitcode-pr-review-fetch",
)
SUPERPOWERS_PATHS = (
    "brainstorming",
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
)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name)
        target = self.repo / "skills-src/l1/local/chinese-thinking"
        target.parent.mkdir(parents=True)
        shutil.copytree(SOURCE_SKILL, target)
        governance_path = target / "skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        governance["source"] = {
            "kind": "upstream",
            "source_id": "fixture-source",
            "revision": "a" * 40,
            "upstream_path": "chinese-thinking",
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
        self.assertEqual(records[0].source_id, "fixture-source")

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

    def test_validation_rejects_upstream_provenance_without_full_revision(self):
        """A local-looking revision cannot make an upstream snapshot reproducible."""
        governance_path = self.repo / "skills-src/l1/local/chinese-thinking/skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        governance["source"]["revision"] = "a" * 12
        governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(RegistryValidationError, "full 40-character"):
            validate_registry(self.repo)

    def test_validation_rejects_upstream_provenance_without_a_safe_path(self):
        """An upstream Skill must retain the path relative to its declared source root."""
        governance_path = self.repo / "skills-src/l1/local/chinese-thinking/skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        governance["source"]["upstream_path"] = "../outside"
        governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(RegistryValidationError, "upstream path"):
            validate_registry(self.repo)

    def test_validation_rejects_a_skill_id_that_disagrees_with_its_physical_path(self):
        """Catalog paths must remain derivable from the governed Skill ID and layer."""
        governance_path = self.repo / "skills-src/l1/local/chinese-thinking/skill.yaml"
        governance = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
        governance["id"] = "other/chinese-thinking"
        governance["content_digest"] = content_digest(governance_path.parent)
        governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(RegistryValidationError, "physical path"):
            validate_registry(self.repo)


class RepositoryMigrationAcceptanceTest(unittest.TestCase):
    """End-to-end assertions for the repository's source-lifecycle migration."""

    def test_repository_has_no_local_source_manifests_or_importer(self):
        for path in (ROOT / "sources").glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("kind:" + " local", text, path)
            self.assertNotRegex(text, r"(?m)^root:", path)
        self.assertFalse((ROOT / "sources/local-agents-skills.yaml").exists())
        self.assertFalse((ROOT / "src/hwskill/importer.py").exists())
        self.assertFalse((ROOT / "tests/core/test_importer.py").exists())

    def test_manual_skills_have_manual_runtime_revision_and_unchanged_digests(self):
        records = {item.skill_id: item for item in validate_registry(ROOT)}
        expected_digests = {
            "local/chinese-thinking": "sha256:56a71441b20fa1157866261c0da27ee50d8449584b6cfc4aea740a6abb2d9c31",
            "local/gitcode-discussion-fetch": "sha256:45e32ba0345c159ed3564c8585313b630ae2beb5365bd705b34997ff0af2f858",
            "local/gitcode-pr-review-fetch": "sha256:c350afa82407c803d8386471bfbc721c50b6f03c02d6bde768b31d0d8ce541c1",
        }
        for skill_id in MANUAL_SKILL_IDS:
            record = records[skill_id]
            self.assertEqual((record.source_kind, record.source_id, record.revision), ("manual", None, "manual"))
            self.assertEqual(record.content_digest, expected_digests[skill_id])

    def test_superpowers_uses_fixed_upstream_schema_with_the_selected_skills(self):
        source = load_source_manifest(ROOT / "sources/superpowers.yaml")
        self.assertEqual(source.source_id, "superpowers")
        self.assertEqual(source.upstream.repository, "https://github.com/obra/superpowers.git")
        self.assertEqual(source.upstream.track, "refs/tags/v6.3.0")
        self.assertEqual(source.resolved_revision, SUPERPOWERS_REVISION)
        self.assertEqual(tuple(item.path for item in source.skills), SUPERPOWERS_PATHS)
        self.assertEqual(source.upstream.ignore, ())

    def test_schema_two_catalog_keeps_manual_and_upstream_runtime_provenance(self):
        catalog = build_catalog(ROOT)
        self.assertEqual(catalog["schema_version"], 2)
        by_id = {item["id"]: item for item in catalog["skills"]}
        for skill_id in MANUAL_SKILL_IDS:
            self.assertEqual(
                (by_id[skill_id]["source_kind"], by_id[skill_id]["source_id"], by_id[skill_id]["revision"]),
                ("manual", None, "manual"),
            )
        self.assertEqual(
            (by_id["superpowers/brainstorming"]["source_kind"], by_id["superpowers/brainstorming"]["source_id"], by_id["superpowers/brainstorming"]["revision"]),
            ("upstream", "superpowers", SUPERPOWERS_REVISION),
        )

    def test_integrity_check_accepts_the_migrated_repository(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["integrity-check", "--repo-root", str(ROOT)]), 0)


if __name__ == "__main__":
    unittest.main()
