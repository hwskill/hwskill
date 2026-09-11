from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


def _digest(value: object) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class IsolatedInstallationTests(unittest.TestCase):
    def material(self, *, kind: str, lifecycle: str = "active", revision: str = "a" * 40) -> dict[str, object]:
        source_identity = {
            "kind": kind,
            "identity": f"{kind}:example/skill",
            "resolved_revision": revision,
            "content_digest": getattr(self, "_source_digest", "sha256:" + "b" * 64),
        }
        install = {"method": "directory", "default_scope": "project", "instructions_url": None}
        install_json = {
            "schema_version": 1,
            "skill_id": f"example/{kind}",
            "entry_digest": _digest({"skill_id": f"example/{kind}", "source_identity": source_identity}),
            "source": {"kind": kind, "resolved_revision": revision},
            "install": install,
            "verification_summary": {},
        }
        return {
            "skill_id": f"example/{kind}",
            "entry_digest": install_json["entry_digest"],
            "install_digest": _digest(install_json),
            "source_identity": source_identity,
            "install_json": install_json,
            "lifecycle": lifecycle,
            "install_capability": "installable",
        }

    def source(self, directory: Path) -> Path:
        source = directory / "source"
        source.mkdir()
        (source / "SKILL.md").write_text("# safe skill\n", encoding="utf-8")
        (source / "reference.md").write_text("complete resource\n", encoding="utf-8")
        # The installer must only copy this file; it must never run it.
        (source / "install.sh").write_text("touch SHOULD_NOT_EXIST\n", encoding="utf-8")
        from hwskill.directory.hosted_content import directory_digest
        self._source_digest = directory_digest(source)
        return source

    def test_hosted_installs_complete_directory_idempotently_without_execution(self) -> None:
        """Replacing copy semantics with a command runner would execute unreviewed input."""
        from hwskill.verification.installer import install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            target = root / "home" / ".codex" / "skills"
            first = install_reviewed_directory(self.material(kind="hosted"), source, target)
            second = install_reviewed_directory(self.material(kind="hosted"), source, target)
            installed = target / "example/hosted"
            self.assertEqual(first.result, "pass")
            self.assertEqual(second.result, "pass")
            self.assertTrue(second.idempotent)
            self.assertEqual((installed / "reference.md").read_text(encoding="utf-8"), "complete resource\n")
            self.assertTrue((installed / "install.sh").is_file())
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())

    def test_external_requires_acquired_local_checkout_and_never_fetches(self) -> None:
        """Allowing the install phase to fetch makes its evidence non-deterministic."""
        from hwskill.verification.installer import AcquisitionRequired, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project" / ".codex" / "skills"
            with self.assertRaises(AcquisitionRequired):
                install_reviewed_directory(self.material(kind="external"), root / "missing-checkout", target)
            source = self.source(root)
            result = install_reviewed_directory(self.material(kind="external"), source, target)
            self.assertEqual(result.result, "pass")
            self.assertTrue((target / "example/external/SKILL.md").is_file())

    def test_conflict_and_failed_copy_preserve_existing_directory(self) -> None:
        """A conflict or incomplete source must fail closed without replacing user files."""
        from hwskill.verification.installer import InstallConflict, InstallationError, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            target = root / "project" / ".codex" / "skills"
            existing = target / "example/hosted"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("# user skill\n", encoding="utf-8")
            with self.assertRaises(InstallConflict):
                install_reviewed_directory(self.material(kind="hosted"), source, target)
            self.assertEqual((existing / "SKILL.md").read_text(encoding="utf-8"), "# user skill\n")
            incomplete = root / "incomplete"
            incomplete.mkdir()
            with self.assertRaises(InstallationError):
                install_reviewed_directory(self.material(kind="external"), incomplete, target)
            self.assertEqual((existing / "SKILL.md").read_text(encoding="utf-8"), "# user skill\n")

    def test_withdrawn_or_unbound_or_unfixed_material_stops_before_copy(self) -> None:
        """Changing identity, digest, lifecycle, or revision must invalidate installation."""
        from hwskill.verification.installer import InstallBlocked, InstallInputError, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            target = root / "project" / ".codex" / "skills"
            with self.assertRaises(InstallBlocked):
                install_reviewed_directory(self.material(kind="hosted", lifecycle="withdrawn"), source, target)
            changed = self.material(kind="hosted")
            changed["entry_digest"] = "sha256:" + "0" * 64
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(changed, source, target)
            unfixed = self.material(kind="hosted")
            unfixed["source_identity"] = {**unfixed["source_identity"], "resolved_revision": None}
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(unfixed, source, target)
            self.assertFalse(target.exists())

    def test_rejects_acquired_content_digest_mismatch_and_source_symlink(self) -> None:
        """A claimed revision is not evidence unless the reviewed bytes are bound too."""
        from hwskill.verification.installer import InstallInputError, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            material = self.material(kind="hosted")
            material["source_identity"] = {**material["source_identity"], "content_digest": "sha256:" + "0" * 64}
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(material, source, root / "target")
            link = root / "linked"
            link.symlink_to(source, target_is_directory=True)
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(self.material(kind="hosted"), link, root / "target")

    def test_rejects_non_directory_or_disabled_install_material(self) -> None:
        """This copier cannot treat upstream guidance as a reviewed directory install."""
        from hwskill.verification.installer import InstallInputError, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            material = self.material(kind="hosted")
            material["install_json"]["install"]["method"] = "upstream"
            material["install_digest"] = _digest(material["install_json"])
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(material, source, root / "target")

    def test_rejects_symlink_in_target_root_ancestry_without_writing_through_it(self) -> None:
        """A check performed after mkdir would already have modified the symlink target."""
        from hwskill.verification.installer import InstallInputError, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            outside = root / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            target = root / "linked" / "new" / "skills"
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(self.material(kind="hosted"), source, target)
            self.assertFalse((outside / "new").exists())

    def test_existing_destination_symlink_is_never_followed_for_idempotence(self) -> None:
        """Comparing an existing symlink as a directory can bless content outside the target."""
        from hwskill.verification.installer import InstallConflict, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            outside = root / "outside"
            outside.mkdir()
            (outside / "SKILL.md").write_text("# safe skill\n", encoding="utf-8")
            (outside / "reference.md").write_text("complete resource\n", encoding="utf-8")
            (outside / "install.sh").write_text("touch SHOULD_NOT_EXIST\n", encoding="utf-8")
            target = root / "target"
            parent = target / "example"
            parent.mkdir(parents=True)
            (parent / "hosted").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(InstallConflict):
                install_reviewed_directory(self.material(kind="hosted"), source, target)
            self.assertEqual((outside / "SKILL.md").read_text(encoding="utf-8"), "# safe skill\n")

    def test_failed_atomic_publish_leaves_no_partial_and_preserves_concurrent_directory(self) -> None:
        """Failure cleanup must not recursively delete a directory another writer published."""
        from hwskill.verification.installer import InstallConflict, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            target = root / "target"

            def concurrent_publish(source_parent_fd: int, source_name: str, target_parent_fd: int, target_name: str) -> None:
                os.mkdir(target_name, dir_fd=target_parent_fd)
                marker_fd = os.open(f"{target_name}/owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=target_parent_fd)
                os.write(marker_fd, b"other writer\n")
                os.close(marker_fd)
                raise InstallConflict("concurrent publication")

            with patch("hwskill.verification.installer._rename_noreplace", side_effect=concurrent_publish):
                with self.assertRaises(InstallConflict):
                    install_reviewed_directory(self.material(kind="hosted"), source, target)
            destination = target / "example/hosted"
            self.assertEqual((destination / "owner").read_text(encoding="utf-8"), "other writer\n")
            self.assertFalse(any(path.name.startswith(".hosted.install-") for path in destination.parent.iterdir()))

    def test_source_cannot_also_be_the_install_destination(self) -> None:
        """Treating the acquired checkout as installed defeats isolation and can recurse into staging."""
        from hwskill.verification.installer import InstallInputError, install_reviewed_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            material = self.material(kind="hosted")
            material["skill_id"] = "source"
            material["install_json"]["skill_id"] = "source"
            material["install_json"]["entry_digest"] = material["entry_digest"]
            material["install_digest"] = _digest(material["install_json"])
            with self.assertRaises(InstallInputError):
                install_reviewed_directory(material, source, root)


if __name__ == "__main__":
    unittest.main()
