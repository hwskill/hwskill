from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        digest.update(relative.encode())
        digest.update(b"\0")
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
        elif path.is_symlink():
            digest.update(os.readlink(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _legacy_repository(root: Path) -> None:
    (root / "registry").mkdir(parents=True)
    (root / "sources").mkdir()
    catalog = {
        "schema_version": 2,
        "skills": [
            {
                "id": "local/manual",
                "name": "Manual",
                "description": "A manually maintained skill.",
                "layer": "l1",
                "source_kind": "manual",
                "source_id": None,
                "revision": "manual",
                "license": "mixed-or-unspecified",
                "content_digest": "sha256:" + "1" * 64,
                "path": "skills-src/l1/local/manual",
            },
            {
                "id": "vendor/tool",
                "name": "Tool",
                "description": "An upstream snapshot.",
                "layer": "l2",
                "source_kind": "upstream",
                "source_id": "vendor",
                "revision": "2" * 40,
                "license": "MIT",
                "content_digest": "sha256:" + "3" * 64,
                "path": "skills-src/l2/vendor/tool",
            },
        ],
    }
    (root / "registry/catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (root / "sources/vendor.yaml").write_text(
        """schema_version: 2
source_id: vendor
kind: upstream
upstream:
  repository: https://example.test/vendor/tools.git
  track: refs/tags/v1.2.3
  skills_path: skills
  ignore: []
defaults:
  namespace: vendor
  layer: l2
  license: MIT
resolved:
  revision: "2222222222222222222222222222222222222222"
  skills:
    - path: tool
      id: vendor/tool
      layer: l2
      content_digest: sha256:3333333333333333333333333333333333333333333333333333333333333333
""",
        encoding="utf-8",
    )
    hosted = root / "skills-src/l1/local/manual"
    hosted.mkdir(parents=True)
    (hosted / "SKILL.md").write_text("# Manual\n", encoding="utf-8")


class MigrationPreviewTests(unittest.TestCase):
    def test_manual_and_upstream_snapshot_become_hosted_and_external_candidates(self) -> None:
        from hwskill.directory.migration import migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            preview = migration_preview(repository, base / "preview")
            manual = json.loads((preview.out_dir / "candidates/entries/l1/local/manual.json").read_text())
            upstream = json.loads((preview.out_dir / "candidates/entries/l2/vendor/tool.json").read_text())

            self.assertEqual(manual["entry"]["source"], {"kind": "hosted", "path": "skills-src/l1/local/manual"})
            self.assertEqual(manual["entry"]["install"]["method"], "directory")
            self.assertIn("source.content", manual["migration_review_required"])
            self.assertEqual(
                upstream["entry"]["source"]["locator"],
                {
                    "type": "git",
                    "repository": "https://example.test/vendor/tools.git",
                    "path": "skills/tool",
                    "requested_ref": "refs/tags/v1.2.3",
                },
            )
            self.assertEqual(upstream["entry"]["source"]["publicity"], "unknown")
            self.assertIn("source.publicity", upstream["migration_review_required"])
            self.assertEqual(upstream["entry"]["install"]["method"], "upstream")
            self.assertEqual(preview.candidate_ids, ("local/manual", "vendor/tool"))
            self.assertEqual(preview.unconvertible, ())

    def test_unknown_locator_and_install_are_listed_without_changing_source_or_user_config(self) -> None:
        from hwskill.directory.migration import migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            catalog_path = repository / "registry/catalog.json"
            catalog = json.loads(catalog_path.read_text())
            catalog["skills"][1]["source_id"] = "missing"
            catalog["skills"][0]["install"] = {"method": "curl-pipe-shell"}
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            user_config = repository / ".hwskills/profile.yaml"
            user_config.parent.mkdir()
            user_config.write_text("profiles: [legacy]\n", encoding="utf-8")
            before = _tree_digest(repository)

            preview = migration_preview(repository, base / "preview")

            self.assertEqual(_tree_digest(repository), before)
            self.assertEqual(user_config.read_text(), "profiles: [legacy]\n")
            self.assertFalse((preview.out_dir / ".hwskills").exists())
            self.assertEqual({item.code for item in preview.unconvertible}, {"unknown-install", "unknown-locator"})
            manifest = json.loads((preview.out_dir / "unconvertible.json").read_text())
            self.assertEqual({item["skill_id"] for item in manifest["items"]}, {"local/manual", "vendor/tool"})

    def test_upstream_snapshot_must_fully_match_the_catalog_record(self) -> None:
        from hwskill.directory.migration import migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            for mutation in ("source-schema", "kind", "source-id", "revision", "layer", "digest", "path"):
                repository = base / mutation
                repository.mkdir()
                _legacy_repository(repository)
                source_path = repository / "sources/vendor.yaml"
                source = source_path.read_text()
                if mutation == "source-schema":
                    source = source.replace("schema_version: 2", "schema_version: 1")
                elif mutation == "kind":
                    source = source.replace("kind: upstream", "kind: private-snapshot")
                elif mutation == "source-id":
                    source = source.replace("source_id: vendor", "source_id: other")
                elif mutation == "revision":
                    source = source.replace(
                        'revision: "2222222222222222222222222222222222222222"',
                        'revision: "' + "4" * 40 + '"',
                    )
                elif mutation == "layer":
                    source = source.replace("      layer: l2", "      layer: l1")
                elif mutation == "digest":
                    source = source.replace("sha256:" + "3" * 64, "sha256:" + "4" * 64)
                else:
                    source = source.replace("    - path: tool", "    - path: ../tool")
                source_path.write_text(source, encoding="utf-8")

                preview = migration_preview(repository, base / f"preview-{mutation}")

                self.assertNotIn("vendor/tool", preview.candidate_ids, mutation)
                self.assertTrue(
                    any(item.skill_id == "vendor/tool" and item.code == "snapshot-mismatch" for item in preview.unconvertible),
                    mutation,
                )

    def test_missing_manual_source_is_not_presented_as_a_complete_candidate(self) -> None:
        from hwskill.directory.migration import migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            (repository / "skills-src/l1/local/manual/SKILL.md").unlink()

            preview = migration_preview(repository, base / "preview")

            self.assertNotIn("local/manual", preview.candidate_ids)
            self.assertTrue(any(item.code == "missing-hosted-source" for item in preview.unconvertible))

    def test_output_must_be_new_nonoverlapping_and_have_no_symlink_ancestor(self) -> None:
        from hwskill.directory.migration import MigrationSafetyError, migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            existing = base / "existing"
            existing.mkdir()
            with self.assertRaises(MigrationSafetyError):
                migration_preview(repository, existing)
            with self.assertRaises(MigrationSafetyError):
                migration_preview(repository, repository / "preview")
            safe_parent = base / "safe"
            safe_parent.mkdir()
            with self.assertRaises(MigrationSafetyError):
                migration_preview(repository, safe_parent / ".." / "escaped-preview")
            link = base / "linked"
            link.symlink_to(base / "real", target_is_directory=True)
            with self.assertRaises(MigrationSafetyError):
                migration_preview(repository, link / "preview")

    def test_source_catalog_and_manifest_symlinks_are_rejected(self) -> None:
        from hwskill.directory.migration import MigrationSafetyError, migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            for target in ("catalog", "manifest"):
                repository = base / target
                repository.mkdir()
                _legacy_repository(repository)
                if target == "catalog":
                    source = repository / "registry/catalog-real.json"
                    (repository / "registry/catalog.json").rename(source)
                    (repository / "registry/catalog.json").symlink_to(source.name)
                else:
                    source = repository / "sources/vendor-real.yaml"
                    (repository / "sources/vendor.yaml").rename(source)
                    (repository / "sources/vendor.yaml").symlink_to(source.name)
                with self.subTest(target=target), self.assertRaises(MigrationSafetyError):
                    migration_preview(repository, base / f"preview-{target}")

            repository = base / "source-directory"
            repository.mkdir()
            _legacy_repository(repository)
            real_sources = repository / "real-sources"
            (repository / "sources").rename(real_sources)
            (repository / "sources").symlink_to(real_sources.name, target_is_directory=True)
            with self.assertRaises(MigrationSafetyError):
                migration_preview(repository, base / "preview-source-directory")

    def test_atomic_failure_leaves_only_an_empty_high_entropy_staging_orphan(self) -> None:
        import hwskill.directory.migration as migration

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            output = base / "preview"
            with mock.patch.object(migration, "_write_atomic_file", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    migration.migration_preview(repository, output)
            self.assertFalse(output.exists())
            orphans = list(base.glob(".preview.staging-*"))
            self.assertEqual(len(orphans), 1)
            self.assertEqual(list(orphans[0].iterdir()), [])

    def test_cleanup_never_removes_a_later_directory_reusing_the_stage_name(self) -> None:
        import hwskill.directory.migration as migration

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            replacement: Path | None = None

            def replace_stage(*_args, **_kwargs):
                nonlocal replacement
                stage = next(base.glob(".preview.staging-*"))
                stage.rename(base / "owned-orphan")
                stage.mkdir()
                (stage / "later-owner.txt").write_text("keep", encoding="utf-8")
                replacement = stage
                raise OSError("injected replacement")

            with mock.patch.object(migration, "_write_atomic_file", side_effect=replace_stage):
                with self.assertRaises(OSError):
                    migration.migration_preview(repository, base / "preview")

            self.assertIsNotNone(replacement)
            self.assertEqual((replacement / "later-owner.txt").read_text(), "keep")
            self.assertEqual(list((base / "owned-orphan").iterdir()), [])

    def test_post_rename_parent_swap_returns_namespace_uncertain_not_success(self) -> None:
        import hwskill.directory.migration as migration

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            configured_parent = base / "configured"
            configured_parent.mkdir()
            original_rename = migration._rename_noreplace

            def rename_then_swap(parent_fd, source, destination):
                original_rename(parent_fd, source, destination)
                configured_parent.rename(base / "moved-parent")
                configured_parent.mkdir()

            with mock.patch.object(migration, "_rename_noreplace", side_effect=rename_then_swap):
                with self.assertRaisesRegex(migration.MigrationSafetyError, "namespace-uncertain"):
                    migration.migration_preview(repository, configured_parent / "preview")
            self.assertFalse((configured_parent / "preview").exists())
            self.assertTrue((base / "moved-parent/preview").is_dir())

    def test_catalog_and_source_fifos_are_rejected_without_blocking(self) -> None:
        project_root = Path(__file__).parents[2]
        script = project_root / "scripts/directory/migration-preview"
        environment = dict(os.environ)
        environment.update(PATH="/usr/bin:/bin", PYTHONPATH=str(project_root / "src"))
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            for target in ("catalog", "source"):
                repository = base / target
                repository.mkdir()
                _legacy_repository(repository)
                path = repository / ("registry/catalog.json" if target == "catalog" else "sources/vendor.yaml")
                path.unlink()
                os.mkfifo(path)
                completed = subprocess.run(
                    [str(script), str(repository), str(base / f"preview-{target}")],
                    cwd=project_root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(completed.returncode, 1, target)

    def test_actual_legacy_tag_checkout_is_read_without_modification(self) -> None:
        from hwskill.directory.migration import migration_preview

        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            checkout = base / "legacy"
            subprocess.run(["git", "clone", "-q", "--shared", ".", str(checkout)], check=True)
            subprocess.run(
                ["git", "-C", str(checkout), "checkout", "-q", "--detach", "hwskill-legacy-v0.1.0"],
                check=True,
            )
            before = subprocess.run(
                ["git", "-C", str(checkout), "status", "--porcelain=v1"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

            preview = migration_preview(checkout, base / "preview")

            after = subprocess.run(
                ["git", "-C", str(checkout), "status", "--porcelain=v1"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(after, before)
            self.assertIn("local/chinese-thinking", preview.candidate_ids)
            self.assertIn("superpowers/brainstorming", preview.candidate_ids)

    def test_migration_preview_script_emits_a_structured_result(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            _legacy_repository(repository)
            output = base / "preview"
            project_root = Path(__file__).parents[2]
            script = project_root / "scripts/directory/migration-preview"
            environment = dict(os.environ)
            environment.update(PATH="/usr/bin:/bin", PYTHONPATH=str(project_root / "src"))

            completed = subprocess.run(
                [str(script), str(repository), str(output)],
                cwd=project_root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["result"], "ok")
            self.assertEqual(result["candidate_ids"], ["local/manual", "vendor/tool"])
            self.assertTrue(output.is_dir())
            self.assertTrue(os.access(script, os.X_OK))


if __name__ == "__main__":
    unittest.main()
