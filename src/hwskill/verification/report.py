from __future__ import annotations

from datetime import datetime, timezone
import json
import platform
import os
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterable, Mapping

from hwskill.directory.digest import canonical_json

from .models import VerificationReportError, validate_report
from .secure_fs import UnsafePathError, open_directory_chain, read_regular_file_at


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_installation_report(*, material: Mapping[str, Any], host: str, host_version: str, runner_identity: str, installation_result: str, installation_reason: str, report_id: str | None = None, stages: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    if installation_result not in {"pass", "fail", "blocked", "not_run"}:
        raise VerificationReportError("invalid installation result")
    required = ("skill_id", "entry_digest", "install_digest", "source_identity")
    if any(not isinstance(material.get(key), str if key != "source_identity" else dict) for key in required):
        raise VerificationReportError("installation report requires bound material")
    stamp = _now()
    identifier = report_id or f"{material['skill_id'].replace('/', '-')}-{stamp.replace(':', '')}-{uuid4().hex}"
    installation = {"result": installation_result, "reason": installation_reason}
    report = {
        "schema_version": 1,
        "report_id": identifier,
        "skill_id": material["skill_id"],
        "source_identity": material["source_identity"],
        "entry_digest": material["entry_digest"],
        "install_digest": material["install_digest"],
        "host": host,
        "host_version": host_version,
        "os": platform.system().lower() or "unknown",
        "arch": platform.machine() or "unknown",
        "runner_identity": runner_identity,
        "executed_at": stamp,
        "stages": stages or {
            "metadata": {"result": "pass", "reason": "bound metadata checked"},
            "acquisition": {"result": "pass", "reason": "local reviewed source supplied"},
            "installation": installation,
            "behavior": {"result": "not_run", "reason": "no agent behavior credentials or task were supplied"},
        },
        "evidence_refs": [],
    }
    return validate_report(report)


class ReportStore:
    def __init__(self, root: Path):
        self.root = root

    def write(self, report: Mapping[str, Any]) -> Path:
        data = validate_report(report)
        report_id = data["report_id"]
        if not report_id.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise VerificationReportError("report_id is not safe for a report filename")
        path = self.root / f"{report_id}.json"
        payload = canonical_json(data)
        filename = f"{report_id}.json"
        try:
            root_fd = open_directory_chain(self.root, create=True)
        except (UnsafePathError, NotADirectoryError, OSError) as error:
            raise VerificationReportError("report root ancestry cannot contain symlinks or non-directories") from error
        temporary = f".report-{uuid4().hex}"
        temp_fd: int | None = None
        temp_identity: tuple[int, int] | None = None
        try:
            try:
                existing = read_regular_file_at(root_fd, filename)
            except FileNotFoundError:
                existing = None
            except UnsafePathError as error:
                raise VerificationReportError("report path must be a regular file, not a symlink") from error
            if existing is not None:
                if existing == payload:
                    return path
                raise VerificationReportError("report_id collision has different bytes")

            temp_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
            metadata = os.fstat(temp_fd)
            temp_identity = (metadata.st_dev, metadata.st_ino)
            view = memoryview(payload)
            while view:
                view = view[os.write(temp_fd, view):]
            os.fsync(temp_fd)
            try:
                os.link(temporary, filename, src_dir_fd=root_fd, dst_dir_fd=root_fd, follow_symlinks=False)
            except FileExistsError:
                try:
                    existing = read_regular_file_at(root_fd, filename)
                except UnsafePathError as error:
                    raise VerificationReportError("report_id collision is not a regular file") from error
                if existing != payload:
                    raise VerificationReportError("report_id collision has different bytes")
            os.fsync(root_fd)
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_identity is not None:
                try:
                    current = os.stat(temporary, dir_fd=root_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == temp_identity:
                        os.unlink(temporary, dir_fd=root_fd)
                        os.fsync(root_fd)
                except FileNotFoundError:
                    pass
            os.close(root_fd)
        return path

    def read_all(self) -> list[dict[str, Any]]:
        try:
            root_fd = open_directory_chain(self.root, create=False)
        except FileNotFoundError:
            return []
        except (UnsafePathError, NotADirectoryError, OSError) as error:
            raise VerificationReportError("report root ancestry cannot contain symlinks or non-directories") from error
        try:
            reports = []
            for name in sorted(item for item in os.listdir(root_fd) if item.endswith(".json")):
                try:
                    payload = read_regular_file_at(root_fd, name)
                except UnsafePathError as error:
                    raise VerificationReportError("report file cannot be a symlink or non-regular file") from error
                reports.append(validate_report(json.loads(payload.decode("utf-8"))))
            return reports
        finally:
            os.close(root_fd)


def _expired(report: Mapping[str, Any]) -> bool:
    value = report.get("expires_at")
    if not value:
        return False
    return datetime.fromisoformat(value.replace("Z", "+00:00")) < datetime.now(timezone.utc)


def _source_identity_matches(report_identity: object, material_identity: object) -> bool:
    if not isinstance(report_identity, Mapping) or not isinstance(material_identity, Mapping):
        return False
    if material_identity.get("kind") != "external":
        return report_identity == material_identity
    stable_fields = ("kind", "identity", "requested_ref")
    if any(report_identity.get(field) != material_identity.get(field) for field in stable_fields):
        return False
    material_revision = material_identity.get("resolved_revision")
    material_content = material_identity.get("content_digest")
    if material_revision is not None or material_content is not None:
        return report_identity.get("resolved_revision") == material_revision and report_identity.get("content_digest") == material_content
    return bool(report_identity.get("resolved_revision")) and bool(report_identity.get("content_digest"))


def current_installation_status(reports: Iterable[Mapping[str, Any]], material: Mapping[str, Any], *, host: str, host_version: str, os_name: str, arch: str) -> dict[str, Any]:
    historical = [report for report in reports if report.get("skill_id") == material.get("skill_id")]
    historical_ids = sorted(report["report_id"] for report in historical)
    if material.get("lifecycle") == "withdrawn":
        return {"result": "not_run", "report_id": None, "historical_report_ids": historical_ids}
    matching = [
        report for report in historical
        if report.get("host") == host
        and report.get("host_version") == host_version
        and report.get("os") == os_name
        and report.get("arch") == arch
        and report.get("entry_digest") == material.get("entry_digest")
        and report.get("install_digest") == material.get("install_digest")
        and _source_identity_matches(report.get("source_identity"), material.get("source_identity"))
        and not _expired(report)
    ]
    if matching:
        newest_time = max(datetime.fromisoformat(report["executed_at"].replace("Z", "+00:00")) for report in matching)
        simultaneous = [report for report in matching if datetime.fromisoformat(report["executed_at"].replace("Z", "+00:00")) == newest_time]
        severity = {"pass": 0, "not_run": 1, "blocked": 2, "fail": 3}
        newest = max(simultaneous, key=lambda report: (severity[report["stages"]["installation"]["result"]], report["report_id"]))
        return {"result": newest["stages"]["installation"]["result"], "report_id": newest["report_id"], "historical_report_ids": historical_ids}
    return {"result": "not_run", "report_id": None, "historical_report_ids": historical_ids}
