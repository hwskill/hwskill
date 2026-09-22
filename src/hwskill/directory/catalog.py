from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
from urllib.parse import quote, urlsplit

from .digest import canonical_json, sha256_bytes
from .entries import validate_repository
from .hosted_content import copy_hosted_content, hosted_directory
from .models import DirectoryIssue
from .recommendations import load_recommendation
from .translations import load_translation, translation_path
from .yaml_io import load_yaml

PUBLIC_SOURCE_REPOSITORY = "https://github.com/hwskill/hwskill"


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


def _quoted_path(value: str) -> str:
    return "/".join(quote(part, safe="") for part in value.split("/"))


def build_source_url(entry: dict[str, Any]) -> str:
    source = entry["source"]
    if source["kind"] == "hosted":
        return f"{PUBLIC_SOURCE_REPOSITORY}/blob/HEAD/{_quoted_path(source['path'])}/SKILL.md"
    locator = source["locator"]
    if locator["type"] == "web":
        return locator["url"]
    if locator.get("file_url"):
        return locator["file_url"]
    repository = urlsplit(locator["repository"])
    if repository.hostname is None or repository.hostname.lower() != "github.com":
        raise ValueError("Non-GitHub Git locators require file_url.")
    repository_path = repository.path.strip("/")
    if repository_path.endswith(".git"):
        repository_path = repository_path[:-4]
    parts = repository_path.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("GitHub repository URL must identify owner/repository.")
    repository_url = f"https://github.com/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}"
    ref = quote(locator.get("ref", "HEAD"), safe="")
    return f"{repository_url}/blob/{ref}/{_quoted_path(locator['path'])}/SKILL.md"


def _normalized(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(entry)
    normalized["schema_version"] = 2
    normalized["lifecycle"] = entry.get("lifecycle", "active")
    return normalized


def _translation(entry: dict[str, Any], root: Path) -> dict[str, Any]:
    document = load_translation(translation_path(root, entry["id"]))
    return {
        "body_format": "markdown",
        "body": document["body"],
        "translated_at": document["translated_at"],
        "source_url": build_source_url(entry),
    }


def _install_markdown(entry: dict[str, Any]) -> str:
    install = entry["install"]
    source = entry["source"]
    lines = [
        f"# {entry['name']} 安装说明",
        "",
        f"技能 ID：`{entry['id']}`",
        f"安装方式：{install['method']}",
        f"默认范围：{install['default_scope']}",
        "",
        "## 声明来源",
        "",
    ]
    if source["kind"] == "hosted":
        lines.extend([
            f"仓库：{PUBLIC_SOURCE_REPOSITORY}",
            f"仓库内路径：{source['path']}",
            f"技能原文：{build_source_url(entry)}",
        ])
    else:
        locator = source["locator"]
        if locator["type"] == "git":
            lines.extend([
                f"仓库：{locator['repository']}",
                f"路径：{locator['path']}",
                f"ref：{locator.get('ref', '未指定，读取默认分支 HEAD')}",
                f"技能原文：{build_source_url(entry)}",
            ])
        else:
            lines.extend([
                f"页面：{locator['url']}",
                f"版本说明：{locator.get('version_note', '未提供')}",
            ])
    lines.extend(["", "## 给 Agent 的安装要求", ""])
    if install["method"] == "unknown":
        lines.extend([
            "来源未声明可直接执行的安装方法。请先阅读来源，确认完整文件、目标 Agent 的项目级技能目录、依赖和权限，再报告可行步骤。",
            "不要猜测命令，也不要覆盖已有同名技能。",
        ])
    else:
        lines.extend([
            "读取来源中的完整技能目录及配套文件，确认依赖、权限和目标 Agent 的项目级技能目录。",
            "按作者声明的方法安装，不要覆盖已有同名技能；安装后检查 Agent 能发现该技能，并报告实际来源、目标路径和结果。",
        ])
    if install.get("instructions_url"):
        lines.extend(["", f"上游安装说明：{install['instructions_url']}"])
    if install.get("included_skills"):
        lines.extend(["", f"同时包含：{'、'.join(install['included_skills'])}"])
    return "\n".join([*lines, ""])


def _install_data(entry: dict[str, Any], entry_digest: str) -> dict[str, Any]:
    public_install = deepcopy(entry["install"])
    document = {
        "schema_version": 2,
        "skill_id": entry["id"],
        "entry_digest": entry_digest,
        "source": deepcopy(entry["source"]),
        "install": public_install,
    }
    document["install_digest"] = sha256_bytes(canonical_json(document))
    return document


def _assert_safe_output(root: Path, out_dir: Path) -> Path:
    output = out_dir.resolve()
    if output == root or output in root.parents:
        raise BuildInputError(DirectoryIssue("--out", "out", "unsafe-output-path", "error", "Output path overlaps repository source material.", "Choose a separate derived output directory."))
    sources = [root / "entries", root / "recommendations", root / "translations", root / "curation", root / "skills-src", root / "schemas", root / "templates", root / "CONTRIBUTING.md", root / "docs/guides/agent-contribution.md"]
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
    template_source = root / "templates"
    if template_source.is_dir():
        shutil.copytree(template_source, output / "templates")


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
            normalized = _normalized(entry)
            entry_digest = sha256_bytes(canonical_json(normalized))
            entries.append({
                "entry": normalized,
                "entry_digest": entry_digest,
                "lifecycle": normalized["lifecycle"],
                "translation": _translation(entry, root),
            })
            skill_dir = staging / "skills" / entry["id"]
            _write_json(skill_dir / "install.json", _install_data(entry, entry_digest))
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "install.md").write_text(_install_markdown(entry), encoding="utf-8")
            if entry["source"]["kind"] == "hosted":
                copy_hosted_content(hosted_directory(root, entry["source"]["path"]), skill_dir / "content")
        entries.sort(key=lambda item: item["entry"]["id"])
        recommendations = []
        for path in sorted((root / "recommendations").rglob("*.md")):
            recommendation = load_recommendation(path)
            if recommendation["id"] in report.publishable_recommendation_ids:
                recommendations.append(recommendation)
        recommendations.sort(key=lambda item: item["id"])
        curation = {}
        for path in sorted((root / "curation").glob("*.yaml")) if (root / "curation").is_dir() else []:
            curation.update(load_yaml(path))
        if set(curation) != {"topics", "synonyms"}:
            raise ValueError("curation output requires topics and synonyms")
        _write_json(staging / "catalog.json", {"schema_version": 2, "source_commit": report.source_commit, "entries": entries})
        _write_json(staging / "recommendations.json", {"schema_version": 1, "recommendations": recommendations})
        _write_json(staging / "curation.json", curation)
        _write_json(staging / "status.json", {"schema_version": 2, "input_digest": report.input_digest, "result": "pass", "entry_count": len(entries), "recommendation_count": len(recommendations)})
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
