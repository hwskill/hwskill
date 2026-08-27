from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import yaml

from . import __version__
from .importer import import_source
from .models import SourceSpec
from .registry import validate_registry, write_catalog


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hwskill")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    registry = commands.add_parser("registry")
    registry_commands = registry.add_subparsers(dest="registry_command")
    import_parser = registry_commands.add_parser("import")
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--repo-root", default=".")
    import_parser.add_argument("--update", action="store_true")
    validate_parser = registry_commands.add_parser("validate")
    validate_parser.add_argument("--repo-root", default=".")
    build_parser = registry_commands.add_parser("build")
    build_parser.add_argument("--repo-root", default=".")
    build_parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"hwskill {__version__}")
    elif args.command == "registry" and args.registry_command == "import":
        source_data = yaml.safe_load(Path(args.source).read_text(encoding="utf-8"))
        records = import_source(
            SourceSpec.from_mapping(source_data),
            Path(args.repo_root).resolve(),
            update=args.update,
        )
        print("ID\tREVISION\tDIGEST")
        for record in records:
            print(f"{record.skill_id}\t{record.revision}\t{record.content_digest}")
    elif args.command == "registry" and args.registry_command == "validate":
        records = validate_registry(Path(args.repo_root).resolve())
        print("ID\tREVISION\tDIGEST")
        for record in records:
            print(f"{record.skill_id}\t{record.revision}\t{record.content_digest}")
    elif args.command == "registry" and args.registry_command == "build":
        valid = write_catalog(Path(args.repo_root).resolve(), check=args.check)
        if args.check and not valid:
            return 1
    return 0
