"""Non-interactive host adapters for isolated Agent test actions."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Literal, Protocol, Sequence

from .eval_events import normalize_event_records
from .hosts import canonical_host
from .test_artifacts import ActionResult, write_json, write_text
from .test_manifest import AgentAction, CommandAction
from .test_runner import (
    ActionContext,
    _blocked_result,
    _command_environment,
    _open_anchored_workdir,
    _terminate_process_group,
)


class TestHost(Protocol):
    host_id: str

    def build_command(
        self,
        context: ActionContext,
        action: AgentAction | None = None,
        *,
        model: str,
        reasoning: str,
    ) -> tuple[str, ...]:
        raise NotImplementedError

    def response_path(self, context: ActionContext) -> Path:
        raise NotImplementedError


def _host_action(context: ActionContext, action: AgentAction | None) -> AgentAction:
    if action is not None:
        return action
    candidate = getattr(context, "action", None)
    if isinstance(candidate, AgentAction):
        return candidate
    raise ValueError("host command requires an Agent action")


def _action_workdir(context: ActionContext, action: AgentAction | None) -> Path:
    action = _host_action(context, action)
    return context.workspace / (action.workdir or ".")


class CodexTestHost:
    host_id = "codex"

    def response_path(self, context: ActionContext) -> Path:
        return context.artifact_dir / "final-response.raw"

    def build_command(self, context: ActionContext, action: AgentAction | None = None, *, model: str, reasoning: str) -> tuple[str, ...]:
        action = _host_action(context, action)
        return (
            "codex", "exec", "--model", model,
            "-c", f'model_reasoning_effort="{reasoning}"',
            "--json", "--cd", str(_action_workdir(context, action)),
            "--output-last-message", str(self.response_path(context)),
            action.prompt,
        )


class ClaudeCodeTestHost:
    host_id = "claude-code"

    def response_path(self, context: ActionContext) -> Path:
        return context.artifact_dir / "final-response.raw"

    def build_command(self, context: ActionContext, action: AgentAction | None = None, *, model: str, reasoning: str) -> tuple[str, ...]:
        action = _host_action(context, action)
        del context, reasoning
        return (
            "claude", "--print", "--output-format", "stream-json", "--verbose",
            "--model", model, action.prompt,
        )


class OpenCodeTestHost:
    host_id = "opencode"

    def response_path(self, context: ActionContext) -> Path:
        return context.artifact_dir / "final-response.raw"

    def build_command(self, context: ActionContext, action: AgentAction | None = None, *, model: str, reasoning: str) -> tuple[str, ...]:
        action = _host_action(context, action)
        del context, reasoning
        return ("opencode", "run", "--format", "json", "--model", model, action.prompt)


_HOSTS: dict[str, TestHost] = {
    "codex": CodexTestHost(),
    "claude-code": ClaudeCodeTestHost(),
    "opencode": OpenCodeTestHost(),
}


CredentialCheck = Callable[[str], bool]
SetupRunner = Callable[..., subprocess.CompletedProcess[str]]


def _credentials_unavailable(_host: str) -> bool:
    """No credential is assumed until the later setup service verifies one."""
    return False


class AgentExecutor:
    """Run an Agent action without persisting raw model output or hidden reasoning."""

    def __init__(
        self,
        host: TestHost | None = None,
        *,
        credential_available: CredentialCheck = _credentials_unavailable,
        setup_runner: SetupRunner = subprocess.run,
    ) -> None:
        self.host = host
        self._credential_available = credential_available
        self._setup_runner = setup_runner

    def run(self, action: CommandAction | AgentAction, context: ActionContext) -> ActionResult:
        if not isinstance(action, AgentAction):
            return _blocked_result(action.action_id, context.artifact_dir, "agent executor cannot run a command action", context.environment)
        try:
            host = self.host or _HOSTS[canonical_host(context.environment.host)]
        except (KeyError, ValueError) as exc:
            return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)
        if not self._credential_available(host.host_id):
            return _blocked_result(
                action.action_id, context.artifact_dir,
                f"credentials are unavailable for Agent host {host.host_id}", context.environment,
            )
        try:
            self._setup(host.host_id, context)
            workdir_fd, cwd = _open_anchored_workdir(context.workspace, action.workdir)
            try:
                process = subprocess.Popen(
                    host.build_command(
                        context, action,
                        model=context.environment.model,
                        reasoning=context.environment.reasoning,
                    ),
                    cwd=cwd,
                    env=_command_environment(context.environment, context.workspace),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,
                    pass_fds=(workdir_fd,),
                )
            finally:
                # The child inherited the descriptor; retaining it in the runner leaks one fd/action.
                import os
                os.close(workdir_fd)
            try:
                stdout, stderr = process.communicate(timeout=context.environment.timeout_seconds)
            except subprocess.TimeoutExpired:
                _terminate_process_group(process)
                stdout, stderr = process.communicate()
                return self._blocked(action, context, host, stdout, stderr, "timeout")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)

        raw_records = _json_records(stdout)
        normalized = _normalize_records(raw_records, host.host_id)
        _persist_agent_artifacts(context, action, host, raw_records, normalized, stderr)
        status: Literal["completed", "failed"] = "completed" if process.returncode == 0 else "failed"
        payload = {
            "action_id": action.action_id,
            "kind": "agent",
            "host": host.host_id,
            "model": context.environment.model,
            "reasoning": context.environment.reasoning,
            "workdir": str(_action_workdir(context, action)),
            "status": status,
            "exit_code": process.returncode,
        }
        write_json(context.artifact_dir / "result.json", payload, context.environment.secret_values)
        return ActionResult(action.action_id, status, process.returncode, context.artifact_dir)

    def _setup(self, host: str, context: ActionContext) -> None:
        """Configure only the disposable workspace as a project-scoped integration."""
        command = (
            sys.executable, "-m", "hwskill", "setup", host,
            "--project", str(context.workspace), "--repo-root", str(context.environment.repo_root), "--yes",
        )
        completed = self._setup_runner(
            command,
            cwd=str(context.workspace),
            env=_command_environment(context.environment, context.workspace),
            text=True,
            capture_output=True,
            timeout=context.environment.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"hwskill project setup failed for {host}")

    def _blocked(self, action: AgentAction, context: ActionContext, host: TestHost, stdout: str, stderr: str, reason: str) -> ActionResult:
        raw_records = _json_records(stdout)
        normalized = _normalize_records(raw_records, host.host_id)
        _persist_agent_artifacts(context, action, host, raw_records, normalized, stderr)
        payload = {
            "action_id": action.action_id,
            "kind": "agent",
            "host": host.host_id,
            "status": "blocked",
            "exit_code": None,
            "reason": reason,
        }
        write_json(context.artifact_dir / "result.json", payload, context.environment.secret_values)
        return ActionResult(action.action_id, "blocked", None, context.artifact_dir)


def _json_records(stdout: str) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return tuple(records)


def _normalize_records(records: Sequence[dict[str, object]], host: str) -> tuple[dict[str, object], ...]:
    return tuple(normalize_event_records(list(records), host))


def _persist_agent_artifacts(
    context: ActionContext,
    action: AgentAction,
    host: TestHost,
    raw_records: Sequence[dict[str, object]],
    normalized: Sequence[dict[str, object]],
    stderr: str,
) -> None:
    event_text = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in normalized)
    write_text(context.artifact_dir / "events.jsonl", event_text, context.environment.secret_values)
    # stdout is deliberately the same filtered observable stream, never the raw host JSONL.
    write_text(context.artifact_dir / "stdout.log", event_text, context.environment.secret_values)
    write_text(context.artifact_dir / "stderr.log", stderr, context.environment.secret_values)
    raw_path = host.response_path(context)
    try:
        response = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else _final_response(raw_records)
    finally:
        # Codex writes this directly. Do not retain an unfiltered transient response alongside artifacts.
        raw_path.unlink(missing_ok=True)
    write_text(context.artifact_dir / "final-response.md", response, context.environment.secret_values)
    del action


def _final_response(events: Sequence[dict[str, object]]) -> str:
    messages: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        # Codex final messages and Claude's documented stream-json result are
        # both final-answer channels rather than reasoning content.
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            messages.append(event["result"])
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
            continue
        message = event.get("message")
        if event.get("type") == "assistant" and isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                messages.extend(
                    block["text"] for block in content
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
                )
        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            messages.append(part["text"])
    return "\n".join(messages)


def append_agent_post_check_metadata(prompt: str, context_path: Path, artifact_dir: Path, workspace: Path) -> str:
    """Keep runner paths visibly separate from the manifest-authored prompt."""
    return (
        f"{prompt}\n\n[harness metadata: read-only]\n"
        f"HWSKILL_TEST_CONTEXT={context_path.absolute()}\n"
        f"HWSKILL_TEST_ARTIFACTS={artifact_dir.absolute()}\n"
        f"HWSKILL_TEST_WORKSPACE={workspace.absolute()}\n"
    )


def parse_agent_post_check(response: str) -> Literal["PASS", "FAIL", "BLOCKED"]:
    """Accept only the documented verdict object; extra fields are not a silent extension point."""
    try:
        data = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return "BLOCKED"
    if not isinstance(data, dict) or set(data) != {"result", "evidence"}:
        return "BLOCKED"
    evidence = data.get("evidence")
    if (
        data.get("result") not in {"pass", "fail"}
        or not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(item, str) or not item.strip() for item in evidence)
    ):
        return "BLOCKED"
    return "PASS" if data["result"] == "pass" else "FAIL"
