from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .digest import canonical_json, sha256_bytes
from .entries import _source_identity, validate_repository
from .hosted_content import copy_hosted_content, hosted_directory
from .models import DirectoryIssue
from .yaml_io import load_yaml


@dataclass(frozen=True)
class BuildResult:
    input_digest: str
    source_commit: str | None
    output_dir: Path
    entry_count: int
    recommendation_count: int


class BuildInputError(ValueError):
    def __init__(self, issue: DirectoryIssue):
        super().__init__(issue.message)
        self.issue = issue


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(data))


def _identity(entry: dict[str, Any], root: Path, source_commit: str | None = None) -> dict[str, Any]:
    source = entry["source"]
    if source["kind"] == "hosted":
        source_root = hosted_directory(root, source["path"])
        from .hosted_content import directory_digest
        return {"kind": "hosted", "identity": _source_identity(entry), "resolved_revision": source_commit, "content_digest": directory_digest(source_root)}
    locator = source["locator"]
    return {"kind": "external", "identity": _source_identity(entry), "requested_ref": locator.get("requested_ref"), "resolved_revision": None, "content_digest": None}


def _summary() -> dict[str, dict[str, Any]]:
    stage = {"result": "not_run", "report_id": None, "executed_at": None}
    return {name: dict(stage) for name in ("metadata", "acquisition", "installation", "behavior")}


