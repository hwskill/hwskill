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
            self.assertEqual(json.loads((first / "curation.json").read_text(encoding="utf-8")), {})

    def test_input_digest_tracks_hosted_content_and_copied_contribution_documents(self) -> None:
        """Omitting either input permits stale public output under the same digest."""
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        guide = root / "docs/guides/agent-contribution.md"
        guide.parent.mkdir(parents=True)
        guide.write_text("first guide\n", encoding="utf-8")
        (root / "CONTRIBUTING.md").write_text("first contribution\n", encoding="utf-8")
        with TemporaryDirectory() as directory:
            first, second, third = (Path(directory) / name for name in ("first", "second", "third"))
            first_result = build_repository(root, first)
            self.assertEqual(first_result.input_digest, build_repository(root, second).input_digest)
            self.assertEqual((first / "catalog.json").read_bytes(), (second / "catalog.json").read_bytes())
            (root / "skills-src/l1/local/hosted/SKILL.md").write_text("changed hosted body\n", encoding="utf-8")
            hosted_changed = build_repository(root, third)
            self.assertNotEqual(first_result.input_digest, hosted_changed.input_digest)
            guide.write_text("changed guide\n", encoding="utf-8")
            guide_changed = build_repository(root, third)
            self.assertNotEqual(hosted_changed.input_digest, guide_changed.input_digest)

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

    def test_catalog_keeps_display_metadata_and_external_install_is_human_readable(self) -> None:
        """Dropping display fields or exposing the internal NUL identity breaks consumers."""
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        hosted = root / "entries/l1/local/hosted.yaml"
        hosted.write_text(hosted.read_text(encoding="utf-8") + "owner: directory-team\nkeywords: [review, evidence]\nlimitations: [requires local checkout]\n", encoding="utf-8")
        external = root / "entries/l2/upstream/external.yaml"
        revision = "a" * 40
        external.write_text(external.read_text(encoding="utf-8").replace("requested_ref: v1.2.3", f"requested_ref: {revision}"), encoding="utf-8")
        with TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            build_repository(root, out)
            catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
            hosted_catalog = next(item["entry"] for item in catalog["entries"] if item["entry"]["id"] == "local/hosted")
            self.assertEqual(hosted_catalog["owner"], "directory-team")
            self.assertEqual(hosted_catalog["keywords"], ["review", "evidence"])
            self.assertEqual(hosted_catalog["limitations"], ["requires local checkout"])
            install = json.loads((out / "skills/upstream/external/install.json").read_text(encoding="utf-8"))
            markdown = (out / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertEqual(install["skill_id"], "upstream/external")
            self.assertEqual(install["source"]["repository"], "https://example.com/org/repository.git")
            self.assertEqual(install["source"]["path"], "skills/review")
            self.assertEqual(install["source"]["resolved_revision"], revision)
            self.assertEqual(install["install"]["default_scope"], "project")
            self.assertNotIn("\0", markdown)
            self.assertIn("https://example.com/org/repository.git", markdown)
            self.assertIn("skills/review", markdown)
            self.assertIn(revision, markdown)

    def test_build_rejects_output_that_can_replace_human_sources(self) -> None:
        """Allowing these destinations can erase the repository before publication."""
        from hwskill.directory.catalog import BuildInputError, build_repository

        root = self.make_repository()
        for output in (root, root.parent, root / "entries", root / "skills-src", root / "docs"):
            with self.subTest(output=output), self.assertRaises(BuildInputError) as raised:
                build_repository(root, output)
            self.assertEqual(raised.exception.issue.code, "unsafe-output-path")
        self.assertTrue((root / "entries/l1/local/hosted.yaml").is_file())

    def test_build_preserves_preexisting_backup_sibling(self) -> None:
        """A predictable backup name must never become a directory deletion target."""
        from hwskill.directory.catalog import BuildInputError, build_repository

        root = self.make_repository()
        with TemporaryDirectory() as directory:
            output = Path(directory) / "published"
            backup = Path(directory) / ".published.previous"
            backup.mkdir()
            marker = backup / "marker"
            marker.write_text("user data\n", encoding="utf-8")
            with self.assertRaises(BuildInputError) as raised:
                build_repository(root, output)
            self.assertEqual(raised.exception.issue.code, "output-backup-conflict")
            self.assertEqual(marker.read_text(encoding="utf-8"), "user data\n")


if __name__ == "__main__":
    unittest.main()
