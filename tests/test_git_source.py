from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


class GitSourceClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.work = self.root / "work"
        self._git("init", "--bare", "--initial-branch=main", str(self.remote))
        self._git("init", "--initial-branch=main", str(self.work))
        self._git("config", "user.name", "Test User", cwd=self.work)
        self._git("config", "user.email", "test@example.invalid", cwd=self.work)

        self._write_skill("zeta", "The Zeta Skill")
        self._write_skill("alpha", "The Alpha Skill")
        self._git("add", ".", cwd=self.work)
        self._git("commit", "-m", "initial skills", cwd=self.work)
        self.tag_commit = self._git_output("rev-parse", "HEAD", cwd=self.work)
        self._git("tag", "-a", "v1", "-m", "version one", cwd=self.work)
        self._git("remote", "add", "origin", str(self.remote), cwd=self.work)
        self._git("push", "origin", "main", "--tags", cwd=self.work)
        self.branch_commit = self._git_output("rev-parse", "HEAD", cwd=self.work)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, *args: str, cwd: Path | None = None) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        )

    def _git_output(self, *args: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def _write_skill(self, path: str, description: str) -> None:
        skill = self.work / "skills" / path
        skill.mkdir(parents=True)
        skill.joinpath("SKILL.md").write_text(
            f"---\nname: {path}\ndescription: {description}\n---\n\nBody.\n",
            encoding="utf-8",
        )

    def test_list_remote_reports_default_branch_and_peeled_annotated_tag(self) -> None:
        from hwskill.git_source import GitSourceClient

        refs = GitSourceClient().list_remote(str(self.remote))

        self.assertEqual(refs.default_branch, "refs/heads/main")
        self.assertEqual(refs.refs["refs/heads/main"], self.branch_commit)
        self.assertEqual(refs.refs["refs/tags/v1"], self.tag_commit)
        with self.assertRaises(TypeError):
            refs.refs["refs/heads/other"] = "a" * 40  # type: ignore[index]

    def test_resolve_branch_returns_full_commit_without_checkout(self) -> None:
        from hwskill.git_source import GitSourceClient

        resolved = GitSourceClient().resolve(str(self.remote), "refs/heads/main")

        self.assertEqual(resolved.track, "refs/heads/main")
        self.assertEqual(resolved.kind, "branch")
        self.assertEqual(resolved.commit, self.branch_commit)
        self.assertRegex(resolved.commit, r"^[0-9a-f]{40}$")

    def test_resolve_annotated_tag_returns_peeled_commit(self) -> None:
        from hwskill.git_source import GitSourceClient

        resolved = GitSourceClient().resolve(str(self.remote), "refs/tags/v1")

        self.assertEqual(resolved.kind, "tag")
        self.assertEqual(resolved.commit, self.tag_commit)

    def test_resolve_full_commit_sha_verifies_the_exact_commit(self) -> None:
        from hwskill.git_source import GitSourceClient

        resolved = GitSourceClient().resolve(str(self.remote), self.branch_commit)

        self.assertEqual(resolved.track, self.branch_commit)
        self.assertEqual(resolved.kind, "commit")
        self.assertEqual(resolved.commit, self.branch_commit)

    def test_resolve_missing_ref_and_bare_ref_fail_clearly(self) -> None:
        from hwskill.git_source import GitSourceClient, GitSourceError

        client = GitSourceClient()
        with self.assertRaisesRegex(GitSourceError, "not found"):
            client.resolve(str(self.remote), "refs/heads/missing")
        with self.assertRaisesRegex(GitSourceError, "fully qualified"):
            client.resolve(str(self.remote), "main")

    def test_fixed_tag_move_is_reported(self) -> None:
        from hwskill.git_source import GitSourceError, verify_existing_tag

        with self.assertRaisesRegex(GitSourceError, "tag moved"):
            verify_existing_tag("refs/tags/v1", "a" * 40, "b" * 40)

    def test_materialize_checks_out_detached_resolved_commit(self) -> None:
        from hwskill.git_source import GitSourceClient

        destination = self.root / "checkout"
        destination.mkdir()
        resolved = GitSourceClient().materialize(
            str(self.remote), "refs/heads/main", destination
        )

        self.assertEqual(resolved.commit, self.branch_commit)
        self.assertEqual(
            self._git_output("rev-parse", "HEAD^{commit}", cwd=destination),
            self.branch_commit,
        )
        self.assertNotEqual(
            subprocess.run(
                ["git", "symbolic-ref", "-q", "HEAD"],
                cwd=destination,
                text=True,
                capture_output=True,
            ).returncode,
            0,
        )

    def test_materialize_rejects_a_destination_that_is_not_empty(self) -> None:
        from hwskill.git_source import GitSourceClient, GitSourceError

        destination = self.root / "occupied"
        destination.mkdir()
        destination.joinpath("keep").write_text("do not overwrite", encoding="utf-8")

        with self.assertRaisesRegex(GitSourceError, "empty directory"):
            GitSourceClient().materialize(str(self.remote), "refs/heads/main", destination)
        self.assertEqual(destination.joinpath("keep").read_text(encoding="utf-8"), "do not overwrite")


class DiscoverSkillsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.checkout = Path(self.temp.name) / "checkout"
        self.checkout.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_skill(self, path: str, *, description: str = "A useful Skill") -> None:
        skill = self.checkout / "skills" / path
        skill.mkdir(parents=True)
        skill.joinpath("SKILL.md").write_text(
            f"---\nname: {path}\ndescription: {description}\n---\n\nBody.\n",
            encoding="utf-8",
        )

    def test_discovery_returns_sorted_direct_children_with_frontmatter(self) -> None:
        from hwskill.git_source import discover_skills

        self._write_skill("zeta", description="Zeta description")
        self._write_skill("alpha", description="Alpha description")
        self._write_skill("group/nested", description="Must not be discovered")

        discovered = discover_skills(self.checkout, "skills")

        self.assertEqual([skill.path for skill in discovered], ["alpha", "zeta"])
        self.assertEqual(discovered[0].name, "alpha")
        self.assertEqual(discovered[0].description, "Alpha description")

    def test_discovery_rejects_invalid_frontmatter(self) -> None:
        from hwskill.git_source import GitSourceError, discover_skills

        self._write_skill("broken")
        skill_file = self.checkout / "skills" / "broken" / "SKILL.md"
        skill_file.write_text("---\nname: broken\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(GitSourceError, "frontmatter"):
            discover_skills(self.checkout, "skills")

    def test_discovery_rejects_unsafe_skills_path(self) -> None:
        from hwskill.git_source import GitSourceError, discover_skills

        with self.assertRaisesRegex(GitSourceError, "safe relative"):
            discover_skills(self.checkout, "../skills")
        with self.assertRaisesRegex(GitSourceError, "safe relative"):
            discover_skills(self.checkout, "/skills")

    def test_discovery_rejects_symlinked_direct_skill_directory(self) -> None:
        from hwskill.git_source import GitSourceError, discover_skills

        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        outside.joinpath("SKILL.md").write_text(
            "---\nname: escaped\ndescription: outside checkout\n---\n",
            encoding="utf-8",
        )
        skills = self.checkout / "skills"
        skills.mkdir()
        skills.joinpath("escaped").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(GitSourceError, "symlink"):
            discover_skills(self.checkout, "skills")


if __name__ == "__main__":
    unittest.main()
