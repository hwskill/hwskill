from __future__ import annotations

import hashlib
from importlib.resources import files
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import DirectoryIssue, ValidationReport
from .recommendations import RecommendationContractError, load_recommendation
from .schema import validator_for
from .translations import TranslationContractError, load_translation, translation_path
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


def _schema_errors(errors: Iterable[Any]) -> Iterable[Any]:
    """Prefer actionable format leaves over an enclosing oneOf failure."""
    for error in errors:
        if error.validator != "oneOf":
            yield error
            continue
        pending = list(error.context)
        format_errors: list[Any] = []
        while pending:
            child = pending.pop()
            if child.validator == "format":
                format_errors.append(child)
            pending.extend(child.context)
        yield from format_errors or [error]


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
    schema_root = files("hwskill.directory").joinpath("schemas")
    for path in sorted((item for item in schema_root.iterdir() if item.name.endswith(".schema.json")), key=lambda item: item.name):
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
    if not isinstance(value, str) or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.netloc)
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _is_safe_external_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _is_safe_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not value.startswith("-")
        and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    )


# Kept while the legacy migration preview is converted to Entry v2.
_is_safe_requested_ref = _is_safe_ref


def _validate_entry_public_security(
    path: Path,
    root: Path,
    entry: dict[str, Any],
    issues: list[DirectoryIssue],
) -> None:
    source = entry["source"]
    if source["kind"] == "external":
        locator = source["locator"]
        url = locator.get("repository") if locator["type"] == "git" else locator.get("url")
        if not _url_is_http(url):
            issues.append(_issue(path, root, "source.locator", "public-url-unsafe", "External locator must be a credential-free http(s) URL.", "Use an explicit public URL without userinfo credentials."))
        if locator["type"] == "git":
            if not _is_safe_external_path(locator.get("path")):
                issues.append(_issue(path, root, "source.locator.path", "external-path-unsafe", "External Git path must be a strict POSIX relative path.", "Use a non-empty relative path without traversal, empty segments, or backslashes."))
            ref = locator.get("ref")
            if ref is not None and not _is_safe_ref(ref):
                issues.append(_issue(path, root, "source.locator.ref", "external-ref-unsafe", "External Git ref must not be blank, option-like, whitespace-bearing, or contain controls.", "Use a safe branch, tag, or commit without a leading dash."))
            file_url = locator.get("file_url")
            if file_url is not None and not _url_is_http(file_url):
                issues.append(_issue(path, root, "source.locator.file_url", "public-url-unsafe", "External skill file URL must be a credential-free http(s) URL.", "Use an explicit public skill file URL without userinfo credentials."))
            try:
                repository_host = urlsplit(locator["repository"]).hostname
            except ValueError:
                repository_host = None
            if repository_host and repository_host.lower() != "github.com" and file_url is None:
                issues.append(_issue(path, root, "source.locator.file_url", "source-file-url-required", "Non-GitHub Git sources require an explicit skill file URL.", "Add file_url pointing to the upstream SKILL.md."))
    for field, value in (
        ("install.instructions_url", entry["install"].get("instructions_url")),
        ("license.url", entry["license"].get("url")),
    ):
        if value is not None and not _url_is_http(value):
            issues.append(_issue(path, root, field, "public-url-unsafe", "Public URL must be credential-free http(s).", "Remove URL userinfo credentials and use a public http(s) URL."))


def _validate_recommendation_public_security(
    path: Path,
    root: Path,
    recommendation: dict[str, Any],
    issues: list[DirectoryIssue],
) -> None:
    for index, evidence in enumerate(recommendation.get("evidence", [])):
        if not _url_is_http(evidence.get("url")):
            issues.append(_issue(path, root, f"evidence[{index}].url", "public-url-unsafe", "Evidence URL must be credential-free http(s).", "Remove URL userinfo credentials and use a public http(s) URL."))


def _normalise_identity_path(value: str) -> str:
    return value.rstrip("/") or "."


