#!/usr/bin/env python3
"""Run one isolated, version-bound directory installation check.

The command performs no network acquisition and never executes skill files.
External sources must already be present as a clean local Git checkout.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit

from hwskill.directory.digest import canonical_json
from hwskill.directory.entries import _normalise_repository
from hwskill.verification.installer import (
    AcquisitionRequired,
    InstallBlocked,
    InstallConflict,
    InstallationError,
    InstallInputError,
    install_reviewed_directory,
    reviewed_directory_digest,
)
from hwskill.verification.models import VerificationReportError, bind_install_material
from hwskill.verification.report import ReportStore, build_installation_report
from hwskill.verification.secure_fs import UnsafePathError, open_directory_chain


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an isolated skill installation verification.")
    parser.add_argument("--published-root", type=Path, required=True, help="Task2 generated catalog directory")
    parser.add_argument("--skill-id", required=True)
    parser.add_argument("--source-dir", type=Path, required=True, help="Already acquired, reviewed full skill directory")
    parser.add_argument("--target-root", type=Path, required=True, help="Isolated project or HOME skill root")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--host-version", required=True)
    parser.add_argument("--runner-identity", required=True)
    return parser.parse_args()


def _read_inputs(published_root: Path, skill_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identity_path = PurePosixPath(skill_id)
    if not skill_id or identity_path.is_absolute() or any(part in {"", ".", ".."} for part in identity_path.parts):
        raise VerificationReportError("skill_id is not a safe relative path")
    catalog = json.loads((published_root / "catalog.json").read_text(encoding="utf-8"))
    item = next((entry for entry in catalog.get("entries", []) if entry.get("entry", {}).get("id") == skill_id), None)
    if item is None:
        raise VerificationReportError(f"skill_id is absent from catalog: {skill_id}")
    install_path = published_root / "skills" / Path(*skill_id.split("/")) / "install.json"
    return dict(item), json.loads(install_path.read_text(encoding="utf-8"))


def _report_material(skill_id: str, item: Mapping[str, Any] | None, install: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a schema-valid failure identity without claiming successful binding."""
    identity = item.get("source_identity") if isinstance(item, Mapping) else None
    if isinstance(identity, Mapping) and identity.get("kind") in {"hosted", "external"} and isinstance(identity.get("identity"), str) and identity.get("identity"):
        safe_identity = {
            "kind": identity["kind"],
            "identity": identity["identity"],
            "requested_ref": identity.get("requested_ref") if isinstance(identity.get("requested_ref"), str) else None,
            "resolved_revision": identity.get("resolved_revision") if isinstance(identity.get("resolved_revision"), str) else None,
            "content_digest": identity.get("content_digest") if isinstance(identity.get("content_digest"), str) else None,
        }
    else:
        safe_identity = {"kind": "hosted", "identity": f"unbound:{skill_id}", "resolved_revision": None, "content_digest": None}
    entry_digest = item.get("entry_digest") if isinstance(item, Mapping) else None
    claimed_install_digest = install.get("install_digest") if isinstance(install, Mapping) else None
    return {
        "skill_id": skill_id,
        "entry_digest": entry_digest if isinstance(entry_digest, str) and entry_digest else "unavailable",
        "install_digest": claimed_install_digest if isinstance(claimed_install_digest, str) and claimed_install_digest else "unavailable",
        "source_identity": safe_identity,
    }


def _git(source_fd: int, *arguments: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", f"/proc/self/fd/{source_fd}", *arguments],
        text=not binary,
        capture_output=True,
        check=False,
        pass_fds=(source_fd,),
    )
    if completed.returncode:
        raise VerificationReportError("external source must be a clean local Git checkout")
    return completed.stdout if binary else completed.stdout.strip()


def _normalise_safe_repository(value: object) -> str:
    if not isinstance(value, str):
        raise VerificationReportError("external repository URL is missing")
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise VerificationReportError("external repository URL is unsafe")
    return _normalise_repository(value)


_HOSTS = {
    "codex": ("codex", (".codex", "skills")),
    "claude-code": ("claude", (".claude", "skills")),
    "opencode": ("opencode", (".opencode", "skills")),
}


