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
from .audit import AuditWriter
from .configuration import (
    setup_claude_code,
    setup_codex,
    setup_opencode,
    unsetup_claude_code,
    unsetup_codex,
    unsetup_opencode,
)
from .claude_code_adapter import run_session_start as run_claude_session_start
from .codex_adapter import run_session_start as run_codex_session_start
from .doctor import run_doctor
from .importer import import_source
from .info import collect_info, format_info_summary
from .loader import load_skill
from .models import SourceSpec
from .profiles import (
    bind_profile,
    list_profiles,
    parse_csv,
    read_profile_ids,
    resolve_profiles,
    set_profiles,
    unbind_profile,
    unset_profiles,
)
from .projects import find_project
from .registry import validate_registry, write_catalog
from .search import search_skills
from .skill_export import (
    export_skills,
    resolve_skill_selectors,
    skills_for_profiles,
)
from .mcp_server import run_server
from .opencode_adapter import render_catalog as render_opencode_catalog
from .paths import resolve_repo_root
from .scopes import ScopeTarget, project_scope, user_scope


HOST_CHOICES = ("codex", "claude-code", "claude_code", "opencode")


def _commands(parser: argparse.ArgumentParser, dest: str):
    return parser.add_subparsers(dest=dest, metavar="<COMMAND>")


def _command(subparsers, name: str, description: str) -> argparse.ArgumentParser:
    return subparsers.add_parser(name, help=description, description=description)


def _add_host_argument(parser: argparse.ArgumentParser) -> None:
    choices = ", ".join(HOST_CHOICES)
    parser.add_argument(
        "host",
        choices=HOST_CHOICES,
        metavar="<HOST>",
        help=f"One of: {choices}",
    )


def _host(value: str) -> str:
    return value.replace("_", "-")


def default_audit_path() -> Path:
    configured = os.environ.get("HWSKILL_AUDIT_PATH")
    return Path(configured) if configured else Path.home() / ".hwskills/logs/audit.jsonl"


def _repo_root(value: str | None) -> Path:
    try:
        return resolve_repo_root(value)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _repo_root_source(value: str | None) -> str:
    if value is not None:
        return "--repo-root"
    if os.environ.get("HWSKILL_HOME"):
        return "HWSKILL_HOME"
    return "launcher"


def _project_path(
    value: str | None,
    *,
    write: bool,
    yes: bool = False,
    announce: bool = True,
) -> Path:
    explicit = value is not None
    project = Path(value).resolve() if explicit else find_project(Path.cwd())
    if not explicit and announce:
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


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", action="store_true", help="Use current-user scope")
    group.add_argument(
        "--project",
        nargs="?",
        const="",
        metavar="PROJECT",
        help="Use project scope, discovering the project when the path is omitted",
    )


def _scope_target(args: argparse.Namespace, *, write: bool) -> ScopeTarget:
    if args.user:
        target = user_scope()
    else:
        target = project_scope(args.project or None, Path.cwd())
        if args.project == "":
            print(f"PROJECT\t{target.project_root}", file=sys.stderr)
    if not write:
        return target
    if args.yes:
        return target
    if not sys.stdin.isatty():
        raise SystemExit("non-interactive writes require --yes")
    label = "current user" if target.kind == "user" else str(target.project_root)
    response = input(f"Apply hwskill profile changes to {label}? [y/N] ")
    if response.strip().lower() not in {"y", "yes"}:
        raise SystemExit("cancelled")
    return target


