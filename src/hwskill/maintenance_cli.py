from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from .console_output import format_json, format_maintenance_plan, plan_data
from .git_source import GitSourceClient, GitSourceError, discover_skills
from .maintenance_transaction import TransactionConflictError, TransactionError
from .skill_maintenance import (
    SkillMaintenanceError, plan_adopt_skill, plan_create_manual, plan_delete_skill,
    plan_manualize_skill, plan_move_skill, plan_rename_skill, plan_update_manual,
)
from .source_manifest import SourceDefaults, load_all_sources
from .source_maintenance import (
    SourceAddRequest, SourceMaintenanceError, SourceSelection, UpdatePolicies,
    inspect_source, plan_add_source, plan_delete_source, plan_ignore_change, plan_update_sources,
)


def register_maintenance_commands(commands) -> None:
    source = commands.add_parser("source", help="Manage upstream skill sources", description="Manage upstream skill sources")
    source_commands = source.add_subparsers(dest="source_command", metavar="<COMMAND>")
    add = source_commands.add_parser("add", help="Add an upstream source")
    add.add_argument("--repo-root"); add.add_argument("--repository"); add.add_argument("--source-id")
    add.add_argument("--track"); add.add_argument("--skills-path"); add.add_argument("--namespace")
    add.add_argument("--layer", default="l1"); add.add_argument("--license", dest="license_name", default="MIT")
    add.add_argument("--include", action="append"); add.add_argument("--yes", action="store_true"); add.add_argument("--json", action="store_true")
    check = source_commands.add_parser("check", help="Check upstream source revisions")
    check.add_argument("source_id", nargs="?"); check.add_argument("--all", action="store_true"); check.add_argument("--repo-root"); check.add_argument("--json", action="store_true")
    update = source_commands.add_parser("update", help="Update upstream source snapshots")
    update.add_argument("source_id", nargs="?"); update.add_argument("--all", action="store_true"); update.add_argument("--repo-root")
    update.add_argument("--on-added", choices=("include", "ignore", "fail")); update.add_argument("--on-removed", choices=("remove", "manualize", "fail")); update.add_argument("--yes", action="store_true"); update.add_argument("--json", action="store_true")
    adopt = source_commands.add_parser("adopt", help="Adopt an upstream Skill")
    adopt.add_argument("source_id"); adopt.add_argument("skill_id"); adopt.add_argument("--path", required=True); adopt.add_argument("--replace", action="store_true"); adopt.add_argument("--repo-root"); adopt.add_argument("--yes", action="store_true"); adopt.add_argument("--json", action="store_true")
    ignore = source_commands.add_parser("ignore", help="Manage source ignores")
    ignored = ignore.add_subparsers(dest="ignore_command", metavar="<COMMAND>")
    for name, help_text in (("list", "List ignored upstream paths"), ("add", "Ignore an upstream path"), ("remove", "Stop ignoring an upstream path")):
        item = ignored.add_parser(name, help=help_text); item.add_argument("source_id");
        if name != "list": item.add_argument("path"); item.add_argument("--yes", action="store_true")
        item.add_argument("--repo-root"); item.add_argument("--json", action="store_true")
    delete = source_commands.add_parser("delete", help="Delete or manualize a source")
    delete.add_argument("source_id"); delete.add_argument("--skills", choices=("delete", "manualize")); delete.add_argument("--remove-from-profiles", action="store_true"); delete.add_argument("--repo-root"); delete.add_argument("--yes", action="store_true"); delete.add_argument("--json", action="store_true")

    skill = commands.choices["skill"]
    skill_commands = next(action for action in skill._actions if isinstance(action, argparse._SubParsersAction))
    create = skill_commands.add_parser("create", help="Create a manual Skill")
    create.add_argument("skill_id"); create.add_argument("--layer", required=True); create.add_argument("--description"); create.add_argument("--license", dest="license_name"); create.add_argument("--repo-root"); create.add_argument("--yes", action="store_true"); create.add_argument("--json", action="store_true")
    for name, help_text in (("update", "Update a manual Skill"), ("manualize", "Convert an upstream Skill to manual")):
        item = skill_commands.add_parser(name, help=help_text); item.add_argument("skill_id"); item.add_argument("--repo-root"); item.add_argument("--yes", action="store_true"); item.add_argument("--json", action="store_true")
    move = skill_commands.add_parser("move", help="Move a Skill between layers"); move.add_argument("skill_id"); move.add_argument("--layer", required=True); move.add_argument("--repo-root"); move.add_argument("--yes", action="store_true"); move.add_argument("--json", action="store_true")
    rename = skill_commands.add_parser("rename", help="Rename a Skill"); rename.add_argument("skill_id"); rename.add_argument("new_skill_id"); rename.add_argument("--repo-root"); rename.add_argument("--yes", action="store_true"); rename.add_argument("--json", action="store_true")
    delete_skill = skill_commands.add_parser("delete", help="Delete a Skill"); delete_skill.add_argument("skill_id"); delete_skill.add_argument("--remove-from-profiles", action="store_true"); delete_skill.add_argument("--repo-root"); delete_skill.add_argument("--yes", action="store_true"); delete_skill.add_argument("--json", action="store_true")