def _normalized(entry: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    source = {"kind": entry["source"]["kind"], "identity": identity["identity"]}
    normalized = {
        "schema_version": 1, "id": entry["id"], "name": entry["name"], "summary": entry["summary"],
        "layer": entry["layer"], "purposes": entry["purposes"], "examples": entry["examples"],
        "source": source,
        "install": {"method": entry["install"]["method"], "default_scope": entry["install"]["default_scope"], "instructions_url": entry["install"].get("instructions_url")},
        "compatibility": entry["compatibility"], "license": entry["license"],
        "lifecycle": entry.get("lifecycle", "active"), "lifecycle_reason": entry.get("lifecycle_reason"), "replacement_id": entry.get("replacement_id"),
    }
    for field in ("owner", "keywords", "limitations"):
        if field in entry:
            normalized[field] = entry[field]
    return normalized


def _install_markdown(entry: dict[str, Any], identity: dict[str, Any]) -> str:
    install = entry["install"]
    source = entry["source"]
    if source["kind"] == "external":
        locator = source["locator"]
        url = locator.get("repository", locator.get("url"))
        source_lines = [str(url), f"路径：{locator.get('path', '不适用')}", f"请求版本：{locator.get('requested_ref', locator.get('version_note', 'unknown'))}", "解析提交：未解析（验证阶段记录实际 commit）"]
    else:
        source_lines = [source["path"]]
    lines = [f"# {entry['name']} 安装说明", "", f"技能 ID：`{entry['id']}`", f"安装方式：{install['method']}", f"默认范围：{install['default_scope']}", "", "## 来源", "", *source_lines, "", "## 验证状态", "", "安装与行为验证：not_run（本构建未执行安装或行为测试）。", ""]
    if install["method"] == "unknown":
        lines.extend(["未提供可执行安装命令；请按来源人工确认安装方式。", ""])
    elif install.get("instructions_url"):
        lines.extend([f"上游指引：{install['instructions_url']}", ""])
    return "\n".join(lines)


def _install_data(entry: dict[str, Any], entry_digest: str, identity: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    source = entry["source"]
    if source["kind"] == "external":
        locator = source["locator"]
        public_source = {"kind": "external", "repository": locator.get("repository", locator.get("url")), "path": locator.get("path"), "requested_ref": locator.get("requested_ref"), "resolved_revision": None}
    else:
        public_source = {"kind": "hosted", "path": source["path"], "resolved_revision": identity["resolved_revision"]}
    return {"schema_version": 1, "skill_id": entry["id"], "entry_digest": entry_digest, "source": public_source, "install": {"method": entry["install"]["method"], "default_scope": entry["install"]["default_scope"], "instructions_url": entry["install"].get("instructions_url")}, "verification_summary": summary}


def _assert_safe_output(root: Path, out_dir: Path) -> Path:
    output = out_dir.resolve()
    if output == root or output in root.parents:
        raise BuildInputError(DirectoryIssue("--out", "out", "unsafe-output-path", "error", "Output path overlaps repository source material.", "Choose a separate derived output directory."))
    sources = [root / "entries", root / "recommendations", root / "curation", root / "skills-src", root / "schemas", root / "templates", root / "CONTRIBUTING.md", root / "docs/guides/agent-contribution.md"]
    if any(output == source or output in source.parents or source in output.parents for source in sources):
        raise BuildInputError(DirectoryIssue("--out", "out", "unsafe-output-path", "error", "Output path overlaps repository source material.", "Choose a separate derived output directory."))
    return output


def _copy_public_resources(root: Path, output: Path) -> None:
    schemas = files("hwskill.directory").joinpath("schemas")
    schema_output = output / "schemas"
    schema_output.mkdir(parents=True, exist_ok=True)
    for resource in schemas.iterdir():
        if resource.name.endswith(".schema.json"):
            schema_output.joinpath(resource.name).write_bytes(resource.read_bytes())
    for relative in (Path("docs/guides/agent-contribution.md"), Path("CONTRIBUTING.md")):
        source = root / relative
        if source.is_file():
            target = output / ("contribute/agent.md" if relative.name == "agent-contribution.md" else relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def build_repository(repo_root: Path, out_dir: Path) -> BuildResult:
    """Validate and atomically replace a deterministic public directory."""
    root = repo_root.resolve()
    report = validate_repository(root)
    if report.result != "pass":
        raise ValueError("cannot build an invalid directory")
    output = _assert_safe_output(root, out_dir)
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=parent))
    try:
        entries: list[dict[str, Any]] = []
        for path in sorted((root / "entries").rglob("*.yaml")):
            entry = load_yaml(path)
            if entry["id"] not in report.publishable_entry_ids:
                continue
            identity = _identity(entry, root, report.source_commit)
            normalized = _normalized(entry, identity)
            entry_digest = sha256_bytes(canonical_json(normalized))
            summary = _summary()
            capability = "disabled" if normalized["lifecycle"] == "withdrawn" else "guidance_only" if entry["install"]["method"] == "unknown" else "installable"
            entries.append({"entry": normalized, "entry_digest": entry_digest, "source_identity": identity, "lifecycle": normalized["lifecycle"], "install_capability": capability, "verification_summary": summary})
            skill_dir = staging / "skills" / entry["id"]
            install_data = _install_data(entry, entry_digest, identity, summary)
            install_data["install_digest"] = sha256_bytes(canonical_json(install_data))
            _write_json(skill_dir / "install.json", install_data)
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "install.md").write_text(_install_markdown(entry, identity), encoding="utf-8")
            if entry["source"]["kind"] == "hosted":
                copy_hosted_content(hosted_directory(root, entry["source"]["path"]), skill_dir / "content")
        entries.sort(key=lambda item: item["entry"]["id"])
        recommendations = []
        for path in sorted((root / "recommendations").rglob("*.yaml")):
            recommendation = load_yaml(path)
            if recommendation["id"] in report.publishable_recommendation_ids:
                recommendations.append(recommendation)
        recommendations.sort(key=lambda item: item["id"])
        curation = {}
        for path in sorted((root / "curation").glob("*.yaml")) if (root / "curation").is_dir() else []:
            curation.update(load_yaml(path))
        if set(curation) != {"topics", "synonyms"}:
            raise ValueError("curation output requires topics and synonyms")
        _write_json(staging / "catalog.json", {"schema_version": 1, "source_commit": report.source_commit, "entries": entries})
        _write_json(staging / "recommendations.json", {"schema_version": 1, "recommendations": recommendations})
        _write_json(staging / "curation.json", curation)
        _write_json(staging / "status.json", {"schema_version": 1, "input_digest": report.input_digest, "result": "pass", "entry_count": len(entries), "recommendation_count": len(recommendations), "verification_note": "installation/behavior 未运行"})
        _copy_public_resources(root, staging)
        legacy_backup = parent / f".{output.name}.previous"
        if legacy_backup.exists():
            raise BuildInputError(DirectoryIssue("--out", "out", "output-backup-conflict", "error", "Output backup sibling already exists and is preserved.", "Choose another output path or move the existing sibling yourself."))
        backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.previous-", dir=parent))
        backup.rmdir()
        if output.exists():
            output.replace(backup)
        try:
            staging.replace(output)
        except Exception:
            if backup.exists():
                backup.replace(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BuildResult(report.input_digest, report.source_commit, output, len(entries), len(recommendations))
