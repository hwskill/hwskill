"""Non-interactive host adapters for isolated Agent test actions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Callable, Literal, Protocol

from .eval_events import EventStreamNormalizer
from .hosts import canonical_host
from .test_artifacts import ActionResult, redact_text, redact_value, write_json, write_text
from .test_manifest import AgentAction, CommandAction
from .test_runner import (
    ActionContext,
    _after_workdir_opened,
    _agent_environment,
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
        anchored_cwd: str | None = None,
    ) -> tuple[str, ...]:
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

    def build_command(
        self,
        context: ActionContext,
        action: AgentAction | None = None,
        *,
        model: str,
        reasoning: str,
        anchored_cwd: str | None = None,
    ) -> tuple[str, ...]:
        action = _host_action(context, action)
        return (
            "codex", "exec", "--model", model,
            "-c", f'model_reasoning_effort="{reasoning}"',
            "--json", "--skip-git-repo-check", "--cd",
            anchored_cwd or str(_action_workdir(context, action)),
            action.prompt,
        )


class ClaudeCodeTestHost:
    host_id = "claude-code"

    def build_command(
        self,
        context: ActionContext,
        action: AgentAction | None = None,
        *,
        model: str,
        reasoning: str,
        anchored_cwd: str | None = None,
    ) -> tuple[str, ...]:
        action = _host_action(context, action)
        del context, reasoning, anchored_cwd
        return (
            "claude", "--print", "--output-format", "stream-json", "--verbose",
            "--model", model, action.prompt,
        )


class OpenCodeTestHost:
    host_id = "opencode"

    def build_command(
        self,
        context: ActionContext,
        action: AgentAction | None = None,
        *,
        model: str,
        reasoning: str,
        anchored_cwd: str | None = None,
    ) -> tuple[str, ...]:
        action = _host_action(context, action)
        del context, reasoning, anchored_cwd
        return ("opencode", "run", "--format", "json", "--model", model, action.prompt)


_HOSTS: dict[str, TestHost] = {
    "codex": CodexTestHost(),
    "claude-code": ClaudeCodeTestHost(),
    "opencode": OpenCodeTestHost(),
}

CredentialCheck = Callable[[str], bool]
CredentialUnavailableReason = Callable[[str], str]
SetupRunner = Callable[..., subprocess.CompletedProcess[str]]


def _credentials_unavailable(_host: str) -> bool:
    """No credential is assumed until the later setup service verifies one."""
    return False


class _AgentStreamCapture:
    """Store only redacted final text and normalized observable events."""

    def __init__(self, host: str, secret_values: tuple[str, ...]) -> None:
        self._normalizer = EventStreamNormalizer(host)
        self._secret_values = secret_values
        self._final_parts: list[str] = []
        self._stderr_parts: list[str] = []
        self._errors: list[Exception] = []
        self._lock = threading.Lock()

    def consume_stdout(self, line: str) -> None:
        try:
            value = json.loads(line)
            safe_value = redact_value(value, self._secret_values)
            if not isinstance(safe_value, dict):
                return
            final = _final_response_from_event(safe_value)
            with self._lock:
                if final:
                    self._final_parts.append(final)
                self._normalizer.consume(safe_value)
        except Exception as exc:  # malformed records must not retain or emit raw text
            with self._lock:
                self._errors.append(exc)

    def consume_stderr(self, line: str) -> None:
        with self._lock:
            self._stderr_parts.append(redact_text(line, self._secret_values))

    def persist(self, artifact_dir: Path, secret_values: tuple[str, ...]) -> None:
        with self._lock:
            if self._errors:
                raise ValueError("cannot normalize Agent event stream")
            events = self._normalizer.finish()
            final = "\n".join(self._final_parts)
            stderr = "".join(self._stderr_parts)
        event_text = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events)
        write_text(artifact_dir / "events.jsonl", event_text, secret_values)
        # stdout is intentionally the filtered observable stream, never raw JSONL.
        write_text(artifact_dir / "stdout.log", event_text, secret_values)
        write_text(artifact_dir / "stderr.log", stderr, secret_values)
        write_text(artifact_dir / "final-response.md", final, secret_values)


class AgentExecutor:
    """Run an Agent action without retaining raw host output or hidden reasoning."""

    def __init__(
        self,
        host: TestHost | None = None,
        *,
        credential_available: CredentialCheck = _credentials_unavailable,
        credential_unavailable_reason: CredentialUnavailableReason | None = None,
        setup_runner: SetupRunner = subprocess.run,
    ) -> None:
        self.host = host
        self._credential_available = credential_available
        self._credential_unavailable_reason = credential_unavailable_reason
        self._setup_runner = setup_runner

    def run(self, action: CommandAction | AgentAction, context: ActionContext) -> ActionResult:
        if not isinstance(action, AgentAction):
            return _blocked_result(action.action_id, context.artifact_dir, "agent executor cannot run a command action", context.environment)
        try:
            host = self.host or _HOSTS[canonical_host(context.environment.host)]
        except (KeyError, ValueError) as exc:
            return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)
        if not self._credential_available(host.host_id):
            reason = (
                self._credential_unavailable_reason(host.host_id)
                if self._credential_unavailable_reason is not None
                else f"credentials are unavailable for Agent host {host.host_id}"
            )
            return _blocked_result(
                action.action_id, context.artifact_dir,
                reason, context.environment,
            )
        capture = _AgentStreamCapture(host.host_id, context.environment.secret_values)
        try:
            self._setup(host.host_id, context)
            workdir_fd, cwd = _open_anchored_workdir(context.workspace, action.workdir)
            try:
                _after_workdir_opened(context.workspace, action.workdir)
                process = subprocess.Popen(
                    host.build_command(
                        context, action,
                        model=context.environment.model,
                        reasoning=context.environment.reasoning,
                        anchored_cwd=cwd,
                    ),
                    cwd=cwd,
                    env=_agent_environment(context.environment, context.workspace),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,
                    pass_fds=(workdir_fd,),
                )
            finally:
                # Codex inherits this descriptor for its own --cd resolution.
                os.close(workdir_fd)
            timed_out = _capture_process_output(process, capture, context.environment.timeout_seconds)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)

        try:
            capture.persist(context.artifact_dir, context.environment.secret_values)
        except ValueError as exc:
            return _blocked_result(action.action_id, context.artifact_dir, str(exc), context.environment)
        if timed_out:
            return self._blocked(action, context, host, "timeout")
        status: Literal["completed", "failed"] = "completed" if process.returncode == 0 else "failed"
        payload = {
            "action_id": action.action_id,
            "kind": "agent",
            "host": host.host_id,
            "model": context.environment.model,
            "reasoning": context.environment.reasoning,
            "workdir": cwd,
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

    def _blocked(self, action: AgentAction, context: ActionContext, host: TestHost, reason: str) -> ActionResult:
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


def _capture_process_output(
    process: subprocess.Popen[str],
    capture: _AgentStreamCapture,
    timeout_seconds: float,
) -> bool:
    """Drain both pipes concurrently so timeout handling cannot deadlock on full stderr."""
    assert process.stdout is not None
    assert process.stderr is not None
    readers = (
        threading.Thread(target=_read_lines, args=(process.stdout, capture.consume_stdout), daemon=True),
        threading.Thread(target=_read_lines, args=(process.stderr, capture.consume_stderr), daemon=True),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        process.wait()
    finally:
        for reader in readers:
            reader.join()
    return timed_out


def _read_lines(stream, consume: Callable[[str], None]) -> None:
    for line in iter(stream.readline, ""):
        consume(line)


def _final_response_from_event(event: dict[str, object]) -> str:
    if event.get("type") == "result" and isinstance(event.get("result"), str):
        return event["result"]
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
        return item["text"]
    message = event.get("message")
    if event.get("type") == "assistant" and isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "\n".join(
                block["text"] for block in content
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
            )
    part = event.get("part")
    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
        return part["text"]
    return ""


def _final_response(events) -> str:
    """Compatibility helper for focused final-answer classification tests."""
    return "\n".join(filter(None, (_final_response_from_event(event) for event in events)))


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
