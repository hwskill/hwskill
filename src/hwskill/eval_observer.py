from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
from typing import Any


_DISCOVERY_TOOLS = {"fd", "find", "grep", "ls", "rg", "tree"}
_SHELLS = {"bash", "dash", "sh", "zsh"}
_SEPARATORS = {"&&", "||", ";", "|"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SKILL_LOCATION_HINT = re.compile(
    r"(?:skills-src|\.agents/skills|\.codex/skills|SKILL\.md|fetch_gitcode_pr_(?:patch|reviews)\.py)"
)
_PATCH_DIAGNOSTIC = re.compile(
    r"patch: state=(?P<state>\S+) api_base=(?P<api_base>\S+) "
    r"head=(?P<head>[0-9a-fA-F]+) files=(?P<files>\d+)"
)


def _load_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(
            command.replace("\n", " ; "), posix=True, punctuation_chars=";&|"
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        outer = list(lexer)
    except ValueError:
        return []
    if outer and Path(outer[0]).name in _SHELLS:
        for index, option in enumerate(outer[1:], start=1):
            if option.startswith("-") and "c" in option and index + 1 < len(outer):
                try:
                    lexer = shlex.shlex(
                        outer[index + 1].replace("\n", " ; "),
                        posix=True,
                        punctuation_chars=";&|",
                    )
                    lexer.whitespace_split = True
                    lexer.commenters = ""
                    return list(lexer)
                except ValueError:
                    return []
    return outer


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _command_argv(segment: list[str]) -> list[str]:
    index = 0
    while index < len(segment) and _ASSIGNMENT.match(segment[index]):
        index += 1
    if index < len(segment) and Path(segment[index]).name == "env":
        index += 1
        while index < len(segment) and (
            segment[index].startswith("-") or _ASSIGNMENT.match(segment[index])
        ):
            index += 1
    return segment[index:]


def _invocation_argv(
    command: str,
    expected_script: str,
    expected_url: str | None,
    expected_output: str | None,
) -> list[str] | None:
    for segment in _segments(_tokens(command)):
        argv = _command_argv(segment)
        if not argv:
            continue
        executable = Path(argv[0]).name
        if executable.startswith("python"):
            if len(argv) < 2 or argv[1] != expected_script:
                continue
            arguments = argv[2:]
        elif argv[0] == expected_script:
            arguments = argv[1:]
        else:
            continue
        if expected_url is not None and expected_url not in arguments:
            continue
        if expected_output is not None:
            output_matches = any(
                argument == f"--output={expected_output}"
                or (
                    argument == "--output"
                    and index + 1 < len(arguments)
                    and arguments[index + 1] == expected_output
                )
                for index, argument in enumerate(arguments)
            )
            if not output_matches:
                continue
        return argv
    return None


def _is_discovery(command: str) -> bool:
    for segment in _segments(_tokens(command)):
        argv = _command_argv(segment)
        if (
            argv
            and Path(argv[0]).name in _DISCOVERY_TOOLS
            and _SKILL_LOCATION_HINT.search(" ".join(argv))
        ):
            return True
    return False


def _structured_result(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result") or {}
    return result.get("structured_content") or result.get("structuredContent") or {}


def observe_script_resolution(
    event_path: Path,
    skill_id: str,
    script_name: str,
    *,
    expected_url: str | None = None,
    expected_output: str | None = None,
) -> dict[str, Any]:
    events = _load_events(event_path)
    load_index = None
    skill_file = None

    for event_index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if (
            item.get("type") == "mcp_tool_call"
            and item.get("tool") == "hwskill_load"
            and (item.get("arguments") or {}).get("skill_id") == skill_id
            and item.get("status") == "completed"
        ):
            candidate = _structured_result(item).get("skill_file")
            if candidate:
                load_index = event_index
                skill_file = candidate
                break

    if load_index is None or not skill_file:
        raise ValueError(f"completed hwskill_load not found for {skill_id}")

    search_indexes = []
    for event_index, event in enumerate(events[:load_index]):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        results = _structured_result(item).get("results") or []
        if (
            item.get("type") == "mcp_tool_call"
            and item.get("tool") == "hwskill_search"
            and item.get("status") == "completed"
            and any(result.get("skill_id") == skill_id for result in results)
        ):
            search_indexes.append(event_index)
    if not search_indexes:
        raise ValueError(f"completed hwskill_search not found for {skill_id}")

    expected_script = str(Path(skill_file).parent / "scripts" / script_name)
    command_items: list[tuple[int, dict[str, Any], list[str]]] = []
    for event_index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") != "command_execution":
            continue
        command = str(item.get("command", ""))
        argv = _invocation_argv(command, expected_script, expected_url, expected_output)
        if argv is not None:
            command_items.append((event_index, item, argv))

    invocation_before_load = any(index < load_index for index, _, _ in command_items)
    invocation_items = [entry for entry in command_items if entry[0] > load_index]
    if not invocation_items:
        raise ValueError(f"script invocation not found: {expected_script}")

    first_index, first_invocation, first_argv = invocation_items[0]
    commands_before = []
    discovery = []
    for event in events[:first_index]:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") != "command_execution":
            continue
        command = str(item.get("command", ""))
        commands_before.append(command)
        if _is_discovery(command):
            discovery.append(command)

    successful = next(
        (entry for entry in invocation_items if entry[1].get("exit_code") == 0), None
    )
    diagnostic = None
    if successful:
        match = _PATCH_DIAGNOSTIC.search(str(successful[1].get("aggregated_output", "")))
        if match:
            diagnostic = match.groupdict()
            diagnostic["files"] = int(diagnostic["files"])

    return {
        "skill_id": skill_id,
        "search_event_index": search_indexes[-1],
        "load_event_index": load_index,
        "first_script_event_index": first_index,
        "skill_file": skill_file,
        "expected_script": expected_script,
        "expected_url": expected_url,
        "expected_output": expected_output,
        "parsed_script_argv": first_argv,
        "script_invocation_command": str(first_invocation.get("command", "")),
        "script_invocation_attempts": len(invocation_items),
        "successful_script_invocation_command": (
            str(successful[1].get("command", "")) if successful else None
        ),
        "execution_succeeded": successful is not None,
        "patch_diagnostic": diagnostic,
        "commands_before_invocation": commands_before,
        "discovery_commands_before_invocation": discovery,
        "invocation_before_load": invocation_before_load,
        "used_runtime_anchored_path": expected_script in first_argv,
        "direct_resolution": not invocation_before_load and not discovery,
    }
