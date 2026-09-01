from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml


class SourceManifestError(ValueError):
    pass


@dataclass(frozen=True)
class IgnoredSkill:
    path: str
    reason: str


@dataclass(frozen=True)
class ResolvedSourceSkill:
    path: str
    skill_id: str
    layer: str
    content_digest: str


@dataclass(frozen=True)
class SourceDefaults:
    namespace: str
    layer: str
    license: str


@dataclass(frozen=True)
class UpstreamConfig:
    repository: str
    track: str
    skills_path: str
    ignore: tuple[IgnoredSkill, ...]


@dataclass(frozen=True)
class UpstreamSource:
    source_id: str
    upstream: UpstreamConfig
    defaults: SourceDefaults
    resolved_revision: str
    skills: tuple[ResolvedSourceSkill, ...]


_SHA1_RE = re.compile(r"[0-9a-fA-F]{40}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SCP_GIT_URL_RE = re.compile(r"(?:[^@/:\s]+@)?[A-Za-z0-9][A-Za-z0-9.-]*:[^/\s].+")


def load_source_manifest(path: Path) -> UpstreamSource:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceManifestError(f"cannot read source manifest {path}: {exc}") from exc
    return _parse_source(data, path)


def write_source_manifest(path: Path, source: UpstreamSource) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 2,
        "source_id": source.source_id,
        "kind": "upstream",
        "upstream": {
            "repository": source.upstream.repository,
            "track": source.upstream.track,
            "skills_path": source.upstream.skills_path,
            "ignore": [
                {"path": ignored.path, "reason": ignored.reason}
                for ignored in source.upstream.ignore
            ],
        },
        "defaults": {
            "namespace": source.defaults.namespace,
            "layer": source.defaults.layer,
            "license": source.defaults.license,
        },
        "resolved": {
            "revision": source.resolved_revision,
            "skills": [
                {
                    "path": skill.path,
                    "id": skill.skill_id,
                    "layer": skill.layer,
                    "content_digest": skill.content_digest,
                }
                for skill in source.skills
            ],
        },
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_all_sources(repo_root: Path) -> tuple[UpstreamSource, ...]:
    sources_dir = repo_root / "sources"
    if not sources_dir.is_dir():
        return ()
    sources = tuple(load_source_manifest(path) for path in sorted(sources_dir.glob("*.yaml")))
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise SourceManifestError("duplicate source_id in source manifests")
    return sources


def _parse_source(data: Any, path: Path) -> UpstreamSource:
    root = _mapping(data, "source manifest")
    _exact_keys(root, {"schema_version", "source_id", "kind", "upstream", "defaults", "resolved"}, "source manifest")
    if root["schema_version"] != 2:
        raise SourceManifestError(f"{path}: schema_version must be 2")
    if _string(root["kind"], "kind") != "upstream":
        raise SourceManifestError(f"{path}: kind must be upstream")

    source_id = _string(root["source_id"], "source_id")
    upstream_data = _mapping(root["upstream"], "upstream")
    _exact_keys(upstream_data, {"repository", "track", "skills_path", "ignore"}, "upstream")
    repository = _string(upstream_data["repository"], "upstream.repository")
    if not _is_remote_git_url(repository):
        raise SourceManifestError(f"{path}: upstream.repository must be a remote Git URL")
    track = _string(upstream_data["track"], "upstream.track")
    if not _is_full_ref(track):
        raise SourceManifestError(
            f"{path}: upstream.track must be refs/heads/..., refs/tags/..., "
            "or a full 40-character Git revision"
        )
    skills_path = _safe_relative_path(upstream_data["skills_path"], "upstream.skills_path")
    ignored = tuple(_parse_ignored(item) for item in _collection(upstream_data["ignore"], "upstream.ignore"))

    defaults_data = _mapping(root["defaults"], "defaults")
    _exact_keys(defaults_data, {"namespace", "layer", "license"}, "defaults")
    defaults = SourceDefaults(
        namespace=_string(defaults_data["namespace"], "defaults.namespace"),
        layer=_string(defaults_data["layer"], "defaults.layer"),
        license=_string(defaults_data["license"], "defaults.license"),
    )

    resolved_data = _mapping(root["resolved"], "resolved")
    _exact_keys(resolved_data, {"revision", "skills"}, "resolved")
    revision = _string(resolved_data["revision"], "resolved.revision")
    if not _SHA1_RE.fullmatch(revision):
        raise SourceManifestError(f"{path}: resolved.revision must be a full 40-character Git revision")
    skills = tuple(_parse_resolved_skill(item) for item in _collection(resolved_data["skills"], "resolved.skills"))

    _validate_unique([skill.skill_id for skill in skills], "skill id")
    _validate_unique([skill.path for skill in skills], "skill path")
    _validate_unique([item.path for item in ignored], "ignored path")
    for skill in skills:
        for ignored_skill in ignored:
            if _paths_overlap(skill.path, ignored_skill.path):
                raise SourceManifestError(
                    f"{path}: resolved skill path and ignored path overlap: {skill.path}"
                )

    return UpstreamSource(
        source_id=source_id,
        upstream=UpstreamConfig(
            repository=repository,
            track=track,
            skills_path=skills_path,
            ignore=ignored,
        ),
        defaults=defaults,
        resolved_revision=revision,
        skills=skills,
    )


def _parse_ignored(data: Any) -> IgnoredSkill:
    item = _mapping(data, "upstream.ignore item")
    _exact_keys(item, {"path", "reason"}, "upstream.ignore item")
    return IgnoredSkill(
        path=_safe_relative_path(item["path"], "upstream.ignore path"),
        reason=_string(item["reason"], "upstream.ignore reason"),
    )


def _parse_resolved_skill(data: Any) -> ResolvedSourceSkill:
    item = _mapping(data, "resolved.skills item")
    _exact_keys(item, {"path", "id", "layer", "content_digest"}, "resolved.skills item")
    digest = _string(item["content_digest"], "resolved.skills content_digest")
    if not _SHA256_RE.fullmatch(digest):
        raise SourceManifestError("resolved.skills content_digest must be a sha256 digest")
    return ResolvedSourceSkill(
        path=_safe_relative_path(item["path"], "resolved.skills path"),
        skill_id=_string(item["id"], "resolved.skills id"),
        layer=_string(item["layer"], "resolved.skills layer"),
        content_digest=digest,
    )


def _mapping(data: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(data, dict):
        raise SourceManifestError(f"{label} must be a mapping")
    return data


def _collection(data: Any, label: str) -> list[Any]:
    if not isinstance(data, list):
        raise SourceManifestError(f"{label} must be a list")
    return data


def _exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = expected - data.keys()
    extra = data.keys() - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise SourceManifestError(f"{label} has " + "; ".join(details) + " fields")


def _string(data: Any, label: str) -> str:
    if not isinstance(data, str) or not data or data != data.strip():
        raise SourceManifestError(f"{label} must be a non-empty string")
    return data


def _safe_relative_path(data: Any, label: str) -> str:
    value = _string(data, label)
    if "\\" in value:
        raise SourceManifestError(f"{label} must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
        raise SourceManifestError(f"{label} must be a safe relative path")
    return value


def _is_remote_git_url(repository: str) -> bool:
    if _is_local_repository_path(repository):
        return False
    parsed = urlsplit(repository)
    if parsed.scheme in {"https", "ssh", "git"}:
        return bool(parsed.netloc and parsed.path and not parsed.query and not parsed.fragment)
    return bool(_SCP_GIT_URL_RE.fullmatch(repository))


def _is_local_repository_path(repository: str) -> bool:
    return repository.startswith(("/", "\\")) or bool(
        re.match(r"^[A-Za-z]:", repository)
    )


def _is_full_ref(track: str) -> bool:
    if _SHA1_RE.fullmatch(track):
        return True
    for prefix in ("refs/heads/", "refs/tags/"):
        if track.startswith(prefix):
            suffix = track.removeprefix(prefix)
            return bool(suffix) and all(part not in {"", ".", ".."} for part in suffix.split("/"))
    return False


def _validate_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise SourceManifestError(f"duplicate {label}")


def _paths_overlap(first: str, second: str) -> bool:
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")
