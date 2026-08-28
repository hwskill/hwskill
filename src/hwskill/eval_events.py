from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


_SKILL_FILE = re.compile(r"(?:^|[\s{,])['\"]?skill_file['\"]?\s*:\s*['\"]?([^\s'\",}]+)")
_SKILL_ID = re.compile(r"(?:^|[\s{,])['\"]?skill_id['\"]?\s*:\s*['\"]?([^\s'\",}]+)")


def _read(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in value
        )
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _structured(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        data = value
    else:
        text = _text(value).strip()
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            data = {}
        if not data:
            skill_file = _SKILL_FILE.search(text)
            skill_ids = _SKILL_ID.findall(text)
            if skill_file:
                data["skill_file"] = skill_file.group(1)
            if skill_ids:
                data["results"] = [{"skill_id": item} for item in skill_ids]
    nested = data.get("structured_content") or data.get("structuredContent")
    return nested if isinstance(nested, dict) else data


def _tool_name(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith("hwskill_search"):
        return "hwskill_search"
    if lowered.endswith("hwskill_load"):
        return "hwskill_load"
    if lowered in {"bash", "shell", "terminal", "exec", "execute"}:
        return "command_execution"
    return name


def _completed(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "item.completed", "item": item}


def _claude(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending: dict[str, dict[str, Any]] = {}
    normalized: list[dict[str, Any]] = []
    for event in events:
        content = (event.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                pending[str(block.get("id"))] = {
                    "name": _tool_name(str(block.get("name", ""))),
                    "input": block.get("input") or {},
                }
                continue
            if block.get("type") != "tool_result":
                continue
            call = pending.pop(str(block.get("tool_use_id")), None)
            if call is None:
                continue
            output = _text(block.get("content"))
            if call["name"] == "command_execution":
                normalized.append(_completed({
                    "type": "command_execution",
                    "command": str(call["input"].get("command", "")),
                    "exit_code": 1 if block.get("is_error") else 0,
                    "status": "failed" if block.get("is_error") else "completed",
                    "aggregated_output": output,
                }))
            elif call["name"] in {"hwskill_search", "hwskill_load"}:
                normalized.append(_completed({
                    "type": "mcp_tool_call",
                    "tool": call["name"],
                    "arguments": call["input"],
                    "status": "failed" if block.get("is_error") else "completed",
                    "result": {"structured_content": _structured(block.get("content"))},
                }))
    return normalized


def _exit_code(state: dict[str, Any]) -> int:
    metadata = state.get("metadata") or {}
    for key in ("exitCode", "exit_code", "code"):
        if key in metadata:
            try:
                return int(metadata[key])
            except (TypeError, ValueError):
                return 1
    return 0 if state.get("status") == "completed" else 1


def _opencode(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        part = event.get("part") or {}
        state = part.get("state") or {}
        if event.get("type") != "tool_use" or part.get("type") != "tool":
            continue
        if state.get("status") not in {"completed", "error"}:
            continue
        name = _tool_name(str(part.get("tool", "")))
        input_data = state.get("input") or {}
        output = _text(state.get("output"))
        if name == "command_execution":
            code = _exit_code(state)
            normalized.append(_completed({
                "type": "command_execution",
                "command": str(input_data.get("command", "")),
                "exit_code": code,
                "status": "completed" if code == 0 else "failed",
                "aggregated_output": output,
            }))
        elif name in {"hwskill_search", "hwskill_load"}:
            normalized.append(_completed({
                "type": "mcp_tool_call",
                "tool": name,
                "arguments": input_data,
                "status": "completed" if state.get("status") == "completed" else "failed",
                "result": {"structured_content": _structured(state.get("output"))},
            }))
    return normalized


def normalize_events(path: Path, host: str) -> list[dict[str, Any]]:
    host = host.replace("_", "-")
    events = _read(path)
    if host == "codex":
        return events
    if host == "claude-code":
        return _claude(events)
    if host == "opencode":
        return _opencode(events)
    raise ValueError(f"unsupported event host: {host}")
