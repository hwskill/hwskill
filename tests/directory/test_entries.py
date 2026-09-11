from __future__ import annotations

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

    def test_ready_recommendation_requires_existing_active_entry(self) -> None:
        report = self.validate("ready-missing-reference")
        self.assertTrue(any(issue.code == "recommendation-missing-entry" for issue in report.issues))
        self.assertEqual(report.publishable_recommendation_ids, ())

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


if __name__ == "__main__":
    unittest.main()