def _profile_show_data(target: ScopeTarget, registry_root: Path) -> dict[str, object]:
    explicit = read_profile_ids(target)
    if target.kind == "project":
        assert target.project_root is not None
        catalog = resolve_profiles(target.project_root, registry_root)
    else:
        catalog = resolve_profiles(target.config_root, registry_root, user_target=target)
    return {
        "scope": target.kind,
        "target": str(target.project_root or target.config_root),
        "configured": explicit is not None,
        "explicit_profiles": list(explicit or ()),
        "effective_scope": catalog.effective_scope,
        "profiles": list(catalog.profile_ids),
        "profile_source": str(catalog.profile_source) if catalog.profile_source else None,
        "catalog_digest": catalog.catalog_digest,
        "skills": list(catalog.skill_ids),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hwskill")
    parser.add_argument("--version", action="store_true", help="show the hwskill version")
    commands = _commands(parser, "command")
    info_parser = _command(commands, "info", "Show installation and integration status")
    info_parser.add_argument("--project")
    info_parser.add_argument("--repo-root")
    info_parser.add_argument("--json", action="store_true")
    registry = _command(commands, "registry", "Manage the skill registry")
    registry_commands = _commands(registry, "registry_command")
    import_parser = _command(
        registry_commands, "import", "Import skills from a source manifest"
    )
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--repo-root")
    import_parser.add_argument("--update", action="store_true")
    validate_parser = _command(registry_commands, "validate", "Validate registry contents")
    validate_parser.add_argument("--repo-root")
    build_parser = _command(registry_commands, "build", "Build the registry catalog")
    build_parser.add_argument("--repo-root")
    build_parser.add_argument("--check", action="store_true")
    profile = _command(commands, "profile", "Manage user and project profiles")
    profile_commands = _commands(profile, "profile_command")
    bind_parser = _command(profile_commands, "bind", "Bind a profile to a project")
    bind_parser.add_argument(
        "profile_id", metavar="<PROFILE_ID>", help="Profile ID to bind (e.g. codex-demo)"
    )
    bind_parser.add_argument("--project")
    bind_parser.add_argument("--repo-root")
    bind_parser.add_argument("--yes", action="store_true")
    unbind_parser = _command(profile_commands, "unbind", "Unbind a profile from a project")
    unbind_parser.add_argument(
        "profile_id", metavar="<PROFILE_ID>", help="Profile ID to unbind (e.g. codex-demo)"
    )
    unbind_parser.add_argument("--project")
    unbind_parser.add_argument("--repo-root")
    unbind_parser.add_argument("--yes", action="store_true")
    list_parser = _command(
        profile_commands, "list", "List available profile definitions"
    )
    list_parser.add_argument("--repo-root")
    list_parser.add_argument("--json", action="store_true")
    set_parser = _command(
        profile_commands, "set", "Set profiles for a user or project"
    )
    set_parser.add_argument(
        "profiles",
        nargs="?",
        metavar="<PROFILE_NAME,PROFILE_NAME,...>",
        help="e.g. personal-baseline,superpowers",
    )
    set_parser.add_argument(
        "--empty",
        action="store_true",
        help="Set an explicit empty profile collection",
    )
    _add_scope_arguments(set_parser)
    set_parser.add_argument("--repo-root")
    set_parser.add_argument("--yes", action="store_true")
    show_parser = _command(
        profile_commands, "show", "Show explicit and effective profiles"
    )
    _add_scope_arguments(show_parser)
    show_parser.add_argument("--repo-root")
    show_parser.add_argument("--json", action="store_true")
    unset_parser = _command(
        profile_commands, "unset", "Remove an explicit profile setting"
    )
    _add_scope_arguments(unset_parser)
    unset_parser.add_argument("--yes", action="store_true")
    resolve_parser = _command(
        profile_commands, "resolve", "Resolve the effective skill catalog"
    )
    resolve_parser.add_argument("--project")
    resolve_parser.add_argument("--repo-root")
    resolve_parser.add_argument("--json", action="store_true")
    skill = _command(commands, "skill", "Search and load effective skills")
    skill_commands = _commands(skill, "skill_command")
    skill_list_parser = _command(skill_commands, "list", "List all registered skills")
    skill_list_parser.add_argument("--repo-root")
    skill_list_parser.add_argument("--json", action="store_true")
    dump_parser = _command(skill_commands, "dump", "Export selected skills")
    dump_parser.add_argument(
        "selectors",
        metavar="<SKILL_ID,SKILL_NAME,...>",
        help="e.g. chinese-thinking,brainstorming",
    )
    dump_parser.add_argument(
        "destination", metavar="<SKILLS_DIR>", help="Destination skill directory"
    )
    dump_parser.add_argument("--repo-root")
    dump_parser.add_argument("--json", action="store_true")
    dump_profile_parser = _command(
        skill_commands, "dump-profile", "Export skills from profiles"
    )
    dump_profile_parser.add_argument(
        "profiles",
        metavar="<PROFILE_NAME,PROFILE_NAME,...>",
        help="e.g. personal-baseline,superpowers",
    )
    dump_profile_parser.add_argument(
        "destination", metavar="<SKILLS_DIR>", help="Destination skill directory"
    )
    dump_profile_parser.add_argument("--repo-root")
    dump_profile_parser.add_argument("--json", action="store_true")
    search_parser = _command(skill_commands, "search", "Search the effective skill catalog")
    search_parser.add_argument(
        "query", metavar="<QUERY>", help='Search terms (e.g. "debug failing test")'
    )
    search_parser.add_argument("--project")
    search_parser.add_argument("--repo-root")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--json", action="store_true")
    load_parser = _command(skill_commands, "load", "Load a skill from the effective catalog")
    load_parser.add_argument(
        "skill_id",
        metavar="<SKILL_ID>",
        help="Skill ID to load (e.g. local/chinese-thinking)",
    )
    load_parser.add_argument("--project")
    load_parser.add_argument("--repo-root")
    load_parser.add_argument("--expected-digest")
    load_parser.add_argument("--raw", action="store_true")
    load_parser.add_argument("--json", action="store_true")
    setup_parser = _command(commands, "setup", "Configure a host integration")
    _add_host_argument(setup_parser)
    setup_parser.add_argument("--project")
    setup_parser.add_argument("--repo-root")
    setup_parser.add_argument("--audit-path")
    setup_parser.add_argument("--yes", action="store_true")
    unsetup_parser = _command(commands, "unsetup", "Remove a host integration")
    _add_host_argument(unsetup_parser)
    unsetup_parser.add_argument("--project")
    unsetup_parser.add_argument("--yes", action="store_true")
    doctor_parser = _command(commands, "doctor", "Check a host integration")
    _add_host_argument(doctor_parser)
    doctor_parser.add_argument("--project")
    doctor_parser.add_argument("--repo-root")
    doctor_parser.add_argument("--json", action="store_true")
    adapter_parser = _command(commands, "adapter", "Run host adapter commands")
    adapter_commands = _commands(adapter_parser, "adapter_host")
    codex_parser = _command(adapter_commands, "codex", "Run Codex adapter commands")
    codex_commands = _commands(codex_parser, "adapter_command")
    session_parser = _command(
        codex_commands, "session-start", "Render Codex session-start context"
    )
    session_parser.add_argument("--repo-root")
    session_parser.add_argument("--audit-path")
    session_parser.add_argument("--project")
    for claude_host in ("claude-code", "claude_code"):
        description = (
            "Run Claude Code adapter commands"
            if claude_host == "claude-code"
            else "Alias for claude-code adapter commands"
        )
        claude_parser = _command(adapter_commands, claude_host, description)
        claude_commands = _commands(claude_parser, "adapter_command")
        claude_session = _command(
            claude_commands,
            "session-start",
            "Render Claude Code session-start context",
        )
        claude_session.add_argument("--repo-root")
        claude_session.add_argument("--audit-path")
        claude_session.add_argument("--project")
    opencode_parser = _command(adapter_commands, "opencode", "Run OpenCode adapter commands")
    opencode_commands = _commands(opencode_parser, "adapter_command")
    catalog_parser = _command(
        opencode_commands, "catalog", "Render the OpenCode skill catalog"
    )
    catalog_parser.add_argument("--project")
    catalog_parser.add_argument("--repo-root")
    catalog_parser.add_argument("--audit-path")
    mcp_parser = _command(commands, "serve-mcp", "Start the hwskill MCP server")
    mcp_parser.add_argument("--repo-root")
    mcp_parser.add_argument("--project", default=".")
    mcp_parser.add_argument("--audit-path")
    args = parser.parse_args(argv)
    if args.version:
        print(f"hwskill {__version__}")
    elif args.command == "info":
        info = collect_info(
            _repo_root(args.repo_root),
            _project_path(args.project, write=False, announce=False),
            repository_source=_repo_root_source(args.repo_root),
        )
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(format_info_summary(info), end="")
    elif args.command == "registry" and args.registry_command == "import":
        source_data = yaml.safe_load(Path(args.source).read_text(encoding="utf-8"))
        records = import_source(
            SourceSpec.from_mapping(source_data),
            _repo_root(args.repo_root),
            update=args.update,
        )
        print("ID\tREVISION\tDIGEST")
        for record in records:
            print(f"{record.skill_id}\t{record.revision}\t{record.content_digest}")
    elif args.command == "registry" and args.registry_command == "validate":
        records = validate_registry(_repo_root(args.repo_root))
        print("ID\tREVISION\tDIGEST")
        for record in records:
            print(f"{record.skill_id}\t{record.revision}\t{record.content_digest}")
    elif args.command == "registry" and args.registry_command == "build":
        valid = write_catalog(_repo_root(args.repo_root), check=args.check)
        if args.check and not valid:
            return 1
    elif args.command == "profile" and args.profile_command == "set":
        if args.empty == (args.profiles is not None):
            set_parser.error("provide either PROFILES or --empty")
        profile_ids = () if args.empty else parse_csv(args.profiles, "profile")
        target = _scope_target(args, write=True)
        catalog = set_profiles(target, _repo_root(args.repo_root), profile_ids)
        print(f"SCOPE\t{target.kind}")
        print(f"PROFILES\t{','.join(catalog.profile_ids) or 'none'}")
        print(f"CATALOG_DIGEST\t{catalog.catalog_digest}")
    elif args.command == "profile" and args.profile_command == "show":
        target = _scope_target(args, write=False)
        data = _profile_show_data(target, _repo_root(args.repo_root))
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"SCOPE\t{data['scope']}")
            print(
                "EXPLICIT_PROFILES\t"
                + (",".join(data["explicit_profiles"]) or "none")
            )
            print(f"EFFECTIVE_SCOPE\t{data['effective_scope'] or 'none'}")
            print("PROFILES\t" + (",".join(data["profiles"]) or "none"))
            print(f"CATALOG_DIGEST\t{data['catalog_digest']}")
    elif args.command == "profile" and args.profile_command == "list":
        definitions = list_profiles(_repo_root(args.repo_root))
        if args.json:
            print(
                json.dumps(
                    {"profiles": [asdict(item) for item in definitions]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("PROFILE\tDESCRIPTION\tSKILLS")
            for item in definitions:
                print(
                    f"{item.profile_id}\t{item.description}\t"
                    f"{','.join(item.skill_ids)}"
                )
    elif args.command == "profile" and args.profile_command == "unset":
        target = _scope_target(args, write=True)
        changed = unset_profiles(target)
        print(f"SCOPE\t{target.kind}")
        print(f"STATUS\t{'REMOVED' if changed else 'UNCHANGED'}")
    elif args.command == "profile" and args.profile_command == "bind":
        print("warning: profile bind is deprecated; use profile set", file=sys.stderr)
        project_path = _project_path(args.project, write=True, yes=args.yes)
        catalog = bind_profile(project_path, _repo_root(args.repo_root), args.profile_id)
        print(f"BOUND\t{args.profile_id}\t{catalog.catalog_digest}")
    elif args.command == "profile" and args.profile_command == "resolve":
        catalog = resolve_profiles(_project_path(args.project, write=False), _repo_root(args.repo_root))
        data = {"project": str(catalog.project), "profile_ids": list(catalog.profile_ids),
                "catalog_digest": catalog.catalog_digest,
                "skills": [asdict(item) | {"path": str(item.path)} for item in catalog.skills]}
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else "ID\tREVISION\tDIGEST")
        if not args.json:
            for item in catalog.skills:
                print(f"{item.skill_id}\t{item.revision}\t{item.content_digest}")
    elif args.command == "profile" and args.profile_command == "unbind":
        print("warning: profile unbind is deprecated; use profile unset", file=sys.stderr)
        project_path = _project_path(args.project, write=True, yes=args.yes)
        unbind_profile(project_path, _repo_root(args.repo_root), args.profile_id)
        print(f"UNBOUND\t{args.profile_id}")
    elif args.command == "skill" and args.skill_command == "list":
        records = validate_registry(_repo_root(args.repo_root))
        if args.json:
            print(
                json.dumps(
                    {
                        "skills": [
                            {
                                "id": item.skill_id,
                                "name": item.name,
                                "description": item.description,
                                "layer": item.layer,
                                "source_id": item.source_id,
                                "revision": item.revision,
                                "license": item.license,
                                "content_digest": item.content_digest,
                                "path": str(item.path),
                            }
                            for item in records
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("ID\tNAME\tLAYER\tREVISION")
            for item in records:
                print(f"{item.skill_id}\t{item.name}\t{item.layer}\t{item.revision}")
    elif args.command == "skill" and args.skill_command in {"dump", "dump-profile"}:
        registry_root = _repo_root(args.repo_root)
        records = validate_registry(registry_root)
        selected = (
            resolve_skill_selectors(args.selectors, records)
            if args.skill_command == "dump"
            else skills_for_profiles(args.profiles, registry_root, records)
        )
        results = export_skills(selected, Path(args.destination).expanduser())
        if args.json:
            print(
                json.dumps(
                    {
                        "results": [
                            asdict(item) | {"target": str(item.target)}
                            for item in results
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("SKILL\tTARGET\tSTATUS")
            for item in results:
                print(f"{item.skill_id}\t{item.target}\t{item.status}")
    elif args.command == "skill" and args.skill_command == "search":
        catalog = resolve_profiles(_project_path(args.project, write=False), _repo_root(args.repo_root))
        results = search_skills(catalog, args.query, args.limit)
        if args.json:
            print(json.dumps({"catalog_digest": catalog.catalog_digest,
                              "results": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
        else:
            print("ID\tSCORE\tDESCRIPTION")
            for item in results:
                print(f"{item.skill_id}\t{item.score}\t{item.description}")
    elif args.command == "skill" and args.skill_command == "load":
        catalog = resolve_profiles(_project_path(args.project, write=False), _repo_root(args.repo_root))
        loaded = load_skill(catalog, args.skill_id, args.expected_digest, args.raw)
        print(json.dumps(asdict(loaded), ensure_ascii=False, indent=2) if args.json else loaded.content, end="\n")
    elif args.command == "setup":
        project_path = _project_path(args.project, write=True, yes=args.yes)
        audit_path = Path(args.audit_path) if args.audit_path else None
        setup = {
            "codex": setup_codex,
            "claude-code": setup_claude_code,
            "opencode": setup_opencode,
        }[_host(args.host)]
        result = setup(project_path, _repo_root(args.repo_root), audit_path)
        print(f"CONFIG\t{result.config_path}\t{'UPDATED' if result.changed else 'UNCHANGED'}")
    elif args.command == "unsetup":
        project_path = _project_path(args.project, write=True, yes=args.yes)
        unsetup = {
            "codex": unsetup_codex,
            "claude-code": unsetup_claude_code,
            "opencode": unsetup_opencode,
        }[_host(args.host)]
        result = unsetup(project_path)
        print(f"CONFIG\t{result.config_path}\t{'UPDATED' if result.changed else 'UNCHANGED'}")
    elif args.command == "doctor":
        checks = run_doctor(
            _host(args.host), _project_path(args.project, write=False),
            _repo_root(args.repo_root),
        )
        if args.json:
            print(json.dumps({"checks": [asdict(item) for item in checks]}, ensure_ascii=False, indent=2))
        else:
            print("CHECK\tSTATUS\tDETAIL")
            for item in checks:
                print(f"{item.name}\t{item.status}\t{item.detail}")
    elif args.command == "adapter" and _host(args.adapter_host) == "codex" and args.adapter_command == "session-start":
        audit_path = Path(args.audit_path) if args.audit_path else default_audit_path()
        print(run_codex_session_start(_repo_root(args.repo_root), audit_path, sys.stdin.read()))
    elif args.command == "adapter" and _host(args.adapter_host) == "claude-code" and args.adapter_command == "session-start":
        audit_path = Path(args.audit_path) if args.audit_path else default_audit_path()
        print(run_claude_session_start(_repo_root(args.repo_root), audit_path, sys.stdin.read()))
    elif args.command == "adapter" and args.adapter_host == "opencode" and args.adapter_command == "catalog":
        audit_path = Path(args.audit_path) if args.audit_path else default_audit_path()
        print(render_opencode_catalog(
            _project_path(args.project, write=False), _repo_root(args.repo_root),
            AuditWriter(audit_path),
        ))
    elif args.command == "serve-mcp":
        audit_path = Path(args.audit_path) if args.audit_path else default_audit_path()
        run_server(Path(args.project).resolve(), _repo_root(args.repo_root), audit_path)
    return 0
