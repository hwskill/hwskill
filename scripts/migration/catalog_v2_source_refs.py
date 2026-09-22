#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Iterable, Mapping
from urllib.parse import quote

from hwskill.directory.yaml_io import load_yaml


COLUMNS = (
    "skill_id",
    "repository",
    "path",
    "current_requested_ref",
    "proposed_ref",
    "decision",
    "reason",
)
DECISIONS = {"omit", "branch", "tag", "commit"}
DEFAULT_REASON = (
    "The existing commit recorded an earlier catalog snapshot; the skill itself can follow "
    "the upstream default branch."
)


class LedgerError(ValueError):
    pass


def _external_git_entries(repo_root: Path) -> list[tuple[Path, dict]]:
    result: list[tuple[Path, dict]] = []
    for path in sorted((repo_root / "entries").rglob("*.yaml")):
        entry = load_yaml(path)
        source = entry.get("source", {})
        locator = source.get("locator", {}) if isinstance(source, Mapping) else {}
        if source.get("kind") == "external" and locator.get("type") == "git":
            result.append((path, entry))
    return result


def generate_rows(repo_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for _path, entry in _external_git_entries(Path(repo_root)):
        locator = entry["source"]["locator"]
        current_ref = locator.get("requested_ref", locator.get("ref", ""))
        rows.append(
            {
                "skill_id": entry["id"],
                "repository": locator["repository"],
                "path": locator["path"],
                "current_requested_ref": current_ref,
                "proposed_ref": "",
                "decision": "omit",
                "reason": DEFAULT_REASON,
            }
        )
    return sorted(rows, key=lambda row: row["skill_id"])


def validate_rows(rows: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    checked: list[dict[str, str]] = []
    seen: set[str] = set()
    for number, raw in enumerate(rows, start=2):
        if tuple(raw) != COLUMNS:
            raise LedgerError(f"row {number} must contain exactly {','.join(COLUMNS)}")
        row = {key: str(raw[key]) for key in COLUMNS}
        if not all(row[key].strip() for key in ("skill_id", "repository", "path", "current_requested_ref", "reason")):
            raise LedgerError(f"row {number} has an empty required field")
        if row["skill_id"] in seen:
            raise LedgerError(f"row {number} duplicates skill_id {row['skill_id']}")
        seen.add(row["skill_id"])
        if row["decision"] not in DECISIONS:
            raise LedgerError(f"row {number} has unsupported decision {row['decision']!r}")
        if row["decision"] == "omit" and row["proposed_ref"]:
            raise LedgerError(f"row {number} omit decision requires an empty proposed_ref")
        if row["decision"] != "omit" and not row["proposed_ref"].strip():
            raise LedgerError(f"row {number} {row['decision']} decision requires proposed_ref")
        checked.append(row)
    return checked


def write_ledger(path: Path, rows: Iterable[Mapping[str, str]]) -> None:
    checked = validate_rows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(checked)


def read_ledger(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise LedgerError(f"ledger must use exactly these columns: {','.join(COLUMNS)}")
        return validate_rows(reader)


def _v2_entry_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text, count = re.subn(r"(?m)^schema_version:\s*1\s*$", "schema_version: 2", text, count=1)
    if count != 1 and not re.search(r"(?m)^schema_version:\s*2\s*$", text):
        raise LedgerError(f"{path} has no supported schema_version line")
    text = re.sub(r"(?m)^(?:lifecycle_reason|replacement_id):\s*null\s*\n", "", text)
    text = text.replace(
        "- 外部正文未复制；来源与元数据于 2026-09-20 核对，安装和行为未运行。\n",
        "",
    )
    text = re.sub(
        r"(?m)^limitations: \[未在本目录构建中执行联网获取或行为验证\]\s*\n",
        "",
        text,
    )
    text = re.sub(
        r"(?m)^limitations: \[external 正文未复制；安装、行为和目标硬件效果均未运行\]\s*\n",
        "",
        text,
    )
    text = text.replace(
        "limitations: [官方安装器默认整仓；本平台只提供单目录安装提示且不执行整包脚本；安装和行为未运行]",
        "limitations: [官方安装器默认整仓；本平台只提供单目录安装提示且不执行整包脚本]",
    )
    return text


def _migrated_text(path: Path, row: Mapping[str, str]) -> str:
    text = _v2_entry_text(path)
    pattern = r"(?m)^(\s{4})(?:requested_ref|ref):[^\n]*(?:\n|$)"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise LedgerError(f"{path} must contain exactly one current source ref")
    if row["decision"] == "omit":
        text = re.sub(pattern, "", text, count=1)
        target_ref = "HEAD"
    else:
        encoded = json.dumps(row["proposed_ref"], ensure_ascii=False)
        text = re.sub(pattern, rf"\1ref: {encoded}\n", text, count=1)
        target_ref = quote(row["proposed_ref"], safe="")
    text = text.replace(
        f"/blob/{row['current_requested_ref']}/",
        f"/blob/{target_ref}/",
    )
    return text


def apply_ledger(repo_root: Path, ledger_path: Path) -> None:
    root = Path(repo_root)
    entries = _external_git_entries(root)
    rows = read_ledger(Path(ledger_path))
    rows_by_id = {row["skill_id"]: row for row in rows}
    entry_ids = {entry["id"] for _path, entry in entries}
    if set(rows_by_id) != entry_ids:
        missing = sorted(entry_ids - set(rows_by_id))
        extra = sorted(set(rows_by_id) - entry_ids)
        raise LedgerError(
            "ledger must contain one row per external Git Entry "
            f"(missing={missing}, extra={extra})"
        )
    pending_by_path: dict[Path, str] = {}
    for path, entry in entries:
        row = rows_by_id[entry["id"]]
        locator = entry["source"]["locator"]
        if row["repository"] != locator["repository"] or row["path"] != locator["path"]:
            raise LedgerError(f"ledger locator does not match {entry['id']}")
        current_ref = locator.get("requested_ref", locator.get("ref", ""))
        if row["current_requested_ref"] != current_ref:
            raise LedgerError(f"ledger current_requested_ref is stale for {entry['id']}")
        pending_by_path[path] = _migrated_text(path, row)
    for path in sorted((root / "entries").rglob("*.yaml")):
        pending_by_path.setdefault(path, _v2_entry_text(path))
    for path, text in sorted(pending_by_path.items()):
        temporary = path.with_name(f".{path.name}.catalog-v2.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and apply the reviewed Catalog v2 source ref ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--repo-root", type=Path, default=Path("."))
    generate.add_argument("--output", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--repo-root", type=Path, default=Path("."))
    apply.add_argument("--ledger", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "generate":
        write_ledger(args.output, generate_rows(args.repo_root))
    else:
        apply_ledger(args.repo_root, args.ledger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
