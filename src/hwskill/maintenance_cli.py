from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import TextIO

from .console_output import format_json, format_maintenance_plan, plan_data
from .git_source import GitSourceClient, GitSourceError, discover_skills
from .maintenance_transaction import TransactionConflictError, TransactionError
from .skill_maintenance import (
    SkillMaintenanceError, plan_adopt_skill, plan_create_manual, plan_delete_skill,
    plan_manualize_skill, plan_move_skill, plan_rename_skill, plan_update_manual,
)
from .source_manifest import SourceDefaults, SourceManifestError, load_all_sources
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
    add.add_argument("--layer"); add.add_argument("--license", dest="license_name")
    add.add_argument("--include", action="append"); add.add_argument("--yes", action="store_true"); add.add_argument("--json", action="store_true")
    check = source_commands.add_parser("check", help="Check upstream source revisions")
    check.add_argument("source_id", nargs="?"); check.add_argument("--all", action="store_true"); check.add_argument("--repo-root"); check.add_argument("--json", action="store_true")
    update = source_commands.add_parser("update", help="Update upstream source snapshots")
    update.add_argument("source_id", nargs="?"); update.add_argument("--all", action="store_true"); update.add_argument("--repo-root")
    update.add_argument("--on-added", choices=("include", "ignore", "fail")); update.add_argument("--on-removed", choices=("remove", "manualize", "fail")); update.add_argument("--track"); update.add_argument("--select-track", action="store_true"); update.add_argument("--yes", action="store_true"); update.add_argument("--json", action="store_true")
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


class UsageError(ValueError):
    pass


def _interactive(stdin: TextIO) -> bool:
    return bool(getattr(stdin, "isatty", lambda: False)())


def _ask(stdin: TextIO, stdout: TextIO, prompt: str, default: str = "") -> str:
    stdout.write(f"{prompt}" + (f" [{default}]" if default else "") + ": ")
    try:
        answer = stdin.readline()
    except (EOFError, OSError) as error:
        raise UsageError("input stream is unavailable") from error
    if answer == "":
        raise UsageError("input ended before confirmation")
    return answer.strip() or default


def _source_id_from_repository(repository: str) -> str:
    name = repository.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return name.replace(" ", "-")


def _detect_skills_path(repository: str, track: str, git: GitSourceClient) -> str:
    with tempfile.TemporaryDirectory(prefix="hwskill-source-detect-") as temporary:
        checkout = Path(temporary)
        git.materialize(repository, track, checkout)
        parents = [path.parent.relative_to(checkout).parts for path in checkout.rglob("SKILL.md")]
    if not parents:
        raise SourceMaintenanceError("no Skills discovered upstream")
    prefix = list(parents[0])
    for parts in parents[1:]:
        prefix = prefix[:next((index for index, pair in enumerate(zip(prefix, parts)) if pair[0] != pair[1]), min(len(prefix), len(parts)))]
    # A lone SKILL.md lives below the enumerable directory; never return its own directory.
    if len(parents) == 1 and prefix == list(parents[0]):
        prefix = prefix[:-1]
    return "/".join(prefix) or "."


def _select_indices(value: str, count: int) -> tuple[int, ...]:
    if not value.strip(): return tuple(range(count))
    try: selected = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as error: raise UsageError("selection must be comma-separated integers") from error
    if len(set(selected)) != len(selected) or any(item < 1 or item > count for item in selected):
        raise UsageError("selection contains duplicate or out-of-range index")
    return tuple(item - 1 for item in selected)


def _confirm(args, stdin: TextIO, stdout: TextIO) -> None:
    if args.yes: return
    if not _interactive(stdin): raise UsageError("non-interactive writes require --yes")
    if _ask(stdin, stdout, "Apply these changes? [y/N]", "n").lower() not in {"y", "yes"}: raise UsageError("cancelled")


def _emit(plan, args, stdout: TextIO) -> int:
    plan.apply()
    stdout.write(format_json(plan_data(plan)) if args.json else format_maintenance_plan(plan, color=stdout.isatty()))
    return 0


def _root(args, repo_root: Path) -> Path:
    return Path(args.repo_root).resolve() if getattr(args, "repo_root", None) else Path(repo_root)


