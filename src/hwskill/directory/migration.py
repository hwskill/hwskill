from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict, dataclass
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Any, Mapping

import yaml

from .entries import _is_safe_external_path, _is_safe_requested_ref, _url_is_http
from .yaml_io import _StrictSafeLoader


class MigrationSafetyError(RuntimeError):
    """The preview cannot proceed without following or replacing an unsafe path."""


@dataclass(frozen=True)
class MigrationException:
    skill_id: str
    code: str
    message: str


@dataclass(frozen=True)
class MigrationPreview:
    out_dir: Path
    candidate_ids: tuple[str, ...]
    unconvertible: tuple[MigrationException, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "out_dir": str(self.out_dir),
            "candidate_ids": list(self.candidate_ids),
            "unconvertible": [asdict(item) for item in self.unconvertible],
        }


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")
_LAYER = re.compile(r"^l[12]$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CONTENT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _open_directory_nofollow(path: Path) -> int:
    absolute = _absolute_lexical(path)
    fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        os.close(fd)
        raise MigrationSafetyError(f"unsafe or unavailable directory path: {absolute}") from exc


def _read_file_at(parent_fd: int, name: str, *, limit: int = 16 * 1024 * 1024) -> bytes:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise MigrationSafetyError(f"refusing unsafe source file {name}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise MigrationSafetyError(f"source file is not regular: {name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise MigrationSafetyError(f"source file exceeds {limit} bytes: {name}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _open_child_directory(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise MigrationSafetyError(f"refusing unsafe source directory {name}") from exc


def _load_inputs(repo_fd: int) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    registry_fd = _open_child_directory(repo_fd, "registry")
    try:
        raw_catalog = _read_file_at(registry_fd, "catalog.json")
    finally:
        os.close(registry_fd)
    try:
        catalog = json.loads(raw_catalog.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationSafetyError("legacy registry/catalog.json is not valid UTF-8 JSON") from exc
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 2 or not isinstance(catalog.get("skills"), list):
        raise MigrationSafetyError("legacy catalog must be a schema_version 2 object with skills")

    sources: dict[str, dict[str, Any]] = {}
    try:
        sources_fd = os.open("sources", _DIRECTORY_FLAGS, dir_fd=repo_fd)
    except FileNotFoundError:
        sources_fd = None
    except OSError as exc:
        raise MigrationSafetyError("refusing unsafe source directory sources") from exc
    if sources_fd is not None:
        try:
            for name in sorted(os.listdir(sources_fd)):
                if not name.endswith((".yaml", ".yml")) or "/" in name:
                    continue
                raw = _read_file_at(sources_fd, name)
                try:
                    value = yaml.load(raw.decode("utf-8"), Loader=_StrictSafeLoader)
                except (UnicodeDecodeError, yaml.YAMLError):
                    continue
                key = name.rsplit(".", 1)[0]
                if key in sources:
                    sources[key] = {}
                elif isinstance(value, dict):
                    sources[key] = value
        finally:
            os.close(sources_fd)
    return catalog, sources


def _safe_hosted_path(value: object) -> bool:
    if not isinstance(value, str) or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    return parts[0] == "skills-src" and all(part not in {"", ".", ".."} for part in parts)


def _candidate_base(skill: Mapping[str, Any]) -> dict[str, Any]:
    license_value = skill.get("license")
    license_data = (
        {"status": "known", "identifier": license_value}
        if isinstance(license_value, str) and license_value not in {"", "mixed-or-unspecified", "unknown"}
        else {"status": "unknown"}
    )
    return {
        "schema_version": 1,
        "id": skill["id"],
        "name": skill["name"],
        "summary": skill["description"],
        "layer": skill["layer"],
        "purposes": [],
        "examples": [],
        "compatibility": {"agents": [], "systems": [], "requirements": []},
        "license": license_data,
        "lifecycle": "active",
        "lifecycle_reason": None,
        "replacement_id": None,
    }


def _hosted_source_complete(repo_fd: int, value: str) -> bool:
    directory_fd = os.dup(repo_fd)
    try:
        for part in value.split("/"):
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        _read_file_at(directory_fd, "SKILL.md")
        return True
    except (OSError, MigrationSafetyError):
        return False
    finally:
        os.close(directory_fd)


def _source_snapshot_matches(
    source_key: str,
    source: Mapping[str, Any],
    skill: Mapping[str, Any],
) -> bool:
    if set(source) != {"schema_version", "source_id", "kind", "upstream", "defaults", "resolved"}:
        return False
    if source.get("schema_version") != 2 or source.get("kind") != "upstream":
        return False
    if source.get("source_id") != source_key or skill.get("source_id") != source_key:
        return False
    upstream = source.get("upstream")
    defaults = source.get("defaults")
    resolved = source.get("resolved")
    if not isinstance(upstream, Mapping) or set(upstream) != {"repository", "track", "skills_path", "ignore"}:
        return False
    if (
        not _url_is_http(upstream.get("repository"))
        or not _is_safe_requested_ref(upstream.get("track"))
        or not _is_safe_external_path(upstream.get("skills_path"))
    ):
        return False
    if not isinstance(defaults, Mapping) or set(defaults) != {"namespace", "layer", "license"}:
        return False
    if not all(isinstance(defaults.get(field), str) and defaults[field] for field in ("namespace", "layer", "license")):
        return False
    ignored = upstream.get("ignore")
    if not isinstance(ignored, list) or any(
        not isinstance(item, Mapping)
        or set(item) != {"path", "reason"}
        or not _is_safe_external_path(item.get("path"))
        or not isinstance(item.get("reason"), str)
        or not item["reason"]
        for item in ignored
    ):
        return False
    if not isinstance(resolved, Mapping) or set(resolved) != {"revision", "skills"}:
        return False
    revision = resolved.get("revision")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision) or revision != skill.get("revision"):
        return False
    resolved_skills = resolved.get("skills")
    if not isinstance(resolved_skills, list):
        return False
    skill_ids: set[str] = set()
    skill_paths: set[str] = set()
    for item in resolved_skills:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"path", "id", "layer", "content_digest"}
            or not isinstance(item.get("id"), str)
            or not _SKILL_ID.fullmatch(item["id"])
            or not isinstance(item.get("layer"), str)
            or not _LAYER.fullmatch(item["layer"])
            or not _is_safe_external_path(item.get("path"))
            or not isinstance(item.get("content_digest"), str)
            or not _CONTENT_DIGEST.fullmatch(item["content_digest"])
            or item["id"] in skill_ids
            or item["path"] in skill_paths
        ):
            return False
        skill_ids.add(item["id"])
        skill_paths.add(item["path"])
    matches = [item for item in resolved_skills if isinstance(item, Mapping) and item.get("id") == skill["id"]]
    if len(matches) != 1:
        return False
    resolved_skill = matches[0]
    if set(resolved_skill) != {"path", "id", "layer", "content_digest"}:
        return False
    if (
        resolved_skill.get("layer") != skill.get("layer")
        or resolved_skill.get("content_digest") != skill.get("content_digest")
        or not isinstance(resolved_skill.get("content_digest"), str)
        or not _CONTENT_DIGEST.fullmatch(resolved_skill["content_digest"])
        or not _is_safe_external_path(resolved_skill.get("path"))
    ):
        return False
    return skill.get("path") == f"skills-src/{skill['layer']}/{skill['id']}"


def _legacy_install_method(skill: Mapping[str, Any]) -> str | None:
    value = skill.get("install")
    if value is None:
        return "directory" if skill.get("source_kind") == "manual" else "upstream"
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("method"), str):
        return value["method"]
    return None


def _convert_skill(
    skill: object,
    sources: Mapping[str, Mapping[str, Any]],
    repo_fd: int,
) -> tuple[dict[str, Any] | None, MigrationException | None]:
    if not isinstance(skill, Mapping) or not isinstance(skill.get("id"), str):
        return None, MigrationException("<unknown>", "invalid-record", "legacy skill is not an identifiable object")
    skill_id = skill["id"]
    layer = skill.get("layer")
    required_strings = ("name", "description", "path")
    if (
        not _SKILL_ID.fullmatch(skill_id)
        or not isinstance(layer, str)
        or not _LAYER.fullmatch(layer)
        or any(not isinstance(skill.get(field), str) or not skill[field] for field in required_strings)
    ):
        return None, MigrationException(skill_id, "invalid-record", "legacy skill metadata or output path is unsafe")

    source_kind = skill.get("source_kind")
    expected_install = "directory" if source_kind == "manual" else "upstream" if source_kind == "upstream" else None
    if expected_install is None:
        return None, MigrationException(skill_id, "unknown-locator", f"unsupported legacy source kind {source_kind!r}")
    if _legacy_install_method(skill) != expected_install:
        return None, MigrationException(skill_id, "unknown-install", "legacy install method has no safe deterministic mapping")

    entry = _candidate_base(skill)
    if source_kind == "manual":
        path = skill["path"]
        if (
            not _safe_hosted_path(path)
            or path != f"skills-src/{skill['layer']}/{skill_id}"
            or skill.get("source_id") is not None
            or skill.get("revision") != "manual"
            or not isinstance(skill.get("content_digest"), str)
            or not _CONTENT_DIGEST.fullmatch(skill["content_digest"])
        ):
            return None, MigrationException(skill_id, "unknown-locator", "manual skill path is not a safe hosted path")
        if not _hosted_source_complete(repo_fd, path):
            return None, MigrationException(skill_id, "missing-hosted-source", "manual hosted source lacks a regular SKILL.md")
        entry["source"] = {"kind": "hosted", "path": path}
        entry["install"] = {"method": "directory", "default_scope": "project"}
    else:
        source_id = skill.get("source_id")
        source = sources.get(source_id) if isinstance(source_id, str) else None
        if source is not None and not _source_snapshot_matches(source_id, source, skill):
            return None, MigrationException(skill_id, "snapshot-mismatch", "legacy source snapshot does not match catalog evidence")
        upstream = source.get("upstream") if isinstance(source, Mapping) else None
        resolved = source.get("resolved") if isinstance(source, Mapping) else None
        resolved_skills = resolved.get("skills") if isinstance(resolved, Mapping) else None
        resolved_skill = next(
            (item for item in resolved_skills if isinstance(item, Mapping) and item.get("id") == skill_id),
            None,
        ) if isinstance(resolved_skills, list) else None
        if not isinstance(upstream, Mapping) or not isinstance(resolved_skill, Mapping):
            return None, MigrationException(skill_id, "unknown-locator", "upstream source snapshot cannot locate this skill")
        repository = upstream.get("repository")
        requested_ref = upstream.get("track")
        skills_path = upstream.get("skills_path")
        relative_path = resolved_skill.get("path")
        path = (
            f"{skills_path}/{relative_path}"
            if isinstance(skills_path, str) and skills_path and isinstance(relative_path, str) and relative_path
            else ""
        )
        if (
            not _url_is_http(repository)
            or not _is_safe_requested_ref(requested_ref)
            or not _is_safe_external_path(path)
        ):
            return None, MigrationException(skill_id, "unknown-locator", "upstream source locator is unsafe or incomplete")
        entry["source"] = {
            "kind": "external",
            "publicity": "unknown",
            "locator": {
                "type": "git",
                "repository": repository,
                "path": path,
                "requested_ref": requested_ref,
            },
        }
        entry["install"] = {"method": "upstream", "default_scope": "project"}
    return {
        "schema_version": 1,
        "entry": entry,
        "migration_review_required": [
            "purposes",
            "examples",
            "compatibility",
            *(["source.publicity"] if skill.get("source_kind") == "upstream" else ["source.content"]),
        ],
        "legacy_evidence": {
            "revision": skill.get("revision"),
            "content_digest": skill.get("content_digest"),
        },
    }, None


def _mkdir_child(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def _write_atomic_file(root_fd: int, relative: PurePosixPath, body: bytes) -> None:
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise MigrationSafetyError(f"unsafe preview output path: {relative}")
    directory_fd = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            next_fd = _mkdir_child(directory_fd, part)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            relative.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            view = memoryview(body)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise MigrationSafetyError("atomic no-replace preview publication is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1) == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise MigrationSafetyError(f"preview output already exists: {destination}")
    raise MigrationSafetyError(f"atomic preview publication failed: {os.strerror(error)}")


def _remove_tree_contents(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                _remove_tree_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _cleanup_owned_stage(parent_fd: int, stage_fd: int, stage_name: str) -> None:
    """Clear only the owned inode; never unlink a reusable directory name."""
    try:
        _remove_tree_contents(stage_fd)
    finally:
        os.close(stage_fd)
    os.fsync(parent_fd)


def _verify_open_directory(path: Path, expected_fd: int) -> None:
    reopened = _open_directory_nofollow(path)
    try:
        expected = os.fstat(expected_fd)
        actual = os.fstat(reopened)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise MigrationSafetyError(f"directory namespace changed during preview: {path}")
    finally:
        os.close(reopened)


def _verify_published_output(path: Path, parent_fd: int, expected: os.stat_result) -> None:
    try:
        _verify_open_directory(path.parent, parent_fd)
        reopened_parent = _open_directory_nofollow(path.parent)
        try:
            published_fd = os.open(path.name, _DIRECTORY_FLAGS, dir_fd=reopened_parent)
            try:
                actual = os.fstat(published_fd)
            finally:
                os.close(published_fd)
        finally:
            os.close(reopened_parent)
    except (OSError, MigrationSafetyError) as exc:
        raise MigrationSafetyError("namespace-uncertain: configured output parent changed after publication") from exc
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise MigrationSafetyError("namespace-uncertain: configured output does not identify the published inode")
    try:
        _verify_open_directory(path.parent, parent_fd)
    except MigrationSafetyError as exc:
        raise MigrationSafetyError("namespace-uncertain: configured output parent changed after verification") from exc


def migration_preview(repo_root: Path | str, out_dir: Path | str) -> MigrationPreview:
    raw_repository = Path(repo_root)
    raw_output = Path(out_dir)
    if ".." in raw_repository.parts or ".." in raw_output.parts:
        raise MigrationSafetyError("repository and output paths must not contain parent traversal")
    repository = _absolute_lexical(raw_repository)
    output = _absolute_lexical(raw_output)
    try:
        common = Path(os.path.commonpath((repository, output)))
    except ValueError as exc:
        raise MigrationSafetyError("repository and output paths cannot be compared safely") from exc
    if common in {repository, output}:
        raise MigrationSafetyError("preview output must not overlap the source repository")
    if os.path.lexists(output):
        raise MigrationSafetyError(f"preview output already exists: {output}")

    repo_fd = _open_directory_nofollow(repository)
    parent_fd = _open_directory_nofollow(output.parent)
    stage_name = f".{output.name}.staging-{secrets.token_hex(12)}"
    stage_path = output.parent / stage_name
    stage_fd: int | None = None
    renamed = False
    try:
        catalog, sources = _load_inputs(repo_fd)
        candidates: list[tuple[str, dict[str, Any]]] = []
        exceptions: list[MigrationException] = []
        for skill in catalog["skills"]:
            candidate, issue = _convert_skill(skill, sources, repo_fd)
            if issue is not None:
                exceptions.append(issue)
            elif candidate is not None:
                candidates.append((candidate["entry"]["id"], candidate))
        candidates.sort(key=lambda item: item[0])
        exceptions.sort(key=lambda item: (item.skill_id, item.code))

        os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        stage_fd = os.open(stage_name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        for skill_id, candidate in candidates:
            layer = candidate["entry"]["layer"]
            identifier_parts = skill_id.split("/")
            identifier_parts[-1] += ".json"
            relative = PurePosixPath("candidates", "entries", layer, *identifier_parts)
            _write_atomic_file(
                stage_fd,
                relative,
                (json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            )
        exception_document = {
            "schema_version": 1,
            "items": [asdict(item) for item in exceptions],
        }
        _write_atomic_file(
            stage_fd,
            PurePosixPath("unconvertible.json"),
            (json.dumps(exception_document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        manifest = {
            "schema_version": 1,
            "candidate_ids": [skill_id for skill_id, _ in candidates],
            "unconvertible_count": len(exceptions),
        }
        _write_atomic_file(
            stage_fd,
            PurePosixPath("preview.json"),
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        os.fsync(stage_fd)
        _verify_open_directory(output.parent, parent_fd)
        staged_identity = os.fstat(stage_fd)
        _rename_noreplace(parent_fd, stage_name, output.name)
        renamed = True
        os.fsync(parent_fd)
        _verify_published_output(output, parent_fd, staged_identity)
        stage_path = output
        return MigrationPreview(output, tuple(item[0] for item in candidates), tuple(exceptions))
    finally:
        if stage_fd is not None:
            if stage_path != output and not renamed:
                owned_stage_fd = stage_fd
                stage_fd = None
                _cleanup_owned_stage(parent_fd, owned_stage_fd, stage_name)
            else:
                os.close(stage_fd)
        os.close(repo_fd)
        os.close(parent_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a non-destructive legacy directory migration preview.")
    parser.add_argument("repo_root")
    parser.add_argument("out_dir")
    args = parser.parse_args(argv)
    try:
        preview = migration_preview(args.repo_root, args.out_dir)
    except (MigrationSafetyError, OSError) as exc:
        print(json.dumps({"schema_version": 1, "result": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"result": "ok", **preview.to_dict()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
