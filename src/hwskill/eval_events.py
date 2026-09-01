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
    normalized: list[tuple[int, dict[str, Any]]] = []
    position = 0
    for event in events:
        content = (event.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            current_position = position
            position += 1
            if block.get("type") == "tool_use":
                pending[str(block.get("id"))] = {
                    "name": _tool_name(str(block.get("name", ""))),
                    "input": block.get("input") or {},
                    "call_position": current_position,
                }
                continue
            if block.get("type") != "tool_result":
                continue
            call = pending.pop(str(block.get("tool_use_id")), None)
            if call is None:
                continue
            output = _text(block.get("content"))
            observation = {
                "call_position": call["call_position"],
                "result_position": current_position,
            }
            if call["name"] == "command_execution":
                normalized.append((call["call_position"], _completed({
                    "type": "command_execution",
                    "command": str(call["input"].get("command", "")),
                    "exit_code": 1 if block.get("is_error") else 0,
                    "status": "failed" if block.get("is_error") else "completed",
                    "aggregated_output": output,
                    "_observation": observation,
                })))
            elif call["name"] in {"hwskill_search", "hwskill_load"}:
                normalized.append((call["call_position"], _completed({
                    "type": "mcp_tool_call",
                    "tool": call["name"],
                    "arguments": call["input"],
                    "status": "failed" if block.get("is_error") else "completed",
                    "result": {"structured_content": _structured(block.get("content"))},
                    "_observation": observation,
                })))
    return [item for _, item in sorted(normalized, key=lambda entry: entry[0])]


def _exit_code(state: dict[str, Any]) -> int:
    metadata = state.get("metadata") or {}
    for key in ("exit", "exitCode", "exit_code", "code"):
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


def _codex_exit_code(item: dict[str, Any]) -> int:
    try:
        return int(item.get("exit_code", 1))
    except (TypeError, ValueError):
        return 1


def _codex(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain completed observable calls, excluding messages and hidden reasoning."""
    normalized: list[dict[str, Any]] = []
    for event in events:
        item = event.get("item") if event.get("type") == "item.completed" else None
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "command_execution":
            normalized.append(_completed({
                "type": "command_execution",
                "command": str(item.get("command", "")),
                "exit_code": _codex_exit_code(item),
                "status": str(item.get("status", "failed")),
                "aggregated_output": _text(item.get("aggregated_output")),
            }))
        elif item_type == "mcp_tool_call":
            normalized.append(_completed({
                "type": "mcp_tool_call",
                "tool": str(item.get("tool", "")),
                "arguments": item.get("arguments") or {},
                "status": str(item.get("status", "failed")),
                "result": {"structured_content": _structured(item.get("result") or {})},
            }))
    return normalized


class EventStreamNormalizer:
    """Incrementally normalize a host event stream without retaining raw records."""

    def __init__(self, host: str) -> None:
        self.host = host.replace("_", "-")
        if self.host not in {"codex", "claude-code", "opencode"}:
            raise ValueError(f"unsupported event host: {host}")
        self._pending: dict[str, dict[str, Any]] = {}
        self._normalized: list[tuple[int, dict[str, Any]]] = []
        self._position = 0

    def consume(self, event: dict[str, Any]) -> None:
        if self.host == "codex":
            self._append(_codex([event]))
        elif self.host == "opencode":
            self._append(_opencode([event]))
        else:
            self._consume_claude(event)

    def finish(self) -> list[dict[str, Any]]:
        return [item for _, item in sorted(self._normalized, key=lambda entry: entry[0])]

    def _append(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            self._normalized.append((self._position, item))
            self._position += 1

    def _consume_claude(self, event: dict[str, Any]) -> None:
        content = (event.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            current_position = self._position
            self._position += 1
            if block.get("type") == "tool_use":
                self._pending[str(block.get("id"))] = {
                    "name": _tool_name(str(block.get("name", ""))),
                    "input": block.get("input") or {},
                    "call_position": current_position,
                }
                continue
            if block.get("type") != "tool_result":
                continue
            call = self._pending.pop(str(block.get("tool_use_id")), None)
            if call is None:
                continue
            output = _text(block.get("content"))
            observation = {
                "call_position": call["call_position"],
                "result_position": current_position,
            }
            if call["name"] == "command_execution":
                item = _completed({
                    "type": "command_execution",
                    "command": str(call["input"].get("command", "")),
                    "exit_code": 1 if block.get("is_error") else 0,
                    "status": "failed" if block.get("is_error") else "completed",
                    "aggregated_output": output,
                    "_observation": observation,
                })
            elif call["name"] in {"hwskill_search", "hwskill_load"}:
                item = _completed({
                    "type": "mcp_tool_call",
                    "tool": call["name"],
                    "arguments": call["input"],
                    "status": "failed" if block.get("is_error") else "completed",
                    "result": {"structured_content": _structured(block.get("content"))},
                    "_observation": observation,
                })
            else:
                continue
            self._normalized.append((call["call_position"], item))


def normalize_event_records(events: list[dict[str, Any]], host: str) -> list[dict[str, Any]]:
    """Normalize in-memory host records so raw streams never reach artifacts."""
    normalizer = EventStreamNormalizer(host)
    for event in events:
        normalizer.consume(event)
    return normalizer.finish()


def normalize_events(path: Path, host: str) -> list[dict[str, Any]]:
    return normalize_event_records(_read(path), host)
