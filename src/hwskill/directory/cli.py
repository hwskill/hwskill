from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .catalog import BuildInputError, build_repository
from .entries import validate_repository
from .migration import MigrationSafetyError, migration_preview


def _report_data(report):
    return {"schema_version": report.schema_version, "source_commit": report.source_commit, "input_digest": report.input_digest, "result": report.result, "issues": [asdict(issue) for issue in report.issues]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hwskill.directory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "build"):
        command = commands.add_parser(name)
        command.add_argument("--repo-root", required=True)
        command.add_argument("--json", action="store_true")
        if name == "build":
            command.add_argument("--out", required=True)
    migration = commands.add_parser("migration-preview")
    migration.add_argument("--repo-root", required=True)
    migration.add_argument("--out", required=True)
    migration.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root)
    if not root.is_dir():
        if args.json:
            print(json.dumps({"schema_version": 1, "result": "environment_error", "issues": []}, ensure_ascii=False))
        return 3
    try:
        if args.command == "migration-preview":
            try:
                preview = migration_preview(root, Path(args.out))
            except MigrationSafetyError as exc:
                if args.json:
                    print(json.dumps({"schema_version": 1, "result": "error", "message": str(exc)}, ensure_ascii=False, sort_keys=True))
                else:
                    print(str(exc))
                return 1
            data = {"result": "ok", **preview.to_dict()}
            print(json.dumps(data, ensure_ascii=False, sort_keys=True) if args.json else str(preview.out_dir))
            return 0
        report = validate_repository(root)
        if args.command == "validate":
            print(json.dumps(_report_data(report), ensure_ascii=False, sort_keys=True) if args.json else report.result)
            return 0 if report.result == "pass" else 1
        if report.result != "pass":
            print(json.dumps(_report_data(report), ensure_ascii=False, sort_keys=True) if args.json else report.result)
            return 1
        try:
            result = build_repository(root, Path(args.out))
        except BuildInputError as exc:
            data = _report_data(report)
            data["result"] = "fail"
            data["issues"] = [*data["issues"], asdict(exc.issue)]
            print(json.dumps(data, ensure_ascii=False, sort_keys=True) if args.json else exc.issue.message)
            return 1
        print(json.dumps(asdict(result), ensure_ascii=False, default=str, sort_keys=True) if args.json else str(result.output_dir))
        return 0
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"schema_version": 1, "result": "environment_error", "issues": [], "message": str(exc)}, ensure_ascii=False))
        return 3
