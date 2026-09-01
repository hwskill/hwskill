from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from hwskill.source_manifest import (
    SourceManifestError,
    load_all_sources,
    load_source_manifest,
    write_source_manifest,
)


class SourceManifestTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source_data(self) -> dict:
        return {
            "schema_version": 2,
            "source_id": "superpowers",
            "kind": "upstream",
            "upstream": {
                "repository": "https://github.com/obra/superpowers.git",
                "track": "refs/heads/main",
                "skills_path": "skills",
                "ignore": [{"path": "unused", "reason": "not selected"}],
            },
            "defaults": {"namespace": "superpowers", "layer": "l1", "license": "MIT"},
            "resolved": {
                "revision": "a" * 40,
                "skills": [{
                    "path": "brainstorming",
                    "id": "superpowers/brainstorming",
                    "layer": "l1",
                    "content_digest": "sha256:" + "b" * 64,
                }],
            },
        }

    def write_source(self, data: dict, name: str = "superpowers.yaml") -> Path:
        path = self.root / "sources" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def test_source_manifest_separates_track_from_resolved_state(self):
        source = load_source_manifest(self.write_source(self.source_data()))

        self.assertEqual(source.upstream.track, "refs/heads/main")
        self.assertEqual(source.resolved_revision, "a" * 40)
        self.assertEqual(source.skills[0].skill_id, "superpowers/brainstorming")

    def test_load_all_sources_rejects_duplicate_source_ids(self):
        self.write_source(self.source_data(), "one.yaml")
        self.write_source(self.source_data(), "two.yaml")

        with self.assertRaisesRegex(SourceManifestError, "duplicate source_id"):
            load_all_sources(self.root)

    def test_rejects_local_repository_paths_and_non_remote_urls(self):
        for repository in ("/srv/superpowers", "file:///srv/superpowers", ".", "../superpowers"):
            with self.subTest(repository=repository):
                data = self.source_data()
                data["upstream"]["repository"] = repository
                with self.assertRaisesRegex(SourceManifestError, "repository"):
                    load_source_manifest(self.write_source(data))

    def test_accepts_supported_remote_git_url_forms(self):
        for repository in (
            "https://github.com/obra/superpowers.git",
            "git@github.com:obra/superpowers.git",
            "ssh://git@github.com/obra/superpowers.git",
            "git://github.com/obra/superpowers.git",
        ):
            with self.subTest(repository=repository):
                data = self.source_data()
                data["upstream"]["repository"] = repository
                self.assertEqual(load_source_manifest(self.write_source(data)).upstream.repository, repository)

    def test_rejects_bare_refs_and_non_full_resolved_revisions(self):
        data = self.source_data()
        data["upstream"]["track"] = "main"
        with self.assertRaisesRegex(SourceManifestError, "track"):
            load_source_manifest(self.write_source(data))

        data = self.source_data()
        data["resolved"]["revision"] = "a" * 12
        with self.assertRaisesRegex(SourceManifestError, "revision"):
            load_source_manifest(self.write_source(data))

    def test_rejects_unsafe_skill_and_ignore_paths(self):
        for key, value in (("skills_path", "../skills"), ("skills_path", "/skills"), ("ignore", [{"path": "../unused", "reason": "not selected"}])):
            with self.subTest(key=key, value=value):
                data = self.source_data()
                data["upstream"][key] = value
                with self.assertRaisesRegex(SourceManifestError, "path"):
                    load_source_manifest(self.write_source(data))

    def test_rejects_resolved_and_ignored_path_overlap(self):
        data = self.source_data()
        data["upstream"]["ignore"] = [{"path": "brainstorming", "reason": "not selected"}]

        with self.assertRaisesRegex(SourceManifestError, "overlap"):
            load_source_manifest(self.write_source(data))

    def test_rejects_duplicate_resolved_skill_ids(self):
        data = self.source_data()
        duplicate = dict(data["resolved"]["skills"][0])
        duplicate["path"] = "writing-plans"
        data["resolved"]["skills"].append(duplicate)

        with self.assertRaisesRegex(SourceManifestError, "duplicate skill id"):
            load_source_manifest(self.write_source(data))

    def test_rejects_missing_collections_and_unknown_source_kind(self):
        data = self.source_data()
        del data["upstream"]["ignore"]
        with self.assertRaisesRegex(SourceManifestError, "ignore"):
            load_source_manifest(self.write_source(data))

        data = self.source_data()
        data["kind"] = "local"
        with self.assertRaisesRegex(SourceManifestError, "kind"):
            load_source_manifest(self.write_source(data))

    def test_write_round_trips_with_deterministic_yaml(self):
        source = load_source_manifest(self.write_source(self.source_data()))
        first = self.root / "first.yaml"
        second = self.root / "second.yaml"

        write_source_manifest(first, source)
        write_source_manifest(second, source)

        self.assertEqual(first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8"))
        self.assertEqual(load_source_manifest(first), source)


if __name__ == "__main__":
    unittest.main()
