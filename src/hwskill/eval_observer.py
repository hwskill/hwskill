from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Any

from .eval_events import normalize_events


_DISCOVERY_TOOLS = {"fd", "find", "grep", "ls", "rg", "tree"}
_SHELLS = {"bash", "dash", "sh", "zsh"}
_SEPARATORS = {"&&", "||", ";", "|"}
_UNSAFE_AUDIT_CHARACTERS = frozenset(";|&<>")
_SAFE_PYTHON_OPTIONS = frozenset({"-u"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NATIVE_SKILL_ROOT = re.compile(
    r"(?:^|/|\s)\.(?:agents|codex|claude|opencode)/skills(?:/|\s|$)"
)
_PATCH_DIAGNOSTIC = re.compile(
    r"patch: state=(?P<state>\S+) api_base=(?P<api_base>\S+) "
    r"head=(?P<head>[0-9a-fA-F]+) files=(?P<files>\d+)"
)


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


def _lex(command: str) -> list[str]:
    """Return shell-like tokens without treating a compound command as one action."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def _single_auditable_tokens(command: str) -> list[str] | None:
    """Accept only one shell execution whose event result belongs to that command.

    Agent hosts commonly record a direct command behind ``sh -c``.  That form is
    retained, but shell lists, redirects, and an additional outer argument are
    rejected instead of assigning the event's aggregate status/output to one
    selected segment.
    """
    if "\n" in command or "\r" in command:
        return None
    outer = _lex(command)
    if not outer or any(_UNSAFE_AUDIT_CHARACTERS.intersection(token) for token in outer):
        return None
    if Path(outer[0]).name not in _SHELLS:
        return outer

    command_index = next(
        (index for index, option in enumerate(outer[1:], start=1)
         if option.startswith("-") and "c" in option),
        None,
    )
    if command_index is None or command_index + 2 != len(outer):
        return None
    return _single_auditable_tokens(outer[command_index + 1])


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
    if index < len(segment) and segment[index] == "command":
        index += 1
    return segment[index:]


def _invocation_argv(
    command: str,
    expected_script: str,
    expected_url: str | None,
    expected_output: str | None,
) -> tuple[list[str], str | None] | None:
    tokens = _single_auditable_tokens(command)
    if tokens is None:
        return None
    argv = _command_argv(tokens)
    if not argv:
        return None
    executable = Path(argv[0]).name
    if executable.startswith("python"):
        script_index = 1
        while script_index < len(argv) and argv[script_index].startswith("-"):
            if argv[script_index] not in _SAFE_PYTHON_OPTIONS:
                return None
            script_index += 1
        if len(argv) <= script_index or argv[script_index] != expected_script:
            return None
        arguments = argv[script_index + 1:]
    elif argv[0] == expected_script:
        arguments = argv[1:]
    else:
        return None
    if expected_url is not None and expected_url not in arguments:
        return None
    valid_output, output_argument = _output_argument(arguments)
    if not valid_output:
        return None
    if expected_output is not None and output_argument != expected_output:
        return None
    return argv, output_argument


def _output_argument(arguments: list[str]) -> tuple[bool, str | None]:
    """Parse exactly one valid ``--output`` occurrence when one is supplied."""
    values: list[str] = []
    for index, argument in enumerate(arguments):
        if argument.startswith("--output="):
            value = argument.removeprefix("--output=")
            if not value or value.startswith("-"):
                return False, None
            values.append(value)
        elif argument == "--output":
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
                return False, None
            values.append(arguments[index + 1])
    if len(values) != 1:
        return (not values), None
    return True, values[0]


def _is_discovery(command: str, skill_file: str, expected_script: str) -> bool:
    skill_directory = str(Path(skill_file).parent)
    script_name = Path(expected_script).name
    for segment in _segments(_tokens(command)):
        argv = _command_argv(segment)
        arguments = " ".join(argv)
        location_hint = any(item in arguments for item in (
            skill_directory, skill_file, expected_script, script_name, "SKILL.md"
        )) or _NATIVE_SKILL_ROOT.search(arguments)
        if argv and Path(argv[0]).name in _DISCOVERY_TOOLS and location_hint:
            return True
    return False


def _structured_result(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result") or {}
    return result.get("structured_content") or result.get("structuredContent") or {}


def _completed_before_call(
    first_item: dict[str, Any],
    first_index: int,
    second_item: dict[str, Any],
    second_index: int,
) -> bool:
    first_observation = first_item.get("_observation") or {}
    second_observation = second_item.get("_observation") or {}
    result_position = first_observation.get("result_position")
    call_position = second_observation.get("call_position")
    if isinstance(result_position, int) and isinstance(call_position, int):
        return result_position < call_position
    return first_index < second_index


def observe_script_resolution(
    event_path: Path,
    skill_id: str,
    script_name: str,
    *,
    expected_url: str | None = None,
    expected_output: str | None = None,
    host: str = "codex",
) -> dict[str, Any]:
    host = host.replace("_", "-")
    events = normalize_events(event_path, host)
    load_index = None
    skill_file = None
    load_item = None

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
                load_item = item
                break

    if load_index is None or not skill_file:
        raise ValueError(f"completed hwskill_load not found for {skill_id}")

    search_indexes = []
    for event_index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        results = _structured_result(item).get("results") or []
        if (
            item.get("type") == "mcp_tool_call"
            and item.get("tool") == "hwskill_search"
            and item.get("status") == "completed"
            and any(result.get("skill_id") == skill_id for result in results)
            and _completed_before_call(item, event_index, load_item, load_index)
        ):
            search_indexes.append(event_index)
    if not search_indexes:
        raise ValueError(f"completed hwskill_search not found for {skill_id}")

    expected_script = str(Path(skill_file).parent / "scripts" / script_name)
    command_items: list[tuple[int, dict[str, Any], list[str], str | None]] = []
    for event_index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") != "command_execution":
            continue
        command = str(item.get("command", ""))
        invocation = _invocation_argv(command, expected_script, expected_url, expected_output)
        if invocation is not None:
            argv, output_argument = invocation
            command_items.append((event_index, item, argv, output_argument))

    invocation_before_load = any(
        not _completed_before_call(load_item, load_index, item, index)
        for index, item, _, _ in command_items
    )
    invocation_items = [
        entry for entry in command_items
        if _completed_before_call(load_item, load_index, entry[1], entry[0])
    ]
    if not invocation_items:
        raise ValueError(
            f"script invocation not found after completed hwskill_load: {expected_script}"
        )

    first_index, first_invocation, first_argv, first_output_argument = invocation_items[0]
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
        if _is_discovery(command, skill_file, expected_script):
            discovery.append(command)

    discovery_after_load = []
    for event_index, event in enumerate(
        events[load_index + 1:first_index], start=load_index + 1
    ):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") != "command_execution":
            continue
        command = str(item.get("command", ""))
        if (
            _completed_before_call(load_item, load_index, item, event_index)
            and _is_discovery(command, skill_file, expected_script)
        ):
            discovery_after_load.append(command)

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
        "host": host,
        "skill_id": skill_id,
        "search_event_index": search_indexes[-1],
        "load_event_index": load_index,
        "first_script_event_index": first_index,
        "skill_file": skill_file,
        "expected_script": expected_script,
        "expected_url": expected_url,
        "expected_output": expected_output,
        "script_output_argument": first_output_argument,
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
        "discovery_commands_after_load": discovery_after_load,
        "invocation_before_load": invocation_before_load,
        "used_runtime_anchored_path": expected_script in first_argv,
        "direct_resolution": not invocation_before_load and not discovery_after_load,
    }