def _wizard_add(args, root: Path, git: GitSourceClient, stdin: TextIO, stdout: TextIO):
    if not _interactive(stdin) and not args.repository: raise UsageError("non-interactive source add requires --repository")
    repository = args.repository or _ask(stdin, stdout, "Git repository URL")
    refs = git.list_remote(repository)
    source_id = args.source_id or _source_id_from_repository(repository)
    known = {item.source_id for item in load_all_sources(root)}
    while source_id in known:
        if not _interactive(stdin): raise UsageError(f"source already exists: {source_id}")
        source_id = _ask(stdin, stdout, "Source ID", source_id)
    track = args.track or (refs.default_branch if not _interactive(stdin) else _ask(stdin, stdout, "Track", refs.default_branch))
    detected_path = _detect_skills_path(repository, track, git)
    skills_path = args.skills_path or (detected_path if not _interactive(stdin) else _ask(stdin, stdout, "Skills path", detected_path))
    if args.include:
        included = () if args.include == ["all"] else tuple(args.include)
        if args.include == ["all"]:
            # plan_add_source gets the discovered list; an empty marker is expanded below.
            with tempfile.TemporaryDirectory() as temporary:
                checkout = Path(temporary); git.materialize(repository, track, checkout)
                included = tuple(item.path for item in discover_skills(checkout, skills_path))
    else:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary); git.materialize(repository, track, checkout)
            found = discover_skills(checkout, skills_path)
        if not _interactive(stdin): raise UsageError("non-interactive source add requires --include all or --include PATH")
        stdout.write("Discovered Skills:\n")
        for index, item in enumerate(found, 1): stdout.write(f"  {index}. {item.path}\n")
        choice = _ask(stdin, stdout, "Select skills (comma-separated; blank for all)")
        included = tuple(found[index].path for index in _select_indices(choice, len(found)))
    namespace = args.namespace or (source_id if not _interactive(stdin) else _ask(stdin, stdout, "Namespace", source_id))
    layer = args.layer or (_ask(stdin, stdout, "Default layer", "l1") if _interactive(stdin) else "l1")
    license_name = args.license_name or (_ask(stdin, stdout, "License", "MIT") if _interactive(stdin) else "MIT")
    request = SourceAddRequest(source_id, repository, track, skills_path, SourceDefaults(namespace, layer, license_name))
    return plan_add_source(root, request, SourceSelection(tuple(included), ()), git)


