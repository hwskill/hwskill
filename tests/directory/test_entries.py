from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


FIXTURES = Path(__file__).parent / "fixtures"


class DirectoryValidationTests(unittest.TestCase):
    """Each fixture is authored independently of the directory normalizer."""

    def validate(self, fixture: str):
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / fixture, root)
        return validate_repository(root)

    def test_accepts_valid_hosted_and_external_entries(self) -> None:
        report = self.validate("valid")
        self.assertEqual(report.result, "pass")
        self.assertEqual(report.issues, ())
        self.assertEqual(report.publishable_entry_ids, ("local/hosted", "upstream/external"))
        self.assertEqual(report.publishable_recommendation_ids, ("review-tools",))

    def test_translation_loader_normalizes_crlf_and_appends_one_newline(self) -> None:
        from hwskill.directory.translations import load_translation, translation_path

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = translation_path(root, "local/hosted")
            path.parent.mkdir(parents=True)
            path.write_bytes(
                b"---\r\nschema_version: 1\r\nskill_id: local/hosted\r\n"
                b"translated_at: 2026-09-20\r\n---\r\n\r\n# Translation\r\n\r\n"
            )

            document = load_translation(path)

        self.assertEqual(document["skill_id"], "local/hosted")
        self.assertEqual(document["body_format"], "markdown")
        self.assertEqual(document["body"], "# Translation\n")

    def test_translation_loader_rejects_invalid_frontmatter_and_empty_body(self) -> None:
        from hwskill.directory.translations import TranslationContractError, load_translation

        cases = {
            "missing-opening.md": ("schema_version: 1\n", "translation-frontmatter-missing"),
            "missing-closing.md": ("---\nschema_version: 1\n", "translation-frontmatter-unclosed"),
            "empty-body.md": (
                "---\nschema_version: 1\nskill_id: local/hosted\ntranslated_at: 2026-09-20\n---\n   \n",
                "translation-body-empty",
            ),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (contents, expected_code) in cases.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(contents, encoding="utf-8")
                    with self.assertRaises(TranslationContractError) as raised:
                        load_translation(path)
                    self.assertEqual(raised.exception.code, expected_code)

    def test_translation_loader_preserves_duplicate_key_error(self) -> None:
        from hwskill.directory.translations import load_translation
        from hwskill.directory.yaml_io import YamlContractError

        with TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.md"
            path.write_text(
                "---\nschema_version: 1\nskill_id: local/hosted\n"
                "skill_id: local/other\ntranslated_at: 2026-09-20\n---\nBody\n",
                encoding="utf-8",
            )
            with self.assertRaises(YamlContractError):
                load_translation(path)

    def test_translation_requires_matching_skill_id_and_valid_frontmatter(self) -> None:
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        hosted = root / "translations/local/hosted.md"
        hosted.parent.mkdir(parents=True, exist_ok=True)
        hosted.write_text(
            "---\nschema_version: 1\nskill_id: local/other\n"
            "translated_at: yesterday\nunexpected: true\n---\n\n# Hosted\n",
            encoding="utf-8",
        )

        report = validate_repository(root)
        codes = {issue.code for issue in report.issues}
        self.assertIn("translation-path-mismatch", codes)
        self.assertIn("schema-format", codes)
        self.assertIn("schema-additionalProperties", codes)

    def test_publishable_entry_requires_translation_and_orphans_are_rejected(self) -> None:
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        missing = root / "translations/local/hosted.md"
        missing.unlink()
        orphan = root / "translations/local/orphan.md"
        orphan.write_text(
            "---\nschema_version: 1\nskill_id: local/orphan\n"
            "translated_at: 2026-09-20\n---\n\n# Orphan\n",
            encoding="utf-8",
        )

        report = validate_repository(root)
        codes = {issue.code for issue in report.issues}
        self.assertIn("translation-missing", codes)
        self.assertIn("translation-orphan", codes)
        self.assertNotIn("local/hosted", report.publishable_entry_ids)

    def test_rejects_duplicate_yaml_key(self) -> None:
        report = self.validate("duplicate-key")
        self.assertTrue(any(issue.code == "yaml-duplicate-key" for issue in report.issues))

    def test_rejects_yaml_merge_key(self) -> None:
        report = self.validate("merge-key")
        self.assertTrue(any(issue.code == "yaml-merge-key" for issue in report.issues))

    def test_rejects_unknown_schema_field(self) -> None:
        report = self.validate("unknown-field")
        self.assertTrue(any(issue.code == "schema-additionalProperties" for issue in report.issues))

    def test_rejects_invalid_url_and_date(self) -> None:
        report = self.validate("invalid-url-date")
        fields = {issue.field for issue in report.issues if issue.code == "schema-format"}
        self.assertEqual(fields, {"evidence[0].observed_at", "evidence[0].url"})

    def test_rejects_id_path_mismatch(self) -> None:
        report = self.validate("id-path-mismatch")
        self.assertTrue(any(issue.code == "entry-path-mismatch" for issue in report.issues))

    def test_rejects_hosted_path_escaping_repository(self) -> None:
        report = self.validate("hosted-path-escape")
        self.assertTrue(any(issue.code == "hosted-path-unsafe" for issue in report.issues))

    def test_rejects_duplicate_source_identity(self) -> None:
        report = self.validate("duplicate-source")
        self.assertTrue(any(issue.code == "duplicate-source-identity" for issue in report.issues))

    def test_normalizes_equivalent_web_source_identity(self) -> None:
        report = self.validate("duplicate-web-source")
        self.assertTrue(any(issue.code == "duplicate-source-identity" for issue in report.issues))

    def test_invalid_uri_port_is_a_schema_issue_not_a_crash(self) -> None:
        report = self.validate("invalid-uri-port")
        self.assertTrue(any(issue.code == "schema-format" for issue in report.issues))

    def test_ready_recommendation_requires_existing_active_entry(self) -> None:
        report = self.validate("ready-missing-reference")
        self.assertTrue(any(issue.code == "recommendation-missing-entry" for issue in report.issues))
        self.assertEqual(report.publishable_recommendation_ids, ())

    def test_ready_recommendation_rejects_entry_that_is_not_publishable(self) -> None:
        report = self.validate("ready-invalid-entry")
        self.assertTrue(any(issue.code == "recommendation-unpublishable-entry" for issue in report.issues))

    def test_rejects_duplicate_recommendation_id_and_missing_replacement(self) -> None:
        report = self.validate("duplicate-recommendation-and-replacement")
        codes = {issue.code for issue in report.issues}
        self.assertIn("duplicate-recommendation-id", codes)
        self.assertIn("replacement-entry-missing", codes)

    def test_rejects_unsafe_external_git_path(self) -> None:
        report = self.validate("external-path-escape")
        self.assertTrue(any(issue.code == "external-path-unsafe" for issue in report.issues))

    def test_rejects_credentials_in_every_public_entry_url(self) -> None:
        from hwskill.directory.entries import validate_repository

        for field in ("repository", "web", "instructions", "license", "evidence"):
            with self.subTest(field=field):
                temporary = TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                root = Path(temporary.name) / "repository"
                fixture = "external-no-read" if field == "web" else "valid"
                shutil.copytree(FIXTURES / fixture, root)
                entry_path = (
                    root / "entries/l1/local/external.yaml"
                    if field == "web"
                    else root / "entries/l2/upstream/external.yaml"
                )
                entry = entry_path.read_text(encoding="utf-8")
                if field == "repository":
                    entry = entry.replace(
                        "https://example.com/org/repository.git",
                        "https://user:secret@example.com/org/repository.git",
                        1,
                    )
                elif field == "web":
                    entry = entry.replace(
                        "https://example.com/skill",
                        "https://user:secret@example.com/skill",
                    )
                elif field == "instructions":
                    entry = entry.replace(
                        "https://example.com/org/repository/blob/v1.2.3/README.md",
                        "https://user:secret@example.com/install",
                    )
                elif field == "license":
                    entry = entry.replace("  identifier: MIT\n", "  url: https://user:secret@example.com/license\n")
                else:
                    recommendation_path = root / "recommendations/review-tools.md"
                    recommendation_text = recommendation_path.read_text(encoding="utf-8")
                    recommendation_path.write_text(
                        recommendation_text.replace(
                            "status: ready\n---",
                            "evidence:\n- url: https://user:secret@example.com/evidence\n  observed_at: 2026-09-11T00:00:00Z\nstatus: ready\n---",
                        ),
                        encoding="utf-8",
                    )
                entry_path.write_text(entry, encoding="utf-8")

                report = validate_repository(root)
                self.assertTrue(
                    any(issue.code == "public-url-unsafe" for issue in report.issues),
                    report.issues,
                )

    def test_rejects_git_path_and_requested_ref_option_injection_shapes(self) -> None:
        from hwskill.directory.entries import validate_repository

        cases = {
            "path": ["/absolute", ".", "skills/../evil", "skills//evil", "skills\\evil"],
            "requested_ref": ["", " v1", "v1 ", "--upload-pack=evil", "v1\tother"],
        }
        for field, values in cases.items():
            for index, value in enumerate(values):
                with self.subTest(field=field, value=value):
                    temporary = TemporaryDirectory()
                    self.addCleanup(temporary.cleanup)
                    root = Path(temporary.name) / "repository"
                    shutil.copytree(FIXTURES / "valid", root)
                    entry_path = root / "entries/l2/upstream/external.yaml"
                    entry = entry_path.read_text(encoding="utf-8")
                    original = "skills/review" if field == "path" else "v1.2.3"
                    replacement = value if field == "path" else json.dumps(value)
                    entry_path.write_text(entry.replace(original, replacement, 1), encoding="utf-8")

                    report = validate_repository(root)
                    expected = "external-path-unsafe" if field == "path" else "external-ref-unsafe"
                    self.assertTrue(
                        any(issue.code == expected for issue in report.issues)
                        or (value == "" and any(issue.code.startswith("schema-") for issue in report.issues)),
                        report.issues,
                    )

    def test_draft_recommendation_is_valid_but_not_publishable(self) -> None:
        report = self.validate("draft")
        self.assertEqual(report.result, "pass")
        self.assertEqual(report.publishable_recommendation_ids, ())

    def test_withdrawn_recommendation_requires_reason(self) -> None:
        report = self.validate("withdrawn-without-reason")
        self.assertTrue(any(issue.code == "schema-required" and issue.field == "withdrawal_reason" for issue in report.issues))

    def test_external_entry_does_not_read_skills_source_content(self) -> None:
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "external-no-read", root)
        original_read_text = Path.read_text

        def reject_skill_body(path: Path, *args, **kwargs):
            if path.name == "SKILL.md":
                raise AssertionError("external source body was opened")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", reject_skill_body):
            report = validate_repository(root)
        self.assertEqual(report.result, "pass")

    def test_uri_and_rfc3339_checks_reject_near_misses(self) -> None:
        from hwskill.directory.schema import validator_for

        checker = validator_for("recommendation").format_checker
        self.assertFalse(checker.conforms("http://", "uri"))
        self.assertFalse(checker.conforms("https://exa mple.test/path", "uri"))
        self.assertFalse(checker.conforms("https://example.test:not-a-port/path", "uri"))
        self.assertFalse(checker.conforms("2026-01-01T00:00Z", "date-time"))
        self.assertFalse(checker.conforms("2026-01-01T00:00:00+00:00:30", "date-time"))
        self.assertTrue(checker.conforms("2026-01-01T00:00:00+00:00", "date-time"))

    def test_yaml_loader_rejects_non_json_values(self) -> None:
        from hwskill.directory.yaml_io import YamlContractError, load_yaml

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in {
                "set.yaml": "value: !!set {one: null}\n",
                "bytes.yaml": "value: !!binary SGVsbG8=\n",
                "nan.yaml": "value: .nan\n",
                "key.yaml": "1: value\n",
            }.items():
                path = root / name
                path.write_text(text, encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(YamlContractError):
                    load_yaml(path)

    def test_output_schemas_reject_incomplete_nested_contracts(self) -> None:
        from hwskill.directory.schema import validator_for

        cases = {
            "catalog": {"schema_version": 1, "source_commit": None, "entries": [{}]},
            "verification": {"schema_version": 1, "report_id": "r", "skill_id": "local/test", "source_identity": {}, "entry_digest": "a", "install_digest": "b", "host": "codex", "runner_identity": "runner", "executed_at": "2026-01-01T00:00:00Z", "stages": {}, "evidence_refs": []},
            "release": {"schema_version": 1, "release_id": "r", "sequence": 1, "source_commit": "abc", "publication_time": "2026-01-01T00:00:00Z", "committed_at": "2026-01-01T00:00:00Z", "events": [{}]},
        }
        for name, document in cases.items():
            with self.subTest(schema=name):
                self.assertTrue(list(validator_for(name).iter_errors(document)))

    def test_root_schemas_match_packaged_resources(self) -> None:
        from importlib.resources import files

        package_schemas = files("hwskill.directory").joinpath("schemas")
        for root_schema in Path("schemas").glob("*.schema.json"):
            with self.subTest(schema=root_schema.name):
                self.assertEqual(root_schema.read_bytes(), package_schemas.joinpath(root_schema.name).read_bytes())

    def test_templates_are_schema_validated_and_part_of_input_digest(self) -> None:
        """A malformed template must not silently pass or retain its old digest."""
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        template = root / "templates/entries/hosted.yaml"
        template.parent.mkdir(parents=True)
        template.write_text((FIXTURES / "valid/entries/l1/local/hosted.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        clean = validate_repository(root)
        template.write_text(template.read_text(encoding="utf-8") + "unexpected: value\n", encoding="utf-8")
        invalid = validate_repository(root)
        self.assertNotEqual(clean.input_digest, invalid.input_digest)
        issue = next(issue for issue in invalid.issues if issue.file == "templates/entries/hosted.yaml")
        self.assertEqual(issue.field, "$")
        self.assertEqual(issue.code, "schema-additionalProperties")

    def test_curation_is_validated_and_part_of_input_digest(self) -> None:
        """Site-facing curation must be a validated derived input, not free YAML."""
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        curation = root / "curation/topics.yaml"
        curation.parent.mkdir(parents=True, exist_ok=True)
        curation.write_text("topics:\n  - slug: testing\n    title: 测试\n", encoding="utf-8")
        clean = validate_repository(root)
        curation.write_text("topics:\n  - slug: invalid slug\n    title: 测试\n", encoding="utf-8")
        invalid = validate_repository(root)
        self.assertNotEqual(clean.input_digest, invalid.input_digest)
        self.assertTrue(any(issue.file == "curation/topics.yaml" and issue.code == "schema-pattern" for issue in invalid.issues))

    def test_curation_requires_both_final_sections_during_validation(self) -> None:
        """A build-only missing-section failure is an input error, not an environment error."""
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        curation = root / "curation"
        curation.mkdir(exist_ok=True)
        (curation / "synonyms.yaml").unlink()
        (curation / "topics.yaml").write_text("topics: []\n", encoding="utf-8")
        report = validate_repository(root)
        self.assertEqual(report.result, "fail")
        self.assertTrue(any(issue.code == "curation-missing-section" and issue.field == "synonyms" for issue in report.issues))

    def test_curation_directory_is_required_during_validation(self) -> None:
        from hwskill.directory.entries import validate_repository

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        shutil.copytree(FIXTURES / "valid", root)
        shutil.rmtree(root / "curation")
        report = validate_repository(root)
        self.assertTrue(any(issue.code == "curation-missing-section" and issue.field == "topics" for issue in report.issues))

    def test_initial_external_entries_use_verified_skill_subdirectories(self) -> None:
        """Root repository references are not substitutes for a verified SKILL.md path."""
        from hwskill.directory.yaml_io import load_yaml

        entries = Path("entries")
        expected = {
            "community/performance-patterns.yaml": "skills/performance-patterns",
            "data-engineering/spark-and-distributed-processing.yaml": "skills/spark-and-distributed-processing",
        }
        for relative, skill_path in expected.items():
            document = load_yaml(next(entries.rglob(relative)))
            self.assertEqual(document["source"]["locator"]["path"], skill_path)


    def test_markdown_recommendation_is_normalized_and_path_checked(self) -> None:
        from hwskill.directory.recommendations import load_recommendation

        with TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_bytes(
                b"---\r\nschema_version: 1\r\nid: guide\r\nskills:\r\n  - id: local/hosted\r\n"
                b"title: Guide\r\nsummary: Short guide.\r\nauthor: test\r\nstatus: ready\r\n---\r\n\r\n# Body\r\n"
            )
            document = load_recommendation(path)
            self.assertEqual(document["body_format"], "markdown")
            self.assertEqual(document["body"], "# Body\n")


    def test_markdown_recommendation_rejects_malformed_boundaries_and_empty_body(self) -> None:
        from hwskill.directory.recommendations import RecommendationContractError, load_recommendation
        from hwskill.directory.yaml_io import YamlContractError

        cases = {
            "missing.md": ("id: missing\n", RecommendationContractError, "recommendation-frontmatter-missing"),
            "unclosed.md": ("---\nid: unclosed\n", RecommendationContractError, "recommendation-frontmatter-unclosed"),
            "empty.md": ("---\nid: empty\n---\n  \n", RecommendationContractError, "recommendation-body-empty"),
            "duplicate.md": ("---\nid: one\nid: two\n---\nBody\n", YamlContractError, "duplicate YAML key"),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (content, error_type, expected) in cases.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(error_type) as raised:
                    load_recommendation(path)
                if isinstance(raised.exception, RecommendationContractError):
                    self.assertEqual(raised.exception.code, expected)
                else:
                    self.assertIn(expected, str(raised.exception))

    def test_markdown_recommendation_source_schema_and_path_are_enforced(self) -> None:
        from hwskill.directory.entries import validate_repository

        with TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            shutil.copytree(FIXTURES / "valid", root)
            shutil.rmtree(root / "recommendations")
            recommendations = root / "recommendations"
            recommendations.mkdir()
            (recommendations / "wrong-name.md").write_text(
                "---\nschema_version: 1\nid: guide\nskills:\n  - id: local/hosted\n"
                "title: Guide\nsummary: Short guide.\nauthor: test\nstatus: ready\n---\n\nBody.\n",
                encoding="utf-8",
            )
            report = validate_repository(root)
            self.assertTrue(any(issue.code == "recommendation-path-mismatch" for issue in report.issues), report.issues)

            (recommendations / "wrong-name.md").write_text(
                "---\nschema_version: 1\nid: wrong-name\nskills:\n  - id: local/hosted\n"
                "title: Guide\nsummary: Short guide.\nbody: forbidden\nauthor: test\nstatus: ready\n---\n\nBody.\n",
                encoding="utf-8",
            )
            report = validate_repository(root)
            self.assertTrue(any(issue.code == "schema-additionalProperties" for issue in report.issues), report.issues)

    def test_normalized_schema_accepts_legacy_plain_text_recommendation(self) -> None:
        from hwskill.directory.schema import validator_for

        legacy = {
            "schema_version": 1,
            "id": "legacy",
            "skills": [{"id": "local/hosted"}],
            "title": "Legacy",
            "body": "Plain text.",
            "author": "test",
            "status": "ready",
        }
        self.assertEqual(list(validator_for("recommendation").iter_errors(legacy)), [])


if __name__ == "__main__":
    unittest.main()