def _normalise_repository(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = f":{parsed.port}" if parsed.port else ""
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    return urlunsplit((parsed.scheme.lower(), f"{userinfo}{host}{port}", path, parsed.query, parsed.fragment))


def _source_identity(entry: dict[str, Any]) -> str | None:
    source = entry.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("kind") == "hosted" and isinstance(source.get("path"), str):
        return f"hosted:{_normalise_identity_path(source['path'])}"
    locator = source.get("locator")
    if not isinstance(locator, dict):
        return None
    if locator.get("type") == "git":
        repository, path = locator.get("repository"), locator.get("path")
        if isinstance(repository, str) and isinstance(path, str):
            try:
                return f"git:{_normalise_repository(repository)}\0{_normalise_identity_path(path)}"
            except ValueError:
                return None
    if locator.get("type") == "web" and isinstance(locator.get("url"), str):
        try:
            return f"web:{_normalise_repository(locator['url'])}"
        except ValueError:
            return None
    return None


def validate_repository(repo_root: Path) -> ValidationReport:
    """Validate YAML source metadata without fetching or reading external bodies."""
    root = repo_root.resolve()
    entry_paths = sorted((root / "entries").rglob("*.yaml")) if (root / "entries").is_dir() else []
    recommendation_paths = sorted((root / "recommendations").rglob("*.md")) if (root / "recommendations").is_dir() else []
    translation_paths = sorted((root / "translations").rglob("*.md")) if (root / "translations").is_dir() else []
    template_entry_paths = sorted((root / "templates" / "entries").rglob("*.yaml")) if (root / "templates" / "entries").is_dir() else []
    template_recommendation_paths = sorted((root / "templates" / "recommendations").rglob("*.md")) if (root / "templates" / "recommendations").is_dir() else []
    curation_paths = sorted((root / "curation").glob("*.yaml")) if (root / "curation").is_dir() else []
    all_paths = [*entry_paths, *recommendation_paths, *translation_paths, *template_entry_paths, *template_recommendation_paths, *curation_paths]
    issues: list[DirectoryIssue] = []
    entries: list[tuple[Path, dict[str, Any]]] = []
    recommendations: list[tuple[Path, dict[str, Any]]] = []
    translations: list[tuple[Path, dict[str, Any]]] = []
    curation_sections: set[str] = set()

    for path in entry_paths:
        try:
            document = load_yaml(path)
        except (OSError, YamlContractError) as exc:
            text = str(exc)
            code = "yaml-merge-key" if "merge key" in text else "yaml-duplicate-key" if "duplicate YAML key" in text else "yaml-invalid"
            issues.append(_issue(path, root, "$", code, text, "Use JSON-compatible YAML with unique keys."))
            continue
        schema_errors = sorted(_schema_errors(validator_for("entry").iter_errors(document)), key=lambda error: list(error.absolute_path))
        for error in schema_errors:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update the field to match its JSON Schema contract."))
        if not schema_errors:
            entries.append((path, document))
            _validate_entry_public_security(path, root, document, issues)

    for path in recommendation_paths:
        try:
            document = load_recommendation(path)
        except RecommendationContractError as exc:
            issues.append(_issue(path, root, exc.field, exc.code, str(exc), "Use a complete Markdown file with YAML Frontmatter."))
            continue
        except (OSError, YamlContractError) as exc:
            text = str(exc)
            code = "yaml-merge-key" if "merge key" in text else "yaml-duplicate-key" if "duplicate YAML key" in text else "yaml-invalid"
            issues.append(_issue(path, root, "$", code, text, "Use JSON-compatible YAML Frontmatter with unique keys."))
            continue
        source_document = {key: value for key, value in document.items() if key not in {"body", "body_format"}}
        source_errors = sorted(_schema_errors(validator_for("recommendation-source").iter_errors(source_document)), key=lambda error: list(error.absolute_path))
        output_errors = sorted(_schema_errors(validator_for("recommendation").iter_errors(document)), key=lambda error: list(error.absolute_path))
        for error in [*source_errors, *output_errors]:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update the field to match its JSON Schema contract."))
        if not source_errors and not output_errors:
            recommendations.append((path, document))
            _validate_recommendation_public_security(path, root, document, issues)

    for path in translation_paths:
        try:
            document = load_translation(path)
        except TranslationContractError as exc:
            issues.append(_issue(path, root, exc.field, exc.code, str(exc), "Use a complete Markdown translation with YAML Frontmatter."))
            continue
        except (OSError, YamlContractError) as exc:
            text = str(exc)
            code = "yaml-merge-key" if "merge key" in text else "yaml-duplicate-key" if "duplicate YAML key" in text else "yaml-invalid"
            issues.append(_issue(path, root, "$", code, text, "Use JSON-compatible YAML Frontmatter with unique keys."))
            continue
        skill_id = document.get("skill_id")
        if isinstance(skill_id, str):
            try:
                expected = translation_path(root, skill_id)
            except ValueError:
                expected = None
            if expected is not None and path != expected:
                issues.append(_issue(path, root, "skill_id", "translation-path-mismatch", "Translation skill_id must match translations/<namespace>/<name>.md.", "Rename the file or correct its Frontmatter skill_id."))
        source_document = {key: value for key, value in document.items() if key not in {"body", "body_format"}}
        schema_errors = sorted(
            _schema_errors(validator_for("translation-source").iter_errors(source_document)),
            key=lambda error: list(error.absolute_path),
        )
        for error in schema_errors:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update the translation Frontmatter to match its source Schema contract."))
        if not schema_errors:
            translations.append((path, document))

    for path in template_entry_paths:
        try:
            document = load_yaml(path)
        except (OSError, YamlContractError) as exc:
            text = str(exc)
            code = "yaml-merge-key" if "merge key" in text else "yaml-duplicate-key" if "duplicate YAML key" in text else "yaml-invalid"
            issues.append(_issue(path, root, "$", code, text, "Use JSON-compatible YAML with unique keys."))
            continue
        schema_errors = sorted(_schema_errors(validator_for("entry").iter_errors(document)), key=lambda error: list(error.absolute_path))
        for error in schema_errors:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update the template to match its JSON Schema contract."))
        if not schema_errors:
            _validate_entry_public_security(path, root, document, issues)

    for path in template_recommendation_paths:
        try:
            document = load_recommendation(path)
        except RecommendationContractError as exc:
            issues.append(_issue(path, root, exc.field, exc.code, str(exc), "Use a complete Markdown template with YAML Frontmatter."))
            continue
        except (OSError, YamlContractError) as exc:
            text = str(exc)
            code = "yaml-merge-key" if "merge key" in text else "yaml-duplicate-key" if "duplicate YAML key" in text else "yaml-invalid"
            issues.append(_issue(path, root, "$", code, text, "Use JSON-compatible YAML Frontmatter with unique keys."))
            continue
        source_document = {key: value for key, value in document.items() if key not in {"body", "body_format"}}
        schema_errors = sorted(_schema_errors(validator_for("recommendation-source").iter_errors(source_document)), key=lambda error: list(error.absolute_path))
        for error in schema_errors:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update the template to match its source Schema contract."))
        if not schema_errors:
            _validate_recommendation_public_security(path, root, document, issues)

    for path in curation_paths:
        try:
            document = load_yaml(path)
        except (OSError, YamlContractError) as exc:
            issues.append(_issue(path, root, "$", "yaml-invalid", str(exc), "Use JSON-compatible YAML with unique keys."))
            continue
        schema_errors = sorted(_schema_errors(validator_for("curation").iter_errors(document)), key=lambda error: list(error.absolute_path))
        for error in schema_errors:
            issues.append(_issue(path, root, _schema_field(error), f"schema-{error.validator}", error.message, "Update curation to match its JSON Schema contract."))
        if not schema_errors:
            curation_sections.update(document)
    for section in sorted({"topics", "synonyms"} - curation_sections):
        issues.append(DirectoryIssue("curation", section, "curation-missing-section", "error", f"Curation requires {section!r} for the published aggregate.", "Add a valid curation source file for this section."))

    by_id: dict[str, tuple[Path, dict[str, Any]]] = {}
    entry_paths_by_id: dict[str, list[Path]] = {}
    identities: dict[str, Path] = {}
    for path, entry in entries:
        entry_id = entry["id"]
        entry_paths_by_id.setdefault(entry_id, []).append(path)
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
        identity = _source_identity(entry)
        if identity:
            previous = identities.get(identity)
            if previous is not None:
                issues.append(_issue(path, root, "source", "duplicate-source-identity", f"Source identity already belongs to {previous.relative_to(root)}.", "Reuse the existing entry or choose a distinct source."))
            else:
                identities[identity] = path

    translations_by_id: dict[str, list[Path]] = {}
    valid_translation_ids: set[str] = set()
    for path, translation in translations:
        skill_id = translation["skill_id"]
        translations_by_id.setdefault(skill_id, []).append(path)
        expected = translation_path(root, skill_id)
        if path != expected:
            continue
        elif skill_id not in by_id:
            issues.append(_issue(path, root, "skill_id", "translation-orphan", f"Translation references missing skill {skill_id!r}.", "Add the matching Entry or remove the orphan translation."))
        else:
            valid_translation_ids.add(skill_id)
    for skill_id, paths in translations_by_id.items():
        if len(paths) > 1:
            valid_translation_ids.discard(skill_id)
            for path in paths:
                issues.append(_issue(path, root, "skill_id", "duplicate-translation-id", f"Duplicate translation skill_id {skill_id!r}.", "Use one translation file per skill."))

    for entry_id, (path, _) in by_id.items():
        if entry_id not in valid_translation_ids:
            issues.append(_issue(path, root, "translation", "translation-missing", f"Publishable entry {entry_id!r} requires a valid Chinese translation.", f"Add {translation_path(root, entry_id).relative_to(root)}."))

    for path, entry in entries:
        replacement_id = entry.get("replacement_id")
        if replacement_id is not None and replacement_id not in by_id:
            issues.append(_issue(path, root, "replacement_id", "replacement-entry-missing", f"Replacement entry {replacement_id!r} does not exist.", "Reference an existing entry ID or remove replacement_id."))

    recommendation_paths_by_id: dict[str, list[Path]] = {}
    for path, recommendation in recommendations:
        recommendation_paths_by_id.setdefault(recommendation["id"], []).append(path)
        expected = root / "recommendations" / f"{recommendation['id']}.md"
        if path != expected:
            issues.append(_issue(path, root, "id", "recommendation-path-mismatch", "Recommendation ID must match recommendations/<id>.md.", "Rename the file or correct its Frontmatter id."))
    for recommendation_id, paths in recommendation_paths_by_id.items():
        if len(paths) > 1:
            for path in paths:
                issues.append(_issue(path, root, "id", "duplicate-recommendation-id", f"Duplicate recommendation ID {recommendation_id!r}.", "Use one stable ID per recommendation."))

    invalid_files = {issue.file for issue in issues}
    publishable_entry_ids = {
        entry_id for entry_id, (path, entry) in by_id.items()
        if len(entry_paths_by_id[entry_id]) == 1
        and str(path.relative_to(root)) not in invalid_files
        and entry_id in valid_translation_ids
        and entry.get("lifecycle", "active") == "active"
    }
    for path, recommendation in recommendations:
        if recommendation["status"] != "ready":
            continue
        for skill in recommendation["skills"]:
            target = by_id.get(skill["id"])
            if target is None or target[1].get("lifecycle", "active") != "active":
                issues.append(_issue(path, root, "skills", "recommendation-missing-entry", f"Ready recommendation references unavailable active entry {skill['id']!r}.", "Reference an active entry in this repository or keep the recommendation draft."))
            elif skill["id"] not in publishable_entry_ids:
                issues.append(_issue(path, root, "skills", "recommendation-unpublishable-entry", f"Ready recommendation references invalid entry {skill['id']!r}.", "Fix the entry before publishing this recommendation."))

    invalid_files = {issue.file for issue in issues}
    publishable_entries = tuple(sorted(
        entry_id for entry_id, (path, entry) in by_id.items()
        if str(path.relative_to(root)) not in invalid_files and entry_id in valid_translation_ids
    ))
    publishable_recommendations = tuple(sorted(
        recommendation["id"] for path, recommendation in recommendations
        if recommendation["status"] in {"ready", "withdrawn"}
        and str(path.relative_to(root)) not in invalid_files
    ))
    issues.sort(key=lambda issue: (issue.file, issue.field, issue.code, issue.message))
    for _, entry in entries:
        source = entry["source"]
        if source["kind"] == "hosted" and _is_safe_hosted_path(root, source["path"]):
            all_paths.extend(path for path in (root / Path(*PurePosixPath(source["path"]).parts)).rglob("*") if path.is_file())
    for path in (root / "CONTRIBUTING.md", root / "docs/guides/agent-contribution.md"):
        if path.is_file():
            all_paths.append(path)
    return ValidationReport(1, _source_commit(root), _input_digest(root, all_paths), "pass" if not issues else "fail", tuple(issues), publishable_entries, publishable_recommendations)