def run_maintenance_command(args, repo_root: Path, stdin: TextIO, stdout: TextIO, stderr: TextIO, git_client: GitSourceClient | None = None) -> int | None:
    if args.command not in {"source", "skill"}: return None
    root, git = _root(args, repo_root), git_client or GitSourceClient()
    try:
        interaction = stderr if getattr(args, "json", False) else stdout
        is_write = not (args.command == "source" and (args.source_command == "check" or (args.source_command == "ignore" and args.ignore_command == "list")))
        if is_write and not _interactive(stdin) and not args.yes: raise UsageError("non-interactive writes require --yes")
        if args.command == "source":
            if args.source_command == "add":
                plan = _wizard_add(args, root, git, stdin, interaction); _confirm(args, stdin, interaction); return _emit(plan, args, stdout)
            if args.source_command == "check":
                if bool(args.source_id) == bool(args.all): raise UsageError("provide SOURCE_ID or --all")
                sources = load_all_sources(root); selected = sources if args.all else tuple(source for source in sources if source.source_id == args.source_id)
                if not selected: raise SourceMaintenanceError("unknown source id")
                rows = []
                for source in selected:
                    inspection = inspect_source(root, source, git); changed = bool(inspection.added or inspection.updated or inspection.removed or inspection.old_revision != inspection.new_revision)
                    rows.append({"source_id": source.source_id, "status": "update_available" if changed else "current", "repository": source.upstream.repository, "track": source.upstream.track, "old_revision": inspection.old_revision, "new_revision": inspection.new_revision, "deltas": {"added": [item.path for item in inspection.added], "updated": [item.path for item in inspection.updated], "removed": list(inspection.removed), "ignored": list(inspection.ignored)}, "affected_profiles": [], "affected_tests": []})
                status = "update_available" if any(row["status"] == "update_available" for row in rows) else "current"
                data = {"status": status, "sources": rows}
                if args.json: stdout.write(format_json(data))
                else:
                    for row in rows: stdout.write(f"Source check: {row['source_id']}\n\nSource\n  Repository   {row['repository']}\n  Track        {row['track']}\n  Revision     {row['old_revision']} → {row['new_revision']}\n\nSkills\n  Added        {len(row['deltas']['added'])}\n  Updated      {len(row['deltas']['updated'])}\n  Removed      {len(row['deltas']['removed'])}\n\nProfiles\n  Affected     0\n\nTests\n  Affected     0\n")
                return 1 if status == "update_available" else 0
            if args.source_command == "update":
                if bool(args.source_id) == bool(args.all): raise UsageError("provide SOURCE_ID or --all")
                if args.track and (args.all or args.select_track): raise UsageError("--track is only valid for one source")
                if args.select_track:
                    if args.all or not _interactive(stdin): raise UsageError("--select-track requires one interactive source")
                    source = next((value for value in load_all_sources(root) if value.source_id == args.source_id), None)
                    if source is None: raise SourceMaintenanceError("unknown source id")
                    refs = git.list_remote(source.upstream.repository)
                    choices = sorted(refs.refs)
                    interaction.write("Available tracks:\n" + "".join(f"  {i}. {value}\n" for i, value in enumerate(choices, 1)))
                    selected = _ask(stdin, interaction, "Track", source.upstream.track)
                    args.track = source.upstream.track if not selected.strip() else (choices[_select_indices(selected, len(choices))[0]] if selected.isdigit() else selected)
                if not args.on_added or not args.on_removed:
                    if not _interactive(stdin) or args.yes: raise UsageError("source update requires --on-added and --on-removed")
                    args.on_added = args.on_added or _ask(stdin, interaction, "On added (include/ignore/fail)", "include")
                    args.on_removed = args.on_removed or _ask(stdin, interaction, "On removed (remove/manualize/fail)", "remove")
                    if args.on_added not in {"include", "ignore", "fail"} or args.on_removed not in {"remove", "manualize", "fail"}:
                        raise UsageError("invalid update policy")
                policies = UpdatePolicies(args.on_added, args.on_removed)
                plan = plan_update_sources(root, None if args.all else (args.source_id,), policies, git, {args.source_id: args.track} if args.track else None); _confirm(args, stdin, interaction); return _emit(plan, args, stdout)
            if args.source_command == "adopt":
                plan = plan_adopt_skill(root, args.source_id, args.skill_id, args.path, args.replace, git); _confirm(args, stdin, interaction); return _emit(plan, args, stdout)
            if args.source_command == "ignore":
                source = next((source for source in load_all_sources(root) if source.source_id == args.source_id), None)
                if source is None: raise SourceMaintenanceError("unknown source id")
                if args.ignore_command == "list":
                    data = {"status": "success", "source_id": source.source_id, "ignored": [{"path": item.path, "reason": item.reason} for item in source.upstream.ignore]}; stdout.write(format_json(data) if args.json else "\n".join(f"{item.path:<32} {item.reason}" for item in source.upstream.ignore) + "\n"); return 0
                plan = plan_ignore_change(root, args.source_id, args.path, args.ignore_command, git if args.ignore_command == "remove" else None); _confirm(args, stdin, interaction); return _emit(plan, args, stdout)
            if args.source_command == "delete":
                if not args.skills: raise UsageError("source delete requires --skills delete or --skills manualize")
                plan = plan_delete_source(root, args.source_id, args.skills, args.remove_from_profiles); _confirm(args, stdin, interaction); return _emit(plan, args, stdout)
        if args.command == "skill":
            if args.skill_command == "create":
                if not args.description and not _interactive(stdin): raise UsageError("non-interactive skill create requires --description")
                description = args.description or _ask(stdin, interaction, "Description")
                license_name = args.license_name or (_ask(stdin, interaction, "License", "MIT") if _interactive(stdin) else "MIT")
                plan = plan_create_manual(root, args.skill_id, args.layer, description, license_name)
            elif args.skill_command == "update": plan = plan_update_manual(root, args.skill_id)
            elif args.skill_command == "move": plan = plan_move_skill(root, args.skill_id, args.layer)
            elif args.skill_command == "rename": plan = plan_rename_skill(root, args.skill_id, args.new_skill_id)
            elif args.skill_command == "delete": plan = plan_delete_skill(root, args.skill_id, args.remove_from_profiles)
            elif args.skill_command == "manualize": plan = plan_manualize_skill(root, args.skill_id)
            else: return None
            _confirm(args, stdin, interaction); return _emit(plan, args, stdout)
    except UsageError as error:
        stderr.write(f"usage: {error}\n"); return 2
    except SourceManifestError as error:
        stderr.write(f"manifest: {error}\n"); return 2
    except (SourceMaintenanceError, SkillMaintenanceError) as error:
        stderr.write(f"error: {error}\n"); return 1
    except (GitSourceError, OSError) as error:
        stderr.write(f"blocked: {error}\n"); return 3
    except (TransactionConflictError, TransactionError) as error:
        stderr.write(f"conflict: {error}\n"); return 4