def _verify_host(host: str, claimed_version: str, target_root: Path, forbidden_roots: tuple[Path, ...]) -> None:
    configuration = _HOSTS.get(host)
    if configuration is None:
        raise VerificationReportError(f"unsupported host identity: {host}")
    executable_name, target_suffix = configuration
    if tuple(target_root.absolute().parts[-len(target_suffix):]) != target_suffix:
        raise VerificationReportError(f"target_root does not match the isolated {host} skill directory")
    executable = shutil.which(executable_name)
    if not executable:
        raise VerificationReportError(f"host executable is unavailable: {executable_name}")
    executable_path = Path(executable).resolve()
    for root in forbidden_roots:
        boundary = root.resolve(strict=False)
        if executable_path == boundary or boundary in executable_path.parents:
            raise VerificationReportError("host executable cannot come from source, published, target, or report directories")
    completed = subprocess.run([str(executable_path), "--version"], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise VerificationReportError(f"host version probe failed: {host}")
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"(?<![0-9A-Za-z])v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)", output)
    if match is None:
        raise VerificationReportError(f"host version probe returned no parseable version: {host}")
    if match.group(1) != claimed_version:
        raise VerificationReportError(f"host version mismatch: claimed {claimed_version}, detected {match.group(1)}")


def _safe_extract_git_archive(archive: bytes, destination: Path) -> None:
    """Extract only regular files/directories using APIs available in Python 3.10."""
    if destination.is_symlink():
        raise ValueError("Git archive destination cannot be a symlink")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise ValueError("Git archive destination must be an empty directory")
    else:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    seen: set[str] = set()
    directory_modes: list[tuple[Path, int]] = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle:
            relative = PurePosixPath(member.name)
            if not member.name or not relative.parts or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise ValueError(f"unsafe Git archive member path: {member.name!r}")
            normalized = relative.as_posix()
            if normalized in seen:
                raise ValueError(f"duplicate Git archive member: {normalized}")
            seen.add(normalized)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory_modes.append((target, stat.S_IMODE(member.mode) & 0o777))
                continue
            if not member.isreg():
                raise ValueError(f"Git archive member is not a regular file or directory: {normalized}")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise ValueError(f"Git archive member has no file body: {normalized}")
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, stat.S_IMODE(member.mode) & 0o777)
            try:
                while chunk := extracted.read(1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(descriptor, view):]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
                extracted.close()
        for directory, mode in reversed(directory_modes):
            directory.chmod(mode)


def _acquire_external(material: dict[str, Any], acquired_directory: Path, snapshot_root: Path) -> tuple[dict[str, Any], Path]:
    try:
        source_fd = open_directory_chain(acquired_directory, create=False)
    except (FileNotFoundError, UnsafePathError, OSError) as error:
        raise VerificationReportError("external source must be a real directory with no symlink ancestors") from error
    try:
        install_source = material["install_json"]["source"]
        prefix = str(_git(source_fd, "rev-parse", "--show-prefix"))
        relative = prefix.rstrip("/") or "."
        if relative != install_source.get("path"):
            raise VerificationReportError("source directory does not match install locator path")
        if _git(source_fd, "status", "--porcelain", "--untracked-files=all"):
            raise VerificationReportError("external checkout is not clean")
        actual_origin = _normalise_safe_repository(_git(source_fd, "remote", "get-url", "origin"))
        expected_origin = _normalise_safe_repository(install_source.get("repository"))
        if actual_origin != expected_origin:
            raise VerificationReportError("checkout origin does not match install repository")
        requested_ref = install_source.get("requested_ref")
        if not isinstance(requested_ref, str) or not requested_ref or requested_ref.startswith("-"):
            raise VerificationReportError("external requested_ref is missing or unsafe")
        acquired_revision = str(_git(source_fd, "rev-parse", "HEAD"))
        requested_revision = str(_git(source_fd, "rev-parse", "--verify", f"{requested_ref}^{{commit}}"))
        if requested_revision != acquired_revision:
            raise VerificationReportError("local checkout HEAD does not match requested install revision")
        archive_target = acquired_revision if relative == "." else f"{acquired_revision}:{relative}"
        archive = _git(source_fd, "archive", "--format=tar", archive_target, binary=True)
    finally:
        os.close(source_fd)
    _safe_extract_git_archive(archive, snapshot_root)
    content_digest = reviewed_directory_digest(snapshot_root)
    resolved = dict(material)
    resolved["source_identity"] = {
        **material["source_identity"],
        "resolved_revision": acquired_revision,
        "content_digest": content_digest,
    }
    return resolved, snapshot_root


