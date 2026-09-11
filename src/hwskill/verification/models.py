from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from hwskill.directory.digest import canonical_json, sha256_bytes
from hwskill.directory.entries import _normalise_identity_path, _normalise_repository
from hwskill.directory.schema import validator_for


class VerificationReportError(ValueError):
    """A report is not a valid VerificationReport v1 document."""


def install_digest(install_json: Mapping[str, Any]) -> str:
    """Digest the published install JSON, excluding its self-referential digest."""
    data = {key: value for key, value in install_json.items() if key != "install_digest"}
    return sha256_bytes(canonical_json(data))


def validate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    data = deepcopy(dict(report))
    errors = sorted(validator_for("verification").iter_errors(data), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(error.message for error in errors)
        raise VerificationReportError(detail)
    stages = data["stages"]
    if stages["acquisition"]["result"] == "pass" and stages["metadata"]["result"] != "pass":
        raise VerificationReportError("acquisition pass requires metadata pass")
    if stages["installation"]["result"] == "pass" and stages["acquisition"]["result"] != "pass":
        raise VerificationReportError("installation pass requires acquisition pass")
    if stages["behavior"]["result"] == "pass" and stages["installation"]["result"] != "pass":
        raise VerificationReportError("behavior pass requires installation pass")
    return data


def bind_install_material(catalog_item: Mapping[str, Any], install_json: Mapping[str, Any]) -> dict[str, Any]:
    """Bind separately published catalog identity and install material before copying."""
    item = dict(catalog_item)
    install = deepcopy(dict(install_json))
    entry = item.get("entry")
    identity = item.get("source_identity")
    if not isinstance(entry, Mapping) or not isinstance(identity, Mapping):
        raise VerificationReportError("catalog item must contain entry and source_identity")
    skill_id = entry.get("id")
    entry_digest = item.get("entry_digest")
    if not isinstance(skill_id, str) or not isinstance(entry_digest, str):
        raise VerificationReportError("catalog item is missing skill_id or entry_digest")
    if sha256_bytes(canonical_json(entry)) != entry_digest:
        raise VerificationReportError("entry_digest does not match canonical catalog entry bytes")
    if item.get("lifecycle") != entry.get("lifecycle"):
        raise VerificationReportError("catalog lifecycle must equal entry lifecycle")
    lifecycle = item.get("lifecycle")
    capability = item.get("install_capability")
    if capability != "installable" and not (lifecycle == "withdrawn" and capability == "disabled"):
        raise VerificationReportError("catalog item is not directory-installable")
    if install.get("skill_id") != skill_id or install.get("entry_digest") != entry_digest:
        raise VerificationReportError("install JSON does not bind to the selected catalog item")
    entry_source = entry.get("source")
    install_source = install.get("source")
    if not isinstance(entry_source, Mapping) or not isinstance(install_source, Mapping):
        raise VerificationReportError("catalog and install source objects are required")
    if entry_source.get("kind") != identity.get("kind") or entry_source.get("identity") != identity.get("identity"):
        raise VerificationReportError("normalized entry source does not match catalog identity")
    if install_source.get("kind") != identity.get("kind"):
        raise VerificationReportError("install source kind does not match catalog identity")
    if identity.get("kind") == "hosted":
        path = install_source.get("path")
        if not isinstance(path, str) or identity.get("identity") != f"hosted:{_normalise_identity_path(path)}":
            raise VerificationReportError("hosted install path does not match catalog identity")
        if install_source.get("resolved_revision") != identity.get("resolved_revision"):
            raise VerificationReportError("hosted install revision does not match catalog identity")
    elif identity.get("kind") == "external":
        repository = install_source.get("repository")
        path = install_source.get("path")
        requested_ref = install_source.get("requested_ref")
        if not isinstance(repository, str) or not isinstance(path, str):
            raise VerificationReportError("external directory install requires repository and path")
        if not isinstance(requested_ref, str) or not requested_ref or requested_ref.startswith("-"):
            raise VerificationReportError("external directory install requires a safe requested_ref")
        try:
            expected_identity = f"git:{_normalise_repository(repository)}\0{_normalise_identity_path(path)}"
        except ValueError as error:
            raise VerificationReportError("external install repository is invalid") from error
        if identity.get("identity") != expected_identity:
            raise VerificationReportError("external install locator does not match catalog identity")
        if identity.get("requested_ref") != requested_ref:
            raise VerificationReportError("external requested_ref does not match catalog identity")
        if install_source.get("resolved_revision") != identity.get("resolved_revision"):
            raise VerificationReportError("external install revision does not match resolved catalog identity")
    else:
        raise VerificationReportError("catalog source identity kind is unsupported")
    if install.get("install", {}).get("method") != "directory":
        raise VerificationReportError("directory verification requires method=directory")
    expected = install_digest(install)
    if install.get("install_digest") != expected:
        raise VerificationReportError("install JSON digest does not match canonical bytes")
    return {
        "skill_id": skill_id,
        "entry_digest": entry_digest,
        "install_digest": expected,
        "source_identity": deepcopy(dict(identity)),
        "install_json": install,
        "lifecycle": lifecycle,
        "install_capability": capability,
    }
