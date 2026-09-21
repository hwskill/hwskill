from __future__ import annotations

import csv
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).parents[2]
LEDGER = ROOT / "docs/migrations/catalog-v2-source-refs.csv"
COLUMNS = (
    "skill_id",
    "repository",
    "path",
    "current_requested_ref",
    "proposed_ref",
    "decision",
    "reason",
)


def approved_rows() -> list[dict[str, str]]:
    with LEDGER.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def create_legacy_repository(parent: Path) -> Path:
    """Reconstruct the pre-migration source state from the approved ledger."""
    from hwskill.directory.yaml_io import load_yaml

    root = parent / "repository"
    shutil.copytree(ROOT / "entries", root / "entries")
    paths_by_id = {
        entry["id"]: path
        for path in (root / "entries").rglob("*.yaml")
        for entry in [load_yaml(path)]
    }
    for row in approved_rows():
        path = paths_by_id[row["skill_id"]]
        text = path.read_text(encoding="utf-8")
        text = text.replace("schema_version: 2", "schema_version: 1", 1)
        source_path = f"    path: {row['path']}\n"
        legacy_source_path = source_path + f"    requested_ref: {row['current_requested_ref']}\n"
        if source_path not in text:
            raise AssertionError(f"missing source path in {path}: {row['path']}")
        text = text.replace(source_path, legacy_source_path, 1)
        text = text.replace("/blob/HEAD/", f"/blob/{row['current_requested_ref']}/")
        path.write_text(text, encoding="utf-8")
    return root


class CatalogV2SourceRefMigrationTests(unittest.TestCase):
    def test_generation_is_sorted_complete_and_defaults_to_omit(self) -> None:
        from scripts.migration.catalog_v2_source_refs import generate_rows

        with TemporaryDirectory() as temporary:
            root = create_legacy_repository(Path(temporary))
            first = generate_rows(root)
            second = generate_rows(root)

        self.assertEqual(first, second)
        self.assertEqual(first, approved_rows())
        self.assertEqual([row["skill_id"] for row in first], sorted(row["skill_id"] for row in first))
        self.assertEqual(len(first), 42)
        self.assertEqual(len({row["skill_id"] for row in first}), 42)
        self.assertTrue(all(tuple(row) == COLUMNS for row in first))
        self.assertTrue(all(row["decision"] == "omit" for row in first))
        self.assertTrue(all(row["proposed_ref"] == "" for row in first))
        self.assertTrue(all(row["current_requested_ref"] for row in first))

    def test_decision_contract_rejects_invalid_or_incomplete_rows(self) -> None:
        from scripts.migration.catalog_v2_source_refs import LedgerError, validate_rows

        base = {
            "skill_id": "example/skill",
            "repository": "https://example.test/repository.git",
            "path": "skills/example",
            "current_requested_ref": "abc123",
            "proposed_ref": "",
            "decision": "omit",
            "reason": "The source follows its default branch.",
        }
        for mutation in (
            {"decision": "pin"},
            {"decision": "branch", "proposed_ref": ""},
            {"decision": "omit", "proposed_ref": "main"},
            {"reason": ""},
        ):
            row = {**base, **mutation}
            with self.subTest(mutation=mutation), self.assertRaises(LedgerError):
                validate_rows([row])

    def test_apply_is_atomic_and_requires_one_matching_row_per_external_git_entry(self) -> None:
        from scripts.migration.catalog_v2_source_refs import LedgerError, apply_ledger, generate_rows, write_ledger

        with TemporaryDirectory() as temporary:
            root = create_legacy_repository(Path(temporary))
            before = {path.relative_to(root): path.read_bytes() for path in (root / "entries").rglob("*.yaml")}
            rows = generate_rows(root)
            ledger = root / "decisions.csv"
            write_ledger(ledger, rows[:-1])

            with self.assertRaisesRegex(LedgerError, "one row per external Git Entry"):
                apply_ledger(root, ledger)

            after = {path.relative_to(root): path.read_bytes() for path in (root / "entries").rglob("*.yaml")}
            self.assertEqual(after, before)

    def test_apply_uses_ref_only_for_approved_branch_tag_or_commit_decisions(self) -> None:
        from hwskill.directory.yaml_io import load_yaml
        from scripts.migration.catalog_v2_source_refs import apply_ledger, generate_rows, write_ledger

        with TemporaryDirectory() as temporary:
            root = create_legacy_repository(Path(temporary))
            rows = generate_rows(root)
            rows[0]["decision"] = "branch"
            rows[0]["proposed_ref"] = "main"
            rows[0]["reason"] = "The skill intentionally tracks a non-default branch."
            ledger = root / "decisions.csv"
            write_ledger(ledger, rows)

            apply_ledger(root, ledger)

            migrated = {
                entry["id"]: entry
                for path in (root / "entries").rglob("*.yaml")
                for entry in [load_yaml(path)]
            }
            self.assertTrue(all(entry["schema_version"] == 2 for entry in migrated.values()))
            selected = migrated[rows[0]["skill_id"]]["source"]["locator"]
            self.assertEqual(selected["ref"], "main")
            for row in rows[1:]:
                locator = migrated[row["skill_id"]]["source"]["locator"]
                self.assertNotIn("ref", locator)
                self.assertNotIn("requested_ref", locator)
            all_text = "\n".join(
                path.read_text(encoding="utf-8") for path in (root / "entries").rglob("*.yaml")
            )
            for row in rows:
                self.assertNotIn(row["current_requested_ref"], all_text)
            self.assertNotIn("安装和行为未运行", all_text)
            self.assertNotIn("安装、行为和目标硬件效果均未运行", all_text)
            self.assertNotIn("未在本目录构建中执行联网获取或行为验证", all_text)
            spark = migrated["data-engineering/spark-and-distributed-processing"]
            self.assertEqual(
                spark["limitations"],
                ["官方安装器默认整仓；本平台只提供单目录安装提示且不执行整包脚本"],
            )


if __name__ == "__main__":
    unittest.main()
