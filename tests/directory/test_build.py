from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


FIXTURES = Path(__file__).parent / "fixtures"


class DirectoryBuildTests(unittest.TestCase):
    def test_catalog_v2_contains_translation_without_durable_verification_state(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        external = root / "entries/l2/upstream/external.yaml"
        external.write_text(
            external.read_text(encoding="utf-8")
            .replace("https://example.com/org/repository.git", "https://github.com/example/repository.git")
            .replace("    file_url: https://example.com/source/SKILL.md\n", "")
            .replace("ref: v1.2.3", "ref: main"),
            encoding="utf-8",
        )

        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            item = next(value for value in catalog["entries"] if value["entry"]["id"] == "upstream/external")

        self.assertEqual(catalog["schema_version"], 2)
        from hwskill.directory.schema import validator_for
        self.assertEqual(list(validator_for("catalog").iter_errors(catalog)), [])
        self.assertEqual(set(item), {"entry", "entry_digest", "lifecycle", "translation"})
        self.assertNotIn("source_identity", item)
        self.assertNotIn("verification_summary", item)
        self.assertNotIn("install_capability", item)
        self.assertEqual(item["translation"]["body_format"], "markdown")
        self.assertEqual(item["translation"]["translated_at"], "2026-09-20")
        self.assertEqual(
            item["translation"]["source_url"],
            "https://github.com/example/repository/blob/main/skills/review/SKILL.md",
        )
        self.assertNotIn("verification_note", status)
    def test_build_publishes_current_contribution_templates_for_agent_prompts(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        repository_templates = Path(__file__).parents[2] / "templates"
        shutil.copytree(repository_templates, root / "templates")
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            for relative in (
                "entries/external.yaml",
                "entries/hosted.yaml",
                "recommendations/recommendation.md",
            ):
                self.assertEqual(
                    (output / "templates" / relative).read_bytes(),
                    (repository_templates / relative).read_bytes(),
                )

    def test_build_preserves_included_skill_dependencies_in_public_install_material(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        external = root / "entries/l2/upstream/external.yaml"
        text = external.read_text(encoding="utf-8").replace(
            "  default_scope: project\n",
            "  default_scope: project\n  included_skills: [external, dependency]\n",
        )
        external.write_text(text, encoding="utf-8")
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            item = next(item for item in catalog["entries"] if item["entry"]["id"] == "upstream/external")
            install = json.loads((output / "skills/upstream/external/install.json").read_text(encoding="utf-8"))
            guide = (output / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertEqual(item["entry"]["install"]["included_skills"], ["external", "dependency"])
            self.assertEqual(install["install"]["included_skills"], ["external", "dependency"])
            self.assertIn("同时包含：external、dependency", guide)

    def test_hosted_install_prompt_requires_agent_checks(self) -> None:
        from hwskill.directory.catalog import _install_markdown

        entry = {
            "id": "local/hosted",
            "name": "Hosted skill",
            "source": {"kind": "hosted", "path": "skills-src/l1/local/hosted"},
            "install": {"method": "directory", "default_scope": "project"},
        }
        guide = _install_markdown(entry)
        self.assertIn("完整技能目录及配套文件", guide)
        self.assertIn("不要覆盖已有同名技能", guide)
        self.assertIn("报告实际来源、目标路径和结果", guide)
        self.assertNotIn("验证状态", guide)

    def test_optional_ref_is_described_without_a_verification_claim(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            external_guide = (output / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertIn("ref：v1.2.3", external_guide)
            self.assertIn("v1.2.3", external_guide)
            self.assertNotIn("已验证", external_guide)
            self.assertNotIn("解析提交", external_guide)

    def test_hosted_content_is_copied_without_derived_capability(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        import subprocess
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture"], check=True)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            items = json.loads((output / "catalog.json").read_text(encoding="utf-8"))["entries"]
            self.assertTrue((output / "skills/local/hosted/content/SKILL.md").is_file())
            self.assertTrue(all("install_capability" not in item for item in items))

    def test_hosted_install_guide_links_to_the_current_original_file(self) -> None:
        from hwskill.directory.catalog import _install_markdown

        entry = {
            "id": "local/hosted",
            "name": "Hosted skill",
            "source": {"kind": "hosted", "path": "skills-src/l1/local/hosted"},
            "install": {"method": "directory", "default_scope": "project"},
        }
        guide = _install_markdown(entry)
        self.assertIn("https://github.com/hwskill/hwskill/blob/HEAD/skills-src/l1/local/hosted/SKILL.md", guide)
        self.assertIn("SKILL.md", guide)
        self.assertIn("不要覆盖", guide)

    def make_repository(self) -> Path:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        (root / "skills-src/l1/local/hosted/reference.md").write_text("hosted reference\n", encoding="utf-8")
        (root / "recommendations/draft.md").write_text(
            "---\nschema_version: 1\nid: draft\nskills:\n  - id: local/hosted\ntitle: Draft\nsummary: Never publish this.\nauthor: test\nstatus: draft\n---\n\nNever publish this.\n",
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
            recommendation = root / "recommendations/review-tools.md"
            recommendation.write_text(recommendation.read_text(encoding="utf-8") + "\nChanged body.\n", encoding="utf-8")
            recommendation_changed = build_repository(root, third)
            self.assertNotEqual(guide_changed.input_digest, recommendation_changed.input_digest)

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
            self.assertEqual(install["schema_version"], 2)
            self.assertNotIn("verification_summary", install)
            markdown = (out / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertIn("安装方式：unknown", markdown)
            self.assertIn("不要猜测命令", markdown)
            self.assertNotIn("npx ", markdown)

    def test_build_keeps_withdrawn_recommendation_tombstones_but_excludes_drafts(self) -> None:
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        (root / "recommendations/retired-guide.md").write_text(
            "---\nschema_version: 1\nid: retired-guide\nskills:\n  - id: local/hosted\n"
            "title: Retired guide\nsummary: Historical recommendation.\nauthor: test\n"
            "status: withdrawn\nwithdrawal_reason: Superseded.\n---\n\nHistorical recommendation.\n",
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

    def test_hosted_source_url_does_not_publish_a_resolved_revision(self) -> None:
        from hwskill.directory.catalog import build_source_url

        url = build_source_url({"source": {"kind": "hosted", "path": "skills-src/l1/local/hosted"}})
        self.assertEqual(url, "https://github.com/hwskill/hwskill/blob/HEAD/skills-src/l1/local/hosted/SKILL.md")

    def test_catalog_keeps_display_metadata_and_external_install_is_human_readable(self) -> None:
        """Dropping display fields or exposing the internal NUL identity breaks consumers."""
        from hwskill.directory.catalog import build_repository

        root = self.make_repository()
        hosted = root / "entries/l1/local/hosted.yaml"
        hosted.write_text(hosted.read_text(encoding="utf-8") + "owner: directory-team\nkeywords: [review, evidence]\nlimitations: [requires local checkout]\n", encoding="utf-8")
        external = root / "entries/l2/upstream/external.yaml"
        revision = "a" * 40
        external.write_text(external.read_text(encoding="utf-8").replace("ref: v1.2.3", f"ref: {revision}"), encoding="utf-8")
        with TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            build_repository(root, out)
            catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
            hosted_catalog = next(item["entry"] for item in catalog["entries"] if item["entry"]["id"] == "local/hosted")
            external_catalog = next(item for item in catalog["entries"] if item["entry"]["id"] == "upstream/external")
            self.assertEqual(hosted_catalog["owner"], "directory-team")
            self.assertEqual(hosted_catalog["keywords"], ["review", "evidence"])
            self.assertEqual(hosted_catalog["limitations"], ["requires local checkout"])
            self.assertEqual(external_catalog["entry"]["source"]["locator"]["ref"], revision)
            self.assertNotIn("source_identity", external_catalog)
            install = json.loads((out / "skills/upstream/external/install.json").read_text(encoding="utf-8"))
            markdown = (out / "skills/upstream/external/install.md").read_text(encoding="utf-8")
            self.assertEqual(install["skill_id"], "upstream/external")
            self.assertEqual(install["source"]["locator"]["repository"], "https://example.com/org/repository.git")
            self.assertEqual(install["source"]["locator"]["path"], "skills/review")
            self.assertEqual(install["source"]["locator"]["ref"], revision)
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
            "schema_version: 2\nid: local/web\nname: Web source\n"
            "summary: An externally hosted web skill.\nlayer: l1\npurposes: [testing]\n"
            "examples:\n  - prompt: Test it.\n    expected_outcome: Evidence.\n"
            "source:\n  kind: external\n  publicity: public\n  locator:\n"
            "    type: web\n    url: HTTPS://SOURCE.example/skill/\n    version_note: stable\n"
            "install:\n  method: unknown\n  default_scope: project\n"
            "compatibility:\n  agents: [unknown]\n  systems: [unknown]\n  requirements: []\n"
            "license:\n  status: unknown\n",
            encoding="utf-8",
        )
        translation = root / "translations/local/web.md"
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text(
            "---\nschema_version: 1\nskill_id: local/web\n"
            "translated_at: 2026-09-20\n---\n\n# Web source\n",
            encoding="utf-8",
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            build_repository(root, output)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            item = next(item for item in catalog["entries"] if item["entry"]["id"] == "local/web")
            install = json.loads((output / "skills/local/web/install.json").read_text(encoding="utf-8"))

        self.assertEqual(install["source"]["kind"], "external")
        self.assertEqual(install["source"]["locator"]["url"], "HTTPS://SOURCE.example/skill/")
        self.assertEqual(install["source"]["locator"]["version_note"], "stable")
        self.assertEqual(item["translation"]["source_url"], "HTTPS://SOURCE.example/skill/")
        self.assertNotIn("source_identity", item)

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
