from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hwskill.digest import content_digest
from hwskill.importer import ImportConflictError, SkillImportError, import_source
from hwskill.models import SourceSpec


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_root = self.root / "source"
        self.repo = self.root / "repo"
        self.source_root.mkdir()
        self.repo.mkdir()
        self.make_skill("example", "Example body\n")

    def tearDown(self):
        self.temp.cleanup()

    def make_skill(self, name: str, body: str) -> Path:
        skill = self.source_root / name
        skill.mkdir(exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Example skill\n---\n\n{body}",
            encoding="utf-8",
        )
        return skill

    def spec(self) -> SourceSpec:
        return SourceSpec.from_mapping({
            "source_id": "local-test",
            "kind": "local",
            "root": str(self.source_root),
            "namespace": "local",
            "revision": "working-tree",
            "license": "unknown",
            "skills": [{"name": "example", "layer": "l1"}],
        })

    def test_import_copies_payload_and_digest_excludes_governance(self):
        records = import_source(
            self.spec(), self.repo, imported_at="2026-08-27T00:00:00Z"
        )
        target = self.repo / "skills-src/l1/local/example"
        self.assertEqual(
            (target / "SKILL.md").read_text(encoding="utf-8"),
            (self.source_root / "example/SKILL.md").read_text(encoding="utf-8"),
        )
        before = content_digest(target)
        governance = target / "skill.yaml"
        governance.write_text(governance.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(content_digest(target), before)
        self.assertEqual(records[0].content_digest, before)

    def test_import_excludes_python_cache_artifacts_from_snapshot(self):
        cache = self.source_root / "example/scripts/__pycache__"
        cache.mkdir(parents=True)
        (cache / "helper.cpython-310.pyc").write_bytes(b"machine-specific cache")

        records = import_source(
            self.spec(), self.repo, imported_at="2026-08-27T00:00:00Z"
        )

        target = self.repo / "skills-src/l1/local/example"
        self.assertFalse((target / "scripts/__pycache__").exists())
        self.assertEqual(records[0].content_digest, content_digest(target))

    def test_import_rejects_symlink_outside_skill(self):
        outside = self.root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (self.source_root / "example/escape").symlink_to(outside)
        with self.assertRaisesRegex(SkillImportError, "outside the skill directory"):
            import_source(self.spec(), self.repo)

    def test_existing_difference_requires_update(self):
        import_source(self.spec(), self.repo)
        self.make_skill("example", "Changed body\n")
        with self.assertRaisesRegex(ImportConflictError, "--update"):
            import_source(self.spec(), self.repo)

if __name__ == "__main__":
    unittest.main()