def _ask(prompt: str, default: str = "") -> str:
    result = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return result or default


def _source_id_from_repository(repository: str) -> str:
    name = repository.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return name.replace(" ", "-")


def _confirm(args) -> None:
    if args.yes: return
    if not sys.stdin.isatty() and getattr(args, "repository", None): raise SystemExit("non-interactive writes require --yes")
    if _ask("Apply these changes? [y/N]", "n").lower() not in {"y", "yes"}: raise SystemExit("cancelled")


def _emit(plan, args, stdout: TextIO) -> int:
    plan.apply()
    stdout.write(format_json(plan_data(plan)) if args.json else format_maintenance_plan(plan, color=stdout.isatty()))
    return 0


def _root(args, repo_root: Path) -> Path:
    return Path(args.repo_root).resolve() if getattr(args, "repo_root", None) else Path(repo_root)


def _wizard_add(args, root: Path, git: GitSourceClient):
    repository = args.repository or _ask("Git repository URL")
    refs = git.list_remote(repository)
    source_id = args.source_id or _source_id_from_repository(repository)
    known = {item.source_id for item in load_all_sources(root)}
    while source_id in known:
        source_id = _ask(f"Source ID '{source_id}' already exists; choose another")
    track = args.track or _ask("Track", refs.default_branch)
    skills_path = args.skills_path or _ask("Skills path", "skills")
    namespace = args.namespace or _ask("Namespace", source_id)
    request = SourceAddRequest(source_id, repository, track, skills_path, SourceDefaults(namespace, args.layer, args.license_name))
    if args.include:
        included = () if args.include == ["all"] else tuple(args.include)
        if args.include == ["all"]:
            # plan_add_source gets the discovered list; an empty marker is expanded below.
            import tempfile
            with tempfile.TemporaryDirectory() as temporary:
                checkout = Path(temporary); git.materialize(repository, track, checkout)
                included = tuple(item.path for item in discover_skills(checkout, skills_path))
    else:
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary); git.materialize(repository, track, checkout)
            found = discover_skills(checkout, skills_path)
        if not sys.stdin.isatty() and args.repository: raise SystemExit("non-interactive source add requires --include all or --include PATH")
        print("Discovered Skills:")
        for index, item in enumerate(found, 1): print(f"  {index}. {item.path}")
        choice = _ask("Select skills (comma-separated; blank for all)")
        included = tuple(item.path for item in found) if not choice else tuple(found[int(value.strip()) - 1].path for value in choice.split(","))
    return plan_add_source(root, request, SourceSelection(tuple(included), ()), git)


