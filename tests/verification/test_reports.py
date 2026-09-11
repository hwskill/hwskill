from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class VerificationReportTests(unittest.TestCase):
    def fake_host(self, root: Path, version: str = "1.0") -> Path:
        bin_directory = root / f"fake-bin-{version.replace('.', '-')}"
        bin_directory.mkdir(exist_ok=True)
        executable = bin_directory / "codex"
        executable.write_text(f"#!/bin/sh\nprintf 'codex {version}\\n'\n", encoding="utf-8")
        executable.chmod(0o700)
        return executable

    def cli_env(self, root: Path, version: str = "1.0") -> dict[str, str]:
        executable = self.fake_host(root, version)
        return {**os.environ, "PYTHONPATH": "src", "PATH": f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}"}

    def report(self, *, host: str = "codex", entry_digest: str = "sha256:entry", install_digest: str = "sha256:install") -> dict[str, object]:
        return {
            "schema_version": 1,
            "report_id": f"report-{host}",
            "skill_id": "example/hosted",
            "source_identity": {"kind": "hosted", "identity": "hosted:example", "resolved_revision": "a" * 40, "content_digest": "sha256:" + "b" * 64},
            "entry_digest": entry_digest,
            "install_digest": install_digest,
            "host": host,
            "host_version": "1.0",
            "os": "linux",
            "arch": "x86_64",
            "runner_identity": "test-runner",
            "executed_at": "2026-09-11T00:00:00Z",
            "stages": {name: {"result": "pass", "reason": "fixture"} for name in ("metadata", "acquisition", "installation", "behavior")},
            "evidence_refs": ["https://example.test/evidence"],
        }

    def test_report_is_schema_valid_and_current_only_for_exact_bound_inputs(self) -> None:
        """A stale report may be retained, but cannot advertise a current installation pass."""
        from hwskill.verification.report import ReportStore, current_installation_status

        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            report = self.report()
            stored = store.write(report)
            self.assertTrue(stored.is_file())
            current = current_installation_status(store.read_all(), report, host="codex", host_version="1.0", os_name="linux", arch="x86_64")
            stale = current_installation_status(store.read_all(), {**report, "entry_digest": "sha256:new"}, host="codex", host_version="1.0", os_name="linux", arch="x86_64")
            other_host = current_installation_status(store.read_all(), report, host="claude", host_version="1.0", os_name="linux", arch="x86_64")
            other_source = current_installation_status(
                store.read_all(),
                {**report, "source_identity": {**report["source_identity"], "resolved_revision": "c" * 40}},
                host="codex", host_version="1.0", os_name="linux", arch="x86_64",
            )
            self.assertEqual(current["result"], "pass")
            self.assertEqual(stale["result"], "not_run")
            self.assertEqual(stale["historical_report_ids"], ["report-codex"])
            self.assertEqual(other_host["result"], "not_run")
            self.assertEqual(other_source["result"], "not_run")

    def test_report_rejects_unknown_stage_or_result(self) -> None:
        """Permissive values make a report look verified without a defined meaning."""
        from hwskill.verification.models import VerificationReportError, validate_report

        invalid = self.report()
        invalid["stages"] = {"metadata": {"result": "pass", "reason": "ok"}}
        with self.assertRaises(VerificationReportError):
            validate_report(invalid)

    def test_current_status_requires_host_version_platform_and_active_lifecycle(self) -> None:
        """A passing Linux/Codex version is not evidence for another target combination."""
        from hwskill.verification.report import current_installation_status

        report = self.report()
        material = {**report, "lifecycle": "active"}
        self.assertEqual(current_installation_status([report], material, host="codex", host_version="2.0", os_name="linux", arch="x86_64")["result"], "not_run")
        self.assertEqual(current_installation_status([report], material, host="codex", host_version="1.0", os_name="darwin", arch="x86_64")["result"], "not_run")
        self.assertEqual(current_installation_status([report], {**material, "lifecycle": "withdrawn"}, host="codex", host_version="1.0", os_name="linux", arch="x86_64")["result"], "not_run")

    def test_report_store_never_overwrites_a_conflicting_id(self) -> None:
        """A report-id collision must preserve the original evidence."""
        from hwskill.verification.models import VerificationReportError
        from hwskill.verification.report import ReportStore

        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            report = self.report()
            store.write(report)
            changed = {**report, "host_version": "2.0"}
            with self.assertRaises(VerificationReportError):
                store.write(changed)

    def test_latest_matching_report_failure_overrides_older_pass(self) -> None:
        """Historical success must not mask a newer failed installation for the same target."""
        from hwskill.verification.report import current_installation_status

        passed = self.report()
        failed = {**self.report(), "report_id": "newer", "executed_at": "2026-09-11T00:00:01Z"}
        failed["stages"] = {**failed["stages"], "installation": {"result": "fail", "reason": "copy failed"}}
        material = {**passed, "lifecycle": "active"}
        self.assertEqual(current_installation_status([passed, failed], material, host="codex", host_version="1.0", os_name="linux", arch="x86_64")["result"], "fail")

    def test_same_timestamp_uses_conservative_deterministic_result(self) -> None:
        """Report ID ordering must never let a simultaneous pass hide fail or blocked evidence."""
        from hwskill.verification.report import current_installation_status

        passed = self.report()
        passed["report_id"] = "z-pass"
        failed = self.report()
        failed["report_id"] = "a-fail"
        failed["stages"] = {**failed["stages"], "installation": {"result": "fail", "reason": "copy failed"}}
        blocked = self.report()
        blocked["report_id"] = "m-blocked"
        blocked["stages"] = {**blocked["stages"], "installation": {"result": "blocked", "reason": "policy stopped"}}
        material = {**passed, "lifecycle": "active"}
        first = current_installation_status([passed, blocked, failed], material, host="codex", host_version="1.0", os_name="linux", arch="x86_64")
        second = current_installation_status([failed, passed, blocked], material, host="codex", host_version="1.0", os_name="linux", arch="x86_64")
        self.assertEqual(first["result"], "fail")
        self.assertEqual(first["report_id"], "a-fail")
        self.assertEqual(second, first)

    def test_auditable_json_run_marks_behavior_not_run_without_agent_credentials(self) -> None:
        """A deterministic installer cannot claim agent behavior evidence it did not execute."""
        from hwskill.verification.report import build_installation_report

        report = build_installation_report(
            material={
                "skill_id": "example/hosted", "entry_digest": "sha256:entry", "install_digest": "sha256:install",
                "source_identity": {"kind": "hosted", "identity": "hosted:example", "resolved_revision": "a" * 40, "content_digest": "sha256:" + "b" * 64},
            },
            host="codex", host_version="1.0", runner_identity="test-runner", installation_result="pass", installation_reason="copied fixture",
        )
        serialized = json.loads(json.dumps(report))
        self.assertEqual(serialized["stages"]["installation"]["result"], "pass")
        self.assertEqual(serialized["stages"]["behavior"]["result"], "not_run")

    def test_install_check_script_emits_auditable_json_for_local_external_checkout(self) -> None:
        """The command must report a local checkout, not silently perform a network acquisition."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "checkout"
            source.mkdir()
            (source / "SKILL.md").write_text("# external\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://example.test/repo"], check=True)
            subprocess.run(["git", "-C", str(source), "add", "SKILL.md"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture"], check=True)
            revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", str(source), "tag", "v1.2.3"], check=True)
            source_identity = {"kind": "external", "identity": "git:https://example.test/repo\0.", "requested_ref": "v1.2.3", "resolved_revision": None, "content_digest": None}
            entry = {"id": "example/external", "lifecycle": "active", "source": {"kind": "external", "identity": source_identity["identity"]}}
            from hwskill.directory.digest import canonical_json, sha256_bytes
            entry_digest = sha256_bytes(canonical_json(entry))
            install = {"schema_version": 1, "skill_id": "example/external", "entry_digest": entry_digest, "source": {"kind": "external", "repository": "https://example.test/repo", "path": ".", "requested_ref": "v1.2.3", "resolved_revision": None}, "install": {"method": "directory", "default_scope": "project", "instructions_url": None}, "verification_summary": {}}
            from hwskill.verification.models import install_digest
            install["install_digest"] = install_digest(install)
            catalog = {"schema_version": 1, "source_commit": None, "entries": [{"entry": entry, "entry_digest": entry_digest, "source_identity": source_identity, "lifecycle": "active", "install_capability": "installable", "verification_summary": {}}]}
            published = root / "published"
            (published / "skills/example/external").mkdir(parents=True)
            (published / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
            (published / "skills/example/external/install.json").write_text(json.dumps(install), encoding="utf-8")
            completed = subprocess.run([
                sys.executable, "scripts/verification/run_install_check.py", "--published-root", str(published),
                "--skill-id", "example/external", "--source-dir", str(source), "--target-root", str(root / "project/.codex/skills"),
                "--report-dir", str(root / "reports"), "--host", "codex", "--host-version", "1.0", "--runner-identity", "test-runner",
            ], text=True, capture_output=True, check=False, env=self.cli_env(root))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["stages"]["installation"]["result"], "pass")
            self.assertEqual(report["stages"]["behavior"]["result"], "not_run")
            self.assertEqual(report["source_identity"]["requested_ref"], "v1.2.3")
            self.assertEqual(report["source_identity"]["resolved_revision"], revision)
            from hwskill.verification.models import bind_install_material
            from hwskill.verification.report import current_installation_status
            fresh_material = bind_install_material(catalog["entries"][0], install)
            current = current_installation_status(
                [report], fresh_material, host="codex", host_version="1.0",
                os_name=report["os"], arch=report["arch"],
            )
            different_ref = {
                **fresh_material,
                "source_identity": {**fresh_material["source_identity"], "requested_ref": "v2.0.0"},
                "install_digest": "sha256:different-ref",
            }
            self.assertEqual(current["result"], "pass")
            self.assertEqual(current_installation_status(
                [report], different_ref, host="codex", host_version="1.0",
                os_name=report["os"], arch=report["arch"],
            )["result"], "not_run")

    def test_git_archive_extraction_is_python310_compatible_and_rejects_unsafe_members(self) -> None:
        """Using 3.12-only filter= or accepting tar links/path traversal breaks the verifier boundary."""
        from scripts.verification.run_install_check import _safe_extract_git_archive

        def archive(member: tarfile.TarInfo, payload: bytes = b"") -> bytes:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as bundle:
                if member.isfile():
                    member.size = len(payload)
                    bundle.addfile(member, io.BytesIO(payload))
                else:
                    bundle.addfile(member)
            return buffer.getvalue()

        with TemporaryDirectory() as directory, patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("3.12-only API")):
            root = Path(directory)
            _safe_extract_git_archive(archive(tarfile.TarInfo("SKILL.md"), b"# safe\n"), root / "safe")
            self.assertEqual((root / "safe/SKILL.md").read_text(encoding="utf-8"), "# safe\n")
            for name, kind in (("../escape", "file"), ("/absolute", "file"), ("link", "symlink"), ("hard", "hardlink")):
                member = tarfile.TarInfo(name)
                if kind == "symlink":
                    member.type, member.linkname = tarfile.SYMTYPE, "SKILL.md"
                elif kind == "hardlink":
                    member.type, member.linkname = tarfile.LNKTYPE, "SKILL.md"
                with self.subTest(name=name), self.assertRaises(ValueError):
                    _safe_extract_git_archive(archive(member, b"bad"), root / f"bad-{kind}")

    def test_external_snapshot_archives_the_captured_commit_not_mutable_head(self) -> None:
        """A concurrent HEAD move must not make report revision and archived bytes describe different commits."""
        from scripts.verification import run_install_check

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "checkout"
            source.mkdir()
            subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://example.test/repo"], check=True)
            (source / "SKILL.md").write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "SKILL.md"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "first"], check=True)
            first = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", str(source), "tag", "v1.2.3"], check=True)
            (source / "SKILL.md").write_text("second\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "SKILL.md"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "second"], check=True)
            second = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", str(source), "reset", "--hard", first, "-q"], check=True)
            identity = {"kind": "external", "identity": "git:https://example.test/repo\0.", "requested_ref": "v1.2.3", "resolved_revision": None, "content_digest": None}
            material = {"source_identity": identity, "install_json": {"source": {"repository": "https://example.test/repo", "path": ".", "requested_ref": "v1.2.3", "resolved_revision": None}}}
            original_git = run_install_check._git

            def move_head_after_capture(source_fd: int, *arguments: str, binary: bool = False):
                result = original_git(source_fd, *arguments, binary=binary)
                if arguments == ("rev-parse", "HEAD"):
                    subprocess.run(["git", "-C", str(source), "reset", "--hard", second, "-q"], check=True)
                return result

            snapshot = root / "snapshot"
            with patch.object(run_install_check, "_git", side_effect=move_head_after_capture):
                resolved, snapshot_path = run_install_check._acquire_external(material, source, snapshot)
            self.assertEqual(resolved["source_identity"]["resolved_revision"], first)
            self.assertEqual((snapshot_path / "SKILL.md").read_text(encoding="utf-8"), "first\n")

    def test_report_store_rejects_symlink_ancestor_before_creating_any_directory(self) -> None:
        """Validating after mkdir would mutate storage outside the selected report namespace."""
        from hwskill.verification.models import VerificationReportError
        from hwskill.verification.report import ReportStore

        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(VerificationReportError):
                ReportStore(root / "linked/new/reports").write(self.report())
            self.assertFalse((outside / "new").exists())

    def test_report_store_rejects_symlink_during_read(self) -> None:
        """Reading through a report symlink would accept evidence outside the store."""
        from hwskill.verification.models import VerificationReportError
        from hwskill.verification.report import ReportStore

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReportStore(root / "reports")
            stored = store.write(self.report())
            outside = root / "outside.json"
            outside.write_bytes(stored.read_bytes())
            stored.unlink()
            stored.symlink_to(outside)
            with self.assertRaises(VerificationReportError):
                store.read_all()

    def test_bind_requires_all_source_identity_fields_to_match_install_locator(self) -> None:
        """Matching only source kind permits a validly re-digested install file to change source."""
        from hwskill.verification.models import VerificationReportError, bind_install_material, install_digest

        identity = {"kind": "external", "identity": "git:https://example.test/repo\0skills/a", "requested_ref": "main", "resolved_revision": None, "content_digest": None}
        item = {
            "entry": {"id": "example/external", "lifecycle": "active", "source": {"kind": "external", "identity": identity["identity"]}},
            "entry_digest": "pending", "source_identity": identity, "lifecycle": "active",
            "install_capability": "installable", "verification_summary": {},
        }
        from hwskill.directory.digest import canonical_json, sha256_bytes
        item["entry_digest"] = sha256_bytes(canonical_json(item["entry"]))
        install = {
            "schema_version": 1, "skill_id": "example/external", "entry_digest": item["entry_digest"],
            "source": {"kind": "external", "repository": "https://evil.test/repo", "path": "skills/a", "requested_ref": "main", "resolved_revision": None},
            "install": {"method": "directory", "default_scope": "project", "instructions_url": None}, "verification_summary": {},
        }
        install["install_digest"] = install_digest(install)
        with self.assertRaises(VerificationReportError):
            bind_install_material(item, install)
        changed_entry = {**item, "entry": {**item["entry"], "source": {"kind": "external", "identity": "git:https://other.test/repo\0skills/a"}}}
        changed_entry["entry_digest"] = sha256_bytes(canonical_json(changed_entry["entry"]))
        install["source"]["repository"] = "https://example.test/repo"
        install["entry_digest"] = changed_entry["entry_digest"]
        install["install_digest"] = install_digest(install)
        with self.assertRaises(VerificationReportError):
            bind_install_material(changed_entry, install)

    def test_bind_rejects_hosted_path_or_revision_drift(self) -> None:
        """A hosted install file cannot redirect a catalog identity or select another commit."""
        from hwskill.verification.models import VerificationReportError, bind_install_material, install_digest

        identity = {"kind": "hosted", "identity": "hosted:skills-src/example", "resolved_revision": "a" * 40, "content_digest": "sha256:" + "b" * 64}
        item = {
            "entry": {"id": "example/hosted", "lifecycle": "active", "source": {"kind": "hosted", "identity": identity["identity"]}},
            "entry_digest": "pending", "source_identity": identity, "lifecycle": "active",
            "install_capability": "installable", "verification_summary": {},
        }
        from hwskill.directory.digest import canonical_json, sha256_bytes
        item["entry_digest"] = sha256_bytes(canonical_json(item["entry"]))
        install = {
            "schema_version": 1, "skill_id": "example/hosted", "entry_digest": item["entry_digest"],
            "source": {"kind": "hosted", "path": "skills-src/other", "resolved_revision": "c" * 40},
            "install": {"method": "directory", "default_scope": "project", "instructions_url": None}, "verification_summary": {},
        }
        install["install_digest"] = install_digest(install)
        with self.assertRaises(VerificationReportError):
            bind_install_material(item, install)

    def test_bind_recomputes_catalog_entry_digest(self) -> None:
        """Matching two copied digest strings does not bind either one to catalog bytes."""
        from hwskill.directory.digest import canonical_json, sha256_bytes
        from hwskill.verification.models import VerificationReportError, bind_install_material, install_digest

        identity = {"kind": "hosted", "identity": "hosted:skills-src/example", "resolved_revision": "a" * 40, "content_digest": "sha256:" + "b" * 64}
        entry = {"id": "example/hosted", "name": "reviewed", "lifecycle": "active", "source": {"kind": "hosted", "identity": identity["identity"]}}
        entry_digest = sha256_bytes(canonical_json(entry))
        item = {"entry": {**entry, "name": "tampered"}, "entry_digest": entry_digest, "source_identity": identity, "lifecycle": "active", "install_capability": "installable", "verification_summary": {}}
        install = {"schema_version": 1, "skill_id": "example/hosted", "entry_digest": entry_digest, "source": {"kind": "hosted", "path": "skills-src/example", "resolved_revision": "a" * 40}, "install": {"method": "directory", "default_scope": "project", "instructions_url": None}, "verification_summary": {}}
        install["install_digest"] = install_digest(install)
        with self.assertRaises(VerificationReportError):
            bind_install_material(item, install)

    def test_cli_persists_schema_valid_reports_for_each_failure_stage(self) -> None:
        """Returning an ad-hoc error object loses the required staged audit record."""
        from hwskill.directory.digest import canonical_json, sha256_bytes
        from hwskill.verification.models import install_digest, validate_report

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "checkout"
            source.mkdir()
            (source / "SKILL.md").write_text("# external\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://wrong.test/repo"], check=True)
            subprocess.run(["git", "-C", str(source), "add", "SKILL.md"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture"], check=True)
            revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
            identity = {"kind": "external", "identity": "git:https://example.test/repo\0.", "requested_ref": revision, "resolved_revision": None, "content_digest": None}
            install = {
                "schema_version": 1, "skill_id": "example/external", "entry_digest": "pending",
                "source": {"kind": "external", "repository": "https://example.test/repo", "path": ".", "requested_ref": revision, "resolved_revision": None},
                "install": {"method": "directory", "default_scope": "project", "instructions_url": None}, "verification_summary": {},
            }
            item = {"entry": {"id": "example/external", "lifecycle": "active", "source": {"kind": "external", "identity": identity["identity"]}}, "entry_digest": "pending", "source_identity": identity, "lifecycle": "active", "install_capability": "installable", "verification_summary": {}}
            item["entry_digest"] = sha256_bytes(canonical_json(item["entry"]))
            install["entry_digest"] = item["entry_digest"]
            install["install_digest"] = install_digest(install)

            def run_case(name: str, mutate) -> dict[str, object]:
                case_item = json.loads(json.dumps(item))
                case_install = json.loads(json.dumps(install))
                mutate(case_item, case_install)
                if case_install.pop("_keep_digest", False) is False:
                    case_install["install_digest"] = install_digest(case_install)
                published = root / name / "published"
                report_dir = root / name / "reports"
                (published / "skills/example/external").mkdir(parents=True)
                (published / "catalog.json").write_text(json.dumps({"schema_version": 1, "source_commit": None, "entries": [case_item]}), encoding="utf-8")
                (published / "skills/example/external/install.json").write_text(json.dumps(case_install), encoding="utf-8")
                completed = subprocess.run([
                    sys.executable, "scripts/verification/run_install_check.py", "--published-root", str(published),
                    "--skill-id", "example/external", "--source-dir", str(source), "--target-root", str(root / name / "project/.codex/skills"),
                    "--report-dir", str(report_dir), "--host", "codex", "--host-version", "1.0", "--runner-identity", "test-runner",
                ], text=True, capture_output=True, check=False, env=self.cli_env(root))
                self.assertEqual(completed.returncode, 1, completed.stderr)
                report = validate_report(json.loads(completed.stdout))
                self.assertEqual(len(list(report_dir.glob("*.json"))), 1)
                return report

            wrong_origin = run_case("origin", lambda _item, _install: None)
            self.assertEqual(wrong_origin["stages"]["metadata"]["result"], "pass")
            self.assertEqual(wrong_origin["stages"]["acquisition"]["result"], "fail")

            dirty_marker = source / "dirty"
            dirty_marker.write_text("dirty\n", encoding="utf-8")
            def accept_wrong_origin(entry, changed_install) -> None:
                changed_install["source"].update(repository="https://wrong.test/repo")
                wrong_identity = "git:https://wrong.test/repo\0."
                entry["source_identity"]["identity"] = wrong_identity
                entry["entry"]["source"]["identity"] = wrong_identity
                entry["entry_digest"] = sha256_bytes(canonical_json(entry["entry"]))
                changed_install["entry_digest"] = entry["entry_digest"]

            dirty = run_case("dirty", accept_wrong_origin)
            self.assertEqual(dirty["stages"]["acquisition"]["result"], "fail")
            dirty_marker.unlink()

            def withdraw(entry, changed_install) -> None:
                entry.update(lifecycle="withdrawn", install_capability="disabled")
                entry["entry"].update(lifecycle="withdrawn")
                entry["entry_digest"] = sha256_bytes(canonical_json(entry["entry"]))
                changed_install["entry_digest"] = entry["entry_digest"]

            withdrawn = run_case("withdrawn", withdraw)
            self.assertEqual(withdrawn["stages"]["installation"]["result"], "blocked")

            mismatch = run_case("bind", lambda _item, changed: changed.update(entry_digest="sha256:other"))
            self.assertEqual(mismatch["stages"]["metadata"]["result"], "fail")
            self.assertEqual(mismatch["stages"]["installation"]["result"], "not_run")

    def test_hosted_source_validation_failures_are_acquisition_failures(self) -> None:
        """A source symlink or digest mismatch occurs before installation and must not claim acquisition pass."""
        from hwskill.directory.digest import canonical_json, sha256_bytes
        from hwskill.verification.models import install_digest, validate_report

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("# hosted\n", encoding="utf-8")
            identity = {"kind": "hosted", "identity": "hosted:skills-src/example", "resolved_revision": "a" * 40, "content_digest": "sha256:" + "0" * 64}
            entry = {"id": "example/hosted", "lifecycle": "active", "source": {"kind": "hosted", "identity": identity["identity"]}}
            entry_digest = sha256_bytes(canonical_json(entry))
            install = {"schema_version": 1, "skill_id": "example/hosted", "entry_digest": entry_digest, "source": {"kind": "hosted", "path": "skills-src/example", "resolved_revision": "a" * 40}, "install": {"method": "directory", "default_scope": "project", "instructions_url": None}, "verification_summary": {}}
            install["install_digest"] = install_digest(install)
            item = {"entry": entry, "entry_digest": entry_digest, "source_identity": identity, "lifecycle": "active", "install_capability": "installable", "verification_summary": {}}
            published = root / "published"
            (published / "skills/example/hosted").mkdir(parents=True)
            (published / "catalog.json").write_text(json.dumps({"schema_version": 1, "source_commit": "a" * 40, "entries": [item]}), encoding="utf-8")
            (published / "skills/example/hosted/install.json").write_text(json.dumps(install), encoding="utf-8")
            linked = root / "linked-source"
            linked.symlink_to(source, target_is_directory=True)
            for name, candidate in (("digest", source), ("symlink", linked)):
                completed = subprocess.run([
                    sys.executable, "scripts/verification/run_install_check.py", "--published-root", str(published),
                    "--skill-id", "example/hosted", "--source-dir", str(candidate), "--target-root", str(root / name / "project/.codex/skills"),
                    "--report-dir", str(root / name / "reports"), "--host", "codex", "--host-version", "1.0", "--runner-identity", "test-runner",
                ], text=True, capture_output=True, check=False, env=self.cli_env(root))
                self.assertEqual(completed.returncode, 1, completed.stderr)
                report = validate_report(json.loads(completed.stdout))
                self.assertEqual(report["stages"]["metadata"]["result"], "pass")
                self.assertEqual(report["stages"]["acquisition"]["result"], "fail")
                self.assertEqual(report["stages"]["installation"]["result"], "not_run")

            for name, host, supplied_version, detected_version in (
                ("invented", "invented-agent", "1.0", "1.0"),
                ("mismatch", "codex", "1.0", "2.0"),
                ("wrong-target", "codex", "1.0", "1.0"),
            ):
                target_root = root / name / ("arbitrary-target" if name == "wrong-target" else "project/.codex/skills")
                completed = subprocess.run([
                    sys.executable, "scripts/verification/run_install_check.py", "--published-root", str(published),
                    "--skill-id", "example/hosted", "--source-dir", str(source), "--target-root", str(target_root),
                    "--report-dir", str(root / name / "reports"), "--host", host, "--host-version", supplied_version,
                    "--runner-identity", "test-runner",
                ], text=True, capture_output=True, check=False, env=self.cli_env(root, detected_version))
                self.assertEqual(completed.returncode, 1, completed.stderr)
                report = validate_report(json.loads(completed.stdout))
                self.assertEqual(report["stages"]["installation"]["result"], "blocked")
                self.assertFalse((root / name / "project/.codex/skills/example/hosted").exists())

            marker = root / "EXECUTED_UNREVIEWED_SKILL"
            malicious = source / "install.sh"
            malicious.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf 'codex 1.0\\n'\n", encoding="utf-8")
            malicious.chmod(0o700)
            rejected = subprocess.run([
                sys.executable, "scripts/verification/run_install_check.py", "--published-root", str(published),
                "--skill-id", "example/hosted", "--source-dir", str(source), "--target-root", str(root / "rejected/project/.codex/skills"),
                "--report-dir", str(root / "rejected/reports"), "--host", "codex", "--host-version", "1.0",
                "--host-executable", str(malicious), "--runner-identity", "test-runner",
            ], text=True, capture_output=True, check=False, env=self.cli_env(root))
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("unrecognized arguments: --host-executable", rejected.stderr)
            self.assertFalse(marker.exists())

            source_host = source / "codex"
            source_host.write_bytes(malicious.read_bytes())
            source_host.chmod(0o700)
            forbidden_env = {**os.environ, "PYTHONPATH": "src", "PATH": f"{source}{os.pathsep}{os.environ.get('PATH', '')}"}
            forbidden = subprocess.run([
                sys.executable, "scripts/verification/run_install_check.py", "--published-root", str(published),
                "--skill-id", "example/hosted", "--source-dir", str(source), "--target-root", str(root / "forbidden/project/.codex/skills"),
                "--report-dir", str(root / "forbidden/reports"), "--host", "codex", "--host-version", "1.0", "--runner-identity", "test-runner",
            ], text=True, capture_output=True, check=False, env=forbidden_env)
            self.assertEqual(forbidden.returncode, 1, forbidden.stderr)
            self.assertEqual(validate_report(json.loads(forbidden.stdout))["stages"]["installation"]["result"], "blocked")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
