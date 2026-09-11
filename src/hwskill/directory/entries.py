from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .models import DirectoryIssue, ValidationReport
from .schema import validator_for
from .yaml_io import YamlContractError, load_yaml


def _issue(file: Path, root: Path, field: str, code: str, message: str, action: str) -> DirectoryIssue:
    return DirectoryIssue(str(file.relative_to(root)), field, code, "error", message, action)


def _field(error_path: Iterable[object]) -> str:
    result = ""
    for part in error_path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += ("." if result else "") + str(part)
    return result or "$"


def _schema_field(error: Any) -> str:
    if error.validator == "required":
        missing = error.message.split("'", 2)
        if len(missing) >= 2:
            prefix = _field(error.absolute_path)
            return missing[1] if prefix == "$" else f"{prefix}.{missing[1]}"
    return _field(error.absolute_path)


def _source_commit(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _input_digest(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item.relative_to(root))):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    schema_root = Path(__file__).resolve().parents[3] / "schemas"
    for path in sorted(schema_root.glob("*.schema.json")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _is_safe_hosted_path(root: Path, value: str) -> bool:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "skills-src" or any(part in {"", ".", ".."} for part in path.parts):
        return False
    try:
        (root / Path(*path.parts)).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _url_is_http(value: object) -> bool:
    return isinstance(value, str) and (value.startswith("https://") or value.startswith("http://"))


def _source_identity(entry: dict[str, Any]) -> str | None:
    source = entry.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("kind") == "hosted" and isinstance(source.get("path"), str):
        return f"hosted:{source['path']}"
    locator = source.get("locator")
    if not isinstance(locator, dict):
        return None
    if locator.get("type") == "git":
        repository, path = locator.get("repository"), locator.get("path")
        if isinstance(repository, str) and isinstance(path, str):
            return f"git:{repository}\0{path}"
    if locator.get("type") == "web" and isinstance(locator.get("url"), str):
        return f"web:{locator['url']}"
    return None


def validate_repository(repo_root: Path) -> ValidationReport:
    """Validate YAML source metadata without fetching or reading external bodies."""
    root = repo_root.resolve()
    entry_paths = sorted((root / "entries").rglob("*.yaml")) if (root / "entries").is_dir() else []
    recommendation_paths = sorted((root / "recommendations").rglob("*.yaml")) if (root / "recommendations").is_dir() else []
    all_paths = [*entry_paths, *recommendation_paths]
    issues: list[DirectoryIssue] = []
    entries: list[tuple[Path, dict[str, Any]]] = []
    recommendations: list[tuple[Path, dict[str, Any]]] = []

    for path, schema_name, destination in (
        *((path, "entry", entries) for path in entry_paths),
        *((path, "recommendation", recommendations) for path in recommendation_paths),
    ):
        try:
            document = load_yaml(path)
        except (OSError, YamlContractError) as exc:
            text = str(exc)
            code = "yaml-merge-key" if "merge key" in text else "yaml-duplicate-key" if "duplicate YAML key" in text else "yaml-invalid"
            issues.append(_issue(path, root, "$", code, text, "Use JSON-compatible YAML with unique keys."))
            continue
        schema_errors = sorted(validator_for(schema_name).iter_errors(document), key=lambda error: list(error.absolute_path))
        for error in schema_errors:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update the field to match its JSON Schema contract."))
        if not schema_errors:
            destination.append((path, document))

    by_id: dict[str, tuple[Path, dict[str, Any]]] = {}
    identities: dict[str, Path] = {}
    for path, entry in entries:
        entry_id = entry["id"]
        expected = root / "entries" / entry["layer"] / f"{entry_id}.yaml"
        if path != expected:
            issues.append(_issue(path, root, "id", "entry-path-mismatch", "Entry ID and layer must match its entries/<layer>/<namespace>/<name>.yaml path.", "Move the file or correct id/layer."))
        if entry_id in by_id:
            issues.append(_issue(path, root, "id", "duplicate-entry-id", f"Duplicate entry ID {entry_id!r}.", "Use one stable ID per skill."))
        else:
            by_id[entry_id] = (path, entry)
        source = entry["source"]
        if source["kind"] == "hosted":
            source_path = source["path"]
            if not _is_safe_hosted_path(root, source_path):
                issues.append(_issue(path, root, "source.path", "hosted-path-unsafe", "Hosted path must be a safe repository-relative skills-src path.", "Use a path below skills-src without traversal."))
            else:
                hosted_root = root / Path(*PurePosixPath(source_path).parts)
                if not (hosted_root / "SKILL.md").is_file():
                    issues.append(_issue(path, root, "source.path", "hosted-skill-missing", "Hosted source must contain SKILL.md.", "Add the complete hosted skill directory."))
        else:
            locator = source["locator"]
            url = locator.get("repository") if locator["type"] == "git" else locator.get("url")
            if not _url_is_http(url):
                issues.append(_issue(path, root, "source.locator", "external-locator-unsafe", "External locator must use http or https.", "Use an explicit http(s) locator."))
        identity = _source_identity(entry)
        if identity:
            previous = identities.get(identity)
            if previous is not None:
                issues.append(_issue(path, root, "source", "duplicate-source-identity", f"Source identity already belongs to {previous.relative_to(root)}.", "Reuse the existing entry or choose a distinct source."))
            else:
                identities[identity] = path

    invalid_files = {issue.file for issue in issues}
    for path, recommendation in recommendations:
        if recommendation["status"] != "ready":
            continue
        for skill in recommendation["skills"]:
            target = by_id.get(skill["id"])
            if target is None or target[1].get("lifecycle", "active") != "active":
                issues.append(_issue(path, root, "skills", "recommendation-missing-entry", f"Ready recommendation references unavailable active entry {skill['id']!r}.", "Reference an active entry in this repository or keep the recommendation draft."))

    invalid_files = {issue.file for issue in issues}
    publishable_entries = tuple(sorted(entry_id for entry_id, (path, _) in by_id.items() if str(path.relative_to(root)) not in invalid_files))
    publishable_recommendations = tuple(sorted(
        recommendation["id"] for path, recommendation in recommendations
        if recommendation["status"] == "ready" and str(path.relative_to(root)) not in invalid_files
    ))
    issues.sort(key=lambda issue: (issue.file, issue.field, issue.code, issue.message))
    return ValidationReport(1, _source_commit(root), _input_digest(root, all_paths), "pass" if not issues else "fail", tuple(issues), publishable_entries, publishable_recommendations)
