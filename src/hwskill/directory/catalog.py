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
from .yaml_io import load_yaml


@dataclass(frozen=True)
class BuildResult:
    input_digest: str
    source_commit: str | None
    output_dir: Path
    entry_count: int
    recommendation_count: int


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(data))


def _identity(entry: dict[str, Any], root: Path) -> dict[str, Any]:
    source = entry["source"]
    if source["kind"] == "hosted":
        source_root = hosted_directory(root, source["path"])
        from .hosted_content import directory_digest
        return {"kind": "hosted", "identity": _source_identity(entry), "resolved_revision": None, "content_digest": directory_digest(source_root)}
    locator = source["locator"]
    return {"kind": "external", "identity": _source_identity(entry), "resolved_revision": locator.get("requested_ref"), "content_digest": None}


def _summary() -> dict[str, dict[str, Any]]:
    stage = {"result": "not_run", "report_id": None, "executed_at": None}
    return {name: dict(stage) for name in ("metadata", "acquisition", "installation", "behavior")}


def _normalized(entry: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    source = {"kind": entry["source"]["kind"], "identity": identity["identity"]}
    return {
        "schema_version": 1, "id": entry["id"], "name": entry["name"], "summary": entry["summary"],
        "layer": entry["layer"], "purposes": entry["purposes"], "examples": entry["examples"],
        "source": source,
        "install": {"method": entry["install"]["method"], "default_scope": entry["install"]["default_scope"], "instructions_url": entry["install"].get("instructions_url")},
        "compatibility": entry["compatibility"], "license": entry["license"],
        "lifecycle": entry.get("lifecycle", "active"), "lifecycle_reason": entry.get("lifecycle_reason"), "replacement_id": entry.get("replacement_id"),
    }


def _install_markdown(entry: dict[str, Any], identity: dict[str, Any]) -> str:
    install = entry["install"]
    lines = [f"# {entry['name']} 安装说明", "", f"技能 ID：`{entry['id']}`", f"安装方式：{install['method']}", "默认范围：project", "", "## 来源", "", str(identity["identity"]), "", "## 验证状态", "", "安装与行为验证：not_run（本构建未执行安装或行为测试）。", ""]
    if install["method"] == "unknown":
        lines.extend(["未提供可执行安装命令；请按来源人工确认安装方式。", ""])
    elif install.get("instructions_url"):
        lines.extend([f"上游指引：{install['instructions_url']}", ""])
    return "\n".join(lines)


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
    parent = out_dir.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=parent))
    try:
        entries: list[dict[str, Any]] = []
        for path in sorted((root / "entries").rglob("*.yaml")):
            entry = load_yaml(path)
            if entry["id"] not in report.publishable_entry_ids:
                continue
            identity = _identity(entry, root)
            normalized = _normalized(entry, identity)
            entry_digest = sha256_bytes(canonical_json(normalized))
            summary = _summary()
            entries.append({"entry": normalized, "entry_digest": entry_digest, "source_identity": identity, "lifecycle": normalized["lifecycle"], "install_capability": "guidance_only" if entry["install"]["method"] == "unknown" else "installable", "verification_summary": summary})
            skill_dir = staging / "skills" / entry["id"]
            _write_json(skill_dir / "install.json", {"schema_version": 1, "skill_id": entry["id"], "entry_digest": entry_digest, "source_identity": identity, "install": normalized["install"], "verification_summary": summary})
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
        _write_json(staging / "catalog.json", {"schema_version": 1, "source_commit": report.source_commit, "entries": entries})
        _write_json(staging / "recommendations.json", {"schema_version": 1, "recommendations": recommendations})
        _write_json(staging / "status.json", {"schema_version": 1, "input_digest": report.input_digest, "result": "pass", "entry_count": len(entries), "recommendation_count": len(recommendations), "verification_note": "installation/behavior 未运行"})
        _copy_public_resources(root, staging)
        backup = parent / f".{out_dir.name}.previous"
        if backup.exists():
            shutil.rmtree(backup)
        if out_dir.exists():
            out_dir.replace(backup)
        try:
            staging.replace(out_dir)
        except Exception:
            if backup.exists():
                backup.replace(out_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BuildResult(report.input_digest, report.source_commit, out_dir, len(entries), len(recommendations))
