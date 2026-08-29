from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex

from .scopes import ScopeTarget, setup_state_path


START = "# >>> hwskill managed codex >>>"
END = "# <<< hwskill managed codex <<<"


@dataclass(frozen=True)
class SetupResult:
    config_path: Path
    changed: bool


def _codex_config_path(target: ScopeTarget) -> Path:
    if target.kind == "user":
        return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    assert target.project_root is not None
    return target.project_root / ".codex/config.toml"


def _runtime_scope_args(target: ScopeTarget) -> list[str]:
    if target.kind == "user":
        return ["--scope", "user"]
    assert target.project_root is not None
    return ["--scope", "project", "--project", str(target.project_root)]


def _managed_block(
    target: ScopeTarget,
    registry_root: Path,
    audit_path: Path | None = None,
) -> str:
    root = str(registry_root.resolve())
    scope_args = _runtime_scope_args(target)
    mcp_args = ["serve-mcp", "--repo-root", root, *scope_args]
    hook_args = [
        "hwskill", "adapter", "codex", "session-start",
        "--repo-root", root, *scope_args,
    ]
    if audit_path is not None:
        mcp_args.extend(("--audit-path", str(audit_path.resolve())))
        hook_args.extend(("--audit-path", str(audit_path.resolve())))
    hook_command = shlex.join(hook_args)
    return (
        f"{START}\n"
        "[mcp_servers.hwskill]\n"
        'command = "hwskill"\n'
        f"args = {json.dumps(mcp_args, ensure_ascii=False)}\n"
        "required = true\n\n"
        "[[hooks.SessionStart]]\n"
        'matcher = "startup|resume|clear|compact"\n'
        "[[hooks.SessionStart.hooks]]\n"
        'type = "command"\n'
        f"command = {json.dumps(hook_command, ensure_ascii=False)}\n"
        "additionalContextLimit = 5000\n"
        f"{END}\n"
    )


def setup_codex(
    target: ScopeTarget,
    registry_root: Path,
    audit_path: Path | None = None,
) -> SetupResult:
    config = _codex_config_path(target)
    config.parent.mkdir(parents=True, exist_ok=True)
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    block = _managed_block(target, registry_root, audit_path)
    if START in existing:
        before, rest = existing.split(START, 1)
        _, after = rest.split(END, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip("\n")
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    changed = updated != existing
    if changed:
        config.write_text(updated, encoding="utf-8")
    state = setup_state_path(target, "codex")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "schema_version": 1,
        "config": (
            str(config.relative_to(target.project_root))
            if target.project_root is not None
            else str(config)
        ),
        "managed_digest": "sha256:" + hashlib.sha256(block.encode()).hexdigest(),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return SetupResult(config, changed)


def _extract_managed_block(content: str) -> tuple[str, int, int]:
    if START not in content or END not in content:
        raise ValueError("hwskill managed Codex block is incomplete; refusing to edit")
    start = content.index(START)
    end = content.index(END, start) + len(END)
    if content[end:end + 1] == "\n":
        end += 1
    return content[start:end], start, end


def codex_setup_is_current(target: ScopeTarget) -> bool:
    config = _codex_config_path(target)
    state = setup_state_path(target, "codex")
    if not config.is_file() or not state.is_file():
        return False
    try:
        block, _, _ = _extract_managed_block(config.read_text(encoding="utf-8"))
        ownership = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(ownership, dict):
        return False
    actual_digest = "sha256:" + hashlib.sha256(block.encode()).hexdigest()
    expected_config = (
        str(config.relative_to(target.project_root))
        if target.project_root is not None
        else str(config)
    )
    return (
        ownership.get("config") == expected_config
        and ownership.get("managed_digest") == actual_digest
    )


def unsetup_codex(target: ScopeTarget) -> SetupResult:
    config = _codex_config_path(target)
    if not config.exists():
        return SetupResult(config, False)
    existing = config.read_text(encoding="utf-8")
    if START not in existing:
        return SetupResult(config, False)
    block, start, end = _extract_managed_block(existing)
    state = setup_state_path(target, "codex")
    if not state.is_file():
        raise ValueError("hwskill setup state is missing; refusing to edit")
    ownership = json.loads(state.read_text(encoding="utf-8"))
    if not isinstance(ownership, dict):
        raise ValueError("hwskill setup state is invalid; refusing to edit")
    actual_digest = "sha256:" + hashlib.sha256(block.encode()).hexdigest()
    if ownership.get("managed_digest") != actual_digest:
        raise ValueError("managed Codex block was modified outside hwskill; refusing to edit")
    before, after = existing[:start], existing[end:]
    updated = before.rstrip() + ("\n\n" if before.strip() and after.strip() else "") + after.lstrip("\n")
    config.write_text(updated, encoding="utf-8")
    state.unlink()
    return SetupResult(config, True)


from .json_configuration import (  # noqa: E402
    setup_claude_code,
    setup_opencode,
    unsetup_claude_code,
    unsetup_opencode,
)
