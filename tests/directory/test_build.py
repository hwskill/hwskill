from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


FIXTURES = Path(__file__).parent / "fixtures"


class DirectoryBuildTests(unittest.TestCase):
    def make_repository(self) -> Path:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        (root / "skills-src/l1/local/hosted/reference.md").write_text("hosted reference\n", encoding="utf-8")
        (root / "recommendations/draft.yaml").write_text(
            "schema_version: 1\nid: draft\nskills:\n  - id: local/hosted\ntitle: Draft\nbody: Never publish this.\nauthor: test\nstatus: draft\n",
            encoding="utf-8",
        )
        return root

    def test_build_is_deterministic_and_keeps_external_content_out(self) -> None:
        """Removing an external body must not change the published output."""
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        external_body = root / "skills-src/l2/upstream/external/SKILL.md"
        external_body.parent.mkdir(parents=True)
        external_body.write_text("this must never be copied\n", encoding="utf-8")
        with TemporaryDirectory() as directory:
            first, second = Path(directory) / "first", Path(directory) / "second"
            first_result = build_repository(root, first)
            external_body.unlink()
            second_result = build_repository(root, second)
            self.assertEqual(first_result.input_digest, second_result.input_digest)
            self.assertEqual(
                sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file()),
                sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file()),
            )
            self.assertEqual((first / "catalog.json").read_bytes(), (second / "catalog.json").read_bytes())
            self.assertFalse(any("this must never" in path.read_text(encoding="utf-8") for path in first.rglob("*") if path.is_file()))
            self.assertTrue((first / "skills/local/hosted/content/SKILL.md").is_file())
            self.assertTrue((first / "skills/local/hosted/content/reference.md").is_file())
            self.assertFalse((first / "skills/upstream/external/content").exists())

    def test_install_material_matches_catalog_and_does_not_invent_unknown_command(self) -> None:
        """A wrong install method or a leaked draft changes the public contract."""
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        external = root / "entries/l2/upstream/external.yaml"
        text = external.read_text(encoding="utf-8").replace("method: upstream", "method: unknown").replace("  instructions_url: https://example.com/org/repository/blob/v1.2.3/README.md\n", "")
        external.write_text(text, encoding="utf-8")
        with TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            build_repository(root, out)
            catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
            ids = [item["entry"]["id"] for item in catalog["entries"]]
            self.assertEqual(ids, ["local/hosted", "upstream/external"])
            recommendations = json.loads((out / "recommendations.json").read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in recommendations["recommendations"]], ["review-tools"])
            install = json.loads((out / "skills/upstream/external/install.json").read_text(encoding="utf-8"))
            self.assertEqual(install["skill_id"], "upstream/external")
            self.assertEqual(install["install"]["method"], "unknown")
            self.assertEqual(install["verification_summary"]["installation"]["result"], "not_run")
            markdown = (out / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertIn("安装方式：unknown", markdown)
            self.assertIn("未提供可执行安装命令", markdown)
            self.assertNotIn("npx ", markdown)


if __name__ == "__main__":
    unittest.main()
