from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict
import json
import os
import sys
from pathlib import Path

import yaml

from . import __version__
from .configuration import setup_codex, unsetup_codex
from .codex_adapter import run_session_start
from .doctor import run_doctor
from .importer import import_source
from .loader import load_skill
from .models import SourceSpec
from .profiles import bind_profile, resolve_profile_ids, resolve_profiles, unbind_profile
from .projects import find_project
from .registry import validate_registry, write_catalog
from .search import search_skills
from .mcp_server import run_server


def default_audit_path() -> Path:
    configured = os.environ.get("HWSKILL_AUDIT_PATH")
    return Path(configured) if configured else Path.home() / ".hwskills/logs/audit.jsonl"


def _project_path(value: str | None, *, write: bool, yes: bool = False) -> Path:
    explicit = value is not None
    project = Path(value).resolve() if explicit else find_project(Path.cwd())
    if not explicit:
        print(f"PROJECT\t{project}", file=sys.stderr)
    if not write:
        return project
    if not sys.stdin.isatty():
        if not explicit or not yes:
            raise SystemExit("non-interactive writes require explicit --project and --yes")
        return project
    if yes:
        return project
    response = input(f"Apply hwskill changes to {project}? [y/N] ")
    if response.strip().lower() not in {"y", "yes"}:
        raise SystemExit("cancelled")
    return project


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
    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command")
    bind_parser = profile_commands.add_parser("bind")
    bind_parser.add_argument("profile_id")
    bind_parser.add_argument("--project")
    bind_parser.add_argument("--repo-root", default=".")
    bind_parser.add_argument("--yes", action="store_true")
    unbind_parser = profile_commands.add_parser("unbind")
    unbind_parser.add_argument("profile_id")
    unbind_parser.add_argument("--project")
    unbind_parser.add_argument("--repo-root", default=".")
    unbind_parser.add_argument("--yes", action="store_true")
    list_parser = profile_commands.add_parser("list")
    list_parser.add_argument("--project")
    list_parser.add_argument("--json", action="store_true")
    resolve_parser = profile_commands.add_parser("resolve")
    resolve_parser.add_argument("--project")
    resolve_parser.add_argument("--repo-root", default=".")
    resolve_parser.add_argument("--json", action="store_true")
    skill = commands.add_parser("skill")
    skill_commands = skill.add_subparsers(dest="skill_command")
    search_parser = skill_commands.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--project")
    search_parser.add_argument("--repo-root", default=".")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--json", action="store_true")
    load_parser = skill_commands.add_parser("load")
    load_parser.add_argument("skill_id")
    load_parser.add_argument("--project")
    load_parser.add_argument("--repo-root", default=".")
    load_parser.add_argument("--expected-digest")
    load_parser.add_argument("--raw", action="store_true")
    load_parser.add_argument("--json", action="store_true")
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("host", choices=("codex",))
    setup_parser.add_argument("--project")
    setup_parser.add_argument("--repo-root", default=".")
    setup_parser.add_argument("--audit-path")
    setup_parser.add_argument("--yes", action="store_true")
    unsetup_parser = commands.add_parser("unsetup")
    unsetup_parser.add_argument("host", choices=("codex",))
    unsetup_parser.add_argument("--project")
    unsetup_parser.add_argument("--yes", action="store_true")
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("host", choices=("codex",))
    doctor_parser.add_argument("--project")
    doctor_parser.add_argument("--repo-root", default=".")
    doctor_parser.add_argument("--json", action="store_true")
    adapter_parser = commands.add_parser("adapter")
    adapter_commands = adapter_parser.add_subparsers(dest="adapter_host")
    codex_parser = adapter_commands.add_parser("codex")
    codex_commands = codex_parser.add_subparsers(dest="adapter_command")
    session_parser = codex_commands.add_parser("session-start")
    session_parser.add_argument("--repo-root", default=".")
    session_parser.add_argument("--audit-path")
    mcp_parser = commands.add_parser("serve-mcp")
    mcp_parser.add_argument("--repo-root", default=".")
    mcp_parser.add_argument("--project", default=".")
    mcp_parser.add_argument("--audit-path")
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
    elif args.command == "profile" and args.profile_command == "bind":
        project_path = _project_path(args.project, write=True, yes=args.yes)
        catalog = bind_profile(project_path, Path(args.repo_root).resolve(), args.profile_id)
        print(f"BOUND\t{args.profile_id}\t{catalog.catalog_digest}")
    elif args.command == "profile" and args.profile_command == "resolve":
        catalog = resolve_profiles(_project_path(args.project, write=False), Path(args.repo_root).resolve())
        data = {"project": str(catalog.project), "profile_ids": list(catalog.profile_ids),
                "catalog_digest": catalog.catalog_digest,
                "skills": [asdict(item) | {"path": str(item.path)} for item in catalog.skills]}
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else "ID\tREVISION\tDIGEST")
        if not args.json:
            for item in catalog.skills:
                print(f"{item.skill_id}\t{item.revision}\t{item.content_digest}")
    elif args.command == "profile" and args.profile_command == "list":
        profile_ids = resolve_profile_ids(_project_path(args.project, write=False))
        if args.json:
            print(json.dumps({"profiles": profile_ids}, ensure_ascii=False, indent=2))
        else:
            print("PROFILE")
            for profile_id in profile_ids:
                print(profile_id)
    elif args.command == "profile" and args.profile_command == "unbind":
        project_path = _project_path(args.project, write=True, yes=args.yes)
        unbind_profile(project_path, Path(args.repo_root).resolve(), args.profile_id)
        print(f"UNBOUND\t{args.profile_id}")
    elif args.command == "skill" and args.skill_command == "search":
        catalog = resolve_profiles(_project_path(args.project, write=False), Path(args.repo_root).resolve())
        results = search_skills(catalog, args.query, args.limit)
        if args.json:
            print(json.dumps({"catalog_digest": catalog.catalog_digest,
                              "results": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
        else:
            print("ID\tSCORE\tDESCRIPTION")
            for item in results:
                print(f"{item.skill_id}\t{item.score}\t{item.description}")
    elif args.command == "skill" and args.skill_command == "load":
        catalog = resolve_profiles(_project_path(args.project, write=False), Path(args.repo_root).resolve())
        loaded = load_skill(catalog, args.skill_id, args.expected_digest, args.raw)
        print(json.dumps(asdict(loaded), ensure_ascii=False, indent=2) if args.json else loaded.content, end="\n")
    elif args.command == "setup":
        project_path = _project_path(args.project, write=True, yes=args.yes)
        audit_path = Path(args.audit_path) if args.audit_path else None
        result = setup_codex(project_path, Path(args.repo_root).resolve(), audit_path)
        print(f"CONFIG\t{result.config_path}\t{'UPDATED' if result.changed else 'UNCHANGED'}")
    elif args.command == "unsetup":
        project_path = _project_path(args.project, write=True, yes=args.yes)
        result = unsetup_codex(project_path)
        print(f"CONFIG\t{result.config_path}\t{'UPDATED' if result.changed else 'UNCHANGED'}")
    elif args.command == "doctor":
        checks = run_doctor(_project_path(args.project, write=False), Path(args.repo_root).resolve())
        if args.json:
            print(json.dumps({"checks": [asdict(item) for item in checks]}, ensure_ascii=False, indent=2))
        else:
            print("CHECK\tSTATUS\tDETAIL")
            for item in checks:
                print(f"{item.name}\t{item.status}\t{item.detail}")
    elif args.command == "adapter" and args.adapter_command == "session-start":
        audit_path = Path(args.audit_path) if args.audit_path else default_audit_path()
        print(run_session_start(Path(args.repo_root).resolve(), audit_path, sys.stdin.read()))
    elif args.command == "serve-mcp":
        audit_path = Path(args.audit_path) if args.audit_path else default_audit_path()
        run_server(Path(args.project).resolve(), Path(args.repo_root).resolve(), audit_path)
    return 0
