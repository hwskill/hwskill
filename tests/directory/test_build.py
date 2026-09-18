from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


FIXTURES = Path(__file__).parent / "fixtures"


class DirectoryBuildTests(unittest.TestCase):
    def test_hosted_install_guide_identifies_an_immutable_complete_source(self) -> None:
        from hwskill.directory.catalog import _install_markdown

        entry = {
            "id": "local/hosted",
            "name": "Hosted skill",
            "source": {"kind": "hosted", "path": "skills-src/l1/local/hosted"},
            "install": {"method": "directory", "default_scope": "project"},
        }
        guide = _install_markdown(entry, {"resolved_revision": "a" * 40})
        self.assertIn("https://github.com/hwskill/hwskill/tree/" + "a" * 40 + "/skills-src/l1/local/hosted", guide)
        self.assertIn("复制整个技能目录", guide)
        self.assertIn("SKILL.md", guide)
        self.assertIn("不要覆盖", guide)

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
        (root / "curation").mkdir(exist_ok=True)
        (root / "curation/topics.yaml").write_text("topics:\n  - slug: testing\n    title: 测试\n", encoding="utf-8")
        (root / "curation/synonyms.yaml").write_text("synonyms:\n  testing: [tests]\n", encoding="utf-8")
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
            curation = json.loads((first / "curation.json").read_text(encoding="utf-8"))
            from hwskill.directory.schema import validator_for
            self.assertEqual(set(curation), {"topics", "synonyms"})
            self.assertEqual(list(validator_for("curation").iter_errors(curation)), [])

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
            from hwskill.directory.digest import canonical_json, sha256_bytes
            expected = dict(install)
            self.assertEqual(install["install_digest"], sha256_bytes(canonical_json({key: value for key, value in expected.items() if key != "install_digest"})))
            self.assertEqual(install["install"]["method"], "unknown")
            self.assertEqual(install["verification_summary"]["installation"]["result"], "not_run")
            markdown = (out / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertIn("安装方式：unknown", markdown)
            self.assertIn("未提供可执行安装命令", markdown)
            self.assertNotIn("npx ", markdown)

    def test_build_keeps_withdrawn_recommendation_tombstones_but_excludes_drafts(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        (root / "recommendations/withdrawn.yaml").write_text(
            "schema_version: 1\nid: retired-guide\nskills:\n  - id: local/hosted\n"
            "title: Retired guide\nbody: Historical recommendation.\nauthor: test\n"
            "status: withdrawn\nwithdrawal_reason: Superseded.\n",
            encoding="utf-8",
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            recommendations = json.loads((output / "recommendations.json").read_text(encoding="utf-8"))

        self.assertEqual(
            [(item["id"], item["status"]) for item in recommendations["recommendations"]],
            [("retired-guide", "withdrawn"), ("review-tools", "ready")],
        )

    def test_hosted_source_identity_uses_the_fixed_build_revision(self) -> None:
        """A hosted report cannot bind a mutable working directory as its source revision."""
        from hwskill.directory.catalog import _identity

        identity = _identity(
            {"source": {"kind": "hosted", "path": "skills-src/l1/local/hosted"}},
            Path("/repository"),
            "a" * 40,
        )
        self.assertEqual(identity["resolved_revision"], "a" * 40)

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
            external_catalog = next(item for item in catalog["entries"] if item["entry"]["id"] == "upstream/external")
            self.assertEqual(hosted_catalog["owner"], "directory-team")
            self.assertEqual(hosted_catalog["keywords"], ["review", "evidence"])
            self.assertEqual(hosted_catalog["limitations"], ["requires local checkout"])
            self.assertEqual(external_catalog["source_identity"]["requested_ref"], revision)
            self.assertIsNone(external_catalog["source_identity"]["resolved_revision"])
            install = json.loads((out / "skills/upstream/external/install.json").read_text(encoding="utf-8"))
            markdown = (out / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertEqual(install["skill_id"], "upstream/external")
            self.assertEqual(install["source"]["repository"], "https://example.com/org/repository.git")
            self.assertEqual(install["source"]["path"], "skills/review")
            self.assertEqual(install["source"]["requested_ref"], revision)
            self.assertIsNone(install["source"]["resolved_revision"])
            self.assertEqual(install["install"]["default_scope"], "project")
            self.assertNotIn("\0", markdown)
            self.assertIn("https://example.com/org/repository.git", markdown)
            self.assertIn("skills/review", markdown)
            self.assertIn(revision, markdown)

    def test_external_web_install_uses_its_exact_locator_variant(self) -> None:
        """A web URL must not be disguised as a git repository with a null path."""
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        (root / "entries/l1/local/web.yaml").write_text(
            "schema_version: 1\nid: local/web\nname: Web source\n"
            "summary: An externally hosted web skill.\nlayer: l1\npurposes: [testing]\n"
            "examples:\n  - prompt: Test it.\n    expected_outcome: Evidence.\n"
            "source:\n  kind: external\n  publicity: public\n  locator:\n"
            "    type: web\n    url: HTTPS://SOURCE.example/skill/\n    version_note: stable\n"
            "install:\n  method: unknown\n  default_scope: project\n"
            "compatibility:\n  agents: [unknown]\n  systems: [unknown]\n  requirements: []\n"
            "license:\n  status: unknown\n",
            encoding="utf-8",
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            item = next(item for item in catalog["entries"] if item["entry"]["id"] == "local/web")
            install = json.loads((output / "skills/local/web/install.json").read_text(encoding="utf-8"))

        self.assertEqual(
            set(install["source"]),
            {"kind", "url", "requested_ref", "resolved_revision"},
        )
        self.assertEqual(install["source"]["url"], "HTTPS://SOURCE.example/skill/")
        self.assertEqual(install["source"]["requested_ref"], "stable")
        self.assertEqual(item["source_identity"]["identity"], "web:https://source.example/skill")
        self.assertEqual(item["source_identity"]["requested_ref"], "stable")

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