def _stages(metadata: str, acquisition: str, installation: str, reason: str) -> dict[str, dict[str, str]]:
    return {
        "metadata": {"result": metadata, "reason": reason if metadata != "pass" else "bound metadata checked"},
        "acquisition": {"result": acquisition, "reason": reason if acquisition != "pass" else "reviewed local source acquired"},
        "installation": {"result": installation, "reason": reason},
        "behavior": {"result": "not_run", "reason": "no agent behavior credentials or task were supplied" if installation == "pass" else "installation did not complete"},
    }


def _emit_report(args: argparse.Namespace, material: Mapping[str, Any], stages: Mapping[str, Mapping[str, str]]) -> int:
    installation = stages["installation"]
    report = build_installation_report(
        material=material,
        host=args.host,
        host_version=args.host_version,
        runner_identity=args.runner_identity,
        installation_result=installation["result"],
        installation_reason=installation["reason"],
        stages=stages,
    )
    ReportStore(args.report_dir).write(report)
    sys.stdout.buffer.write(canonical_json(report))
    return 0 if installation["result"] == "pass" else 1


def main() -> int:
    args = _arguments()
    item: dict[str, Any] | None = None
    install: dict[str, Any] | None = None
    try:
        item, install = _read_inputs(args.published_root, args.skill_id)
        material = bind_install_material(item, install)
    except (OSError, ValueError, VerificationReportError) as error:
        fallback = _report_material(args.skill_id, item, install)
        return _emit_report(args, fallback, _stages("fail", "not_run", "not_run", str(error)))

    if material.get("lifecycle") == "withdrawn":
        reason = "withdrawn skills cannot be newly installed"
        return _emit_report(args, material, _stages("pass", "not_run", "blocked", reason))

    try:
        _verify_host(
            args.host,
            args.host_version,
            args.target_root,
            (args.source_dir, args.published_root, args.target_root, args.report_dir),
        )
    except (OSError, ValueError, VerificationReportError) as error:
        return _emit_report(args, material, _stages("pass", "not_run", "blocked", str(error)))

    source = args.source_dir
    snapshot: tempfile.TemporaryDirectory[str] | None = None
    try:
        if material["source_identity"]["kind"] == "hosted":
            try:
                actual_digest = reviewed_directory_digest(source)
                if actual_digest != material["source_identity"].get("content_digest"):
                    raise VerificationReportError("hosted source does not match reviewed content_digest")
            except (OSError, ValueError, VerificationReportError, InstallInputError) as error:
                return _emit_report(args, material, _stages("pass", "fail", "not_run", str(error)))
        else:
            snapshot = tempfile.TemporaryDirectory(prefix="hwskill-git-snapshot-")
            try:
                material, source = _acquire_external(material, source, Path(snapshot.name))
            except (OSError, ValueError, VerificationReportError, InstallInputError) as error:
                return _emit_report(args, material, _stages("pass", "fail", "not_run", str(error)))
        try:
            result = install_reviewed_directory(material, source, args.target_root)
        except (InstallInputError, OSError) as error:
            if isinstance(error, AcquisitionRequired):
                acquisition, installation = "blocked", "not_run"
            elif isinstance(error, InstallConflict):
                acquisition, installation = "pass", "fail"
            elif isinstance(error, InstallBlocked):
                acquisition, installation = "not_run", "blocked"
            else:
                acquisition, installation = "pass", "fail"
            return _emit_report(args, material, _stages("pass", acquisition, installation, str(error)))
        reason = f"{result.reason}; target={result.target}; idempotent={result.idempotent}"
        return _emit_report(args, material, _stages("pass", "pass", "pass", reason))
    finally:
        if snapshot is not None:
            snapshot.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