def run_maintenance_command(args, repo_root: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int | None:
    if args.command not in {"source", "skill"}: return None
    root, git = _root(args, repo_root), GitSourceClient()
    try:
        if args.command == "source":
            if args.source_command == "add":
                plan = _wizard_add(args, root, git); _confirm(args); return _emit(plan, args, stdout)
            if args.source_command == "check":
                if bool(args.source_id) == bool(args.all): raise SystemExit("provide SOURCE_ID or --all")
                sources = load_all_sources(root); selected = sources if args.all else tuple(source for source in sources if source.source_id == args.source_id)
                if not selected: raise SourceMaintenanceError("unknown source id")
                rows = []
                for source in selected:
                    inspection = inspect_source(root, source, git); changed = bool(inspection.added or inspection.updated or inspection.removed or inspection.old_revision != inspection.new_revision)
                    rows.append({"source_id": source.source_id, "status": "update_available" if changed else "current", "old_revision": inspection.old_revision, "new_revision": inspection.new_revision, "added": [item.path for item in inspection.added], "updated": [item.path for item in inspection.updated], "removed": list(inspection.removed), "ignored": list(inspection.ignored)})
                status = "update_available" if any(row["status"] == "update_available" for row in rows) else "current"
                data = {"status": status, "sources": rows}
                if args.json: stdout.write(format_json(data))
                else:
                    for row in rows: stdout.write(f"Source check: {row['source_id']}\n\nSource\n  Revision     {row['old_revision']} → {row['new_revision']}\n\nSkills\n  Added        {len(row['added'])}\n  Updated      {len(row['updated'])}\n  Removed      {len(row['removed'])}\n")
                return 1 if status == "update_available" else 0
            if args.source_command == "update":
                if bool(args.source_id) == bool(args.all): raise SystemExit("provide SOURCE_ID or --all")
                if not sys.stdin.isatty() and (not args.on_added or not args.on_removed): raise SystemExit("non-interactive source update requires --on-added and --on-removed")
                policies = UpdatePolicies(args.on_added or "include", args.on_removed or "remove")
                plan = plan_update_sources(root, None if args.all else (args.source_id,), policies, git); _confirm(args); return _emit(plan, args, stdout)
            if args.source_command == "adopt":
                plan = plan_adopt_skill(root, args.source_id, args.skill_id, args.path, args.replace, git); _confirm(args); return _emit(plan, args, stdout)
            if args.source_command == "ignore":
                source = next((source for source in load_all_sources(root) if source.source_id == args.source_id), None)
                if source is None: raise SourceMaintenanceError("unknown source id")
                if args.ignore_command == "list":
                    data = {"status": "success", "source_id": source.source_id, "ignored": [{"path": item.path, "reason": item.reason} for item in source.upstream.ignore]}; stdout.write(format_json(data) if args.json else "\n".join(f"{item.path:<32} {item.reason}" for item in source.upstream.ignore) + "\n"); return 0
                plan = plan_ignore_change(root, args.source_id, args.path, args.ignore_command, git if args.ignore_command == "remove" else None); _confirm(args); return _emit(plan, args, stdout)
            if args.source_command == "delete":
                if not args.skills: raise SystemExit("source delete requires --skills delete or --skills manualize")
                plan = plan_delete_source(root, args.source_id, args.skills, args.remove_from_profiles); _confirm(args); return _emit(plan, args, stdout)
        if args.command == "skill":
            if args.skill_command == "create":
                description = args.description or _ask("Description")
                license_name = args.license_name or _ask("License", "MIT")
                plan = plan_create_manual(root, args.skill_id, args.layer, description, license_name)
            elif args.skill_command == "update": plan = plan_update_manual(root, args.skill_id)
            elif args.skill_command == "move": plan = plan_move_skill(root, args.skill_id, args.layer)
            elif args.skill_command == "rename": plan = plan_rename_skill(root, args.skill_id, args.new_skill_id)
            elif args.skill_command == "delete": plan = plan_delete_skill(root, args.skill_id, args.remove_from_profiles)
            elif args.skill_command == "manualize": plan = plan_manualize_skill(root, args.skill_id)
            else: return None
            _confirm(args); return _emit(plan, args, stdout)
    except (SourceMaintenanceError, SkillMaintenanceError) as error:
        stderr.write(f"error: {error}\n"); return 1
    except (GitSourceError, OSError) as error:
        stderr.write(f"blocked: {error}\n"); return 3
    except (TransactionConflictError, TransactionError) as error:
        stderr.write(f"conflict: {error}\n"); return 4
