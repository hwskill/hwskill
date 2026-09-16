from __future__ import annotations

import ast
import configparser
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import tomllib
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
LEGACY_TAG = "hwskill-legacy-v0.1.0"
LEGACY_COMMIT = "45825a59489e96160ad73b7074717ddedd292ef5"
ENTRYPOINTS = {
    "hwskill-directory": "hwskill.directory.cli:main",
    "hwskill-publish": "hwskill.publishing.cli:main",
    "hwskill-sharing": "hwskill.sharing.cli:main",
    "hwskill-verify": "hwskill.verification.cli:main",
}


def _digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_fake_python(path: Path, log: Path) -> None:
    path.write_text(
        """#!/bin/sh
set -eu
if [ -n "${FAKE_PYTHON_LOG:-}" ]; then
  printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
fi
if [ "${1:-}" = -m ] && [ "${2:-}" = venv ]; then
  for last do :; done
  mkdir -p "$last/bin"
  cp "$0" "$last/bin/python"
  chmod +x "$last/bin/python"
  exit 0
fi
if [ "${1:-}" = -m ] && [ "${2:-}" = pip ]; then
  bindir=$(dirname -- "$0")
  for command in hwskill-directory hwskill-publish hwskill-sharing hwskill-verify; do
    printf '%s\\n' '#!/bin/sh' 'echo usage: $0' > "$bindir/$command"
    chmod +x "$bindir/$command"
  done
  exit 0
fi
if [ "${1:-}" = -c ]; then
  case " $* " in
    *" --help "*) printf '%s\\n' 'usage: hwskill command' ;;
  esac
  exit 0
fi
if [ "${1:-}" = -m ] && [ "${2:-}" = hwskill ]; then
  printf '%s\n' 'usage: hwskill'
  exit 0
fi
exit 64
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    log.write_text("", encoding="utf-8")


def _copy_installer(
    destination: Path,
    *,
    allow_local_repository: bool = False,
    git_binary: Path | None = None,
    archive_limit: int | None = None,
    pause_before_stage_open: bool = False,
) -> None:
    body = (ROOT / "install.sh").read_text(encoding="utf-8")
    if allow_local_repository:
        body = body.replace("ALLOW_LOCAL_TEST_REPOSITORY = False", "ALLOW_LOCAL_TEST_REPOSITORY = True")
    if git_binary is not None:
        body = body.replace('GIT = "/usr/bin/git"', f'GIT = {str(git_binary)!r}')
    if archive_limit is not None:
        body = body.replace("MAX_ARCHIVE_BYTES = 128 * 1024 * 1024", f"MAX_ARCHIVE_BYTES = {archive_limit}")
    if pause_before_stage_open:
        ready_marker = destination.with_name(".stage-lstat-ready")
        body = body.replace(
            "expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)\n        descriptor = os.open",
            f"expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)\n        Path({str(ready_marker)!r}).write_text('ready')\n        __import__('time').sleep(0.25)\n        descriptor = os.open",
        )
    destination.write_text(body, encoding="utf-8")
    destination.chmod(0o755)


def _make_current_repository(destination: Path, *, allow_local_repository: bool = False) -> None:
    destination.mkdir()
    for name in ("pyproject.toml", "build_backend.py", "MANIFEST.in", "README.md", "install.sh"):
        shutil.copy2(ROOT / name, destination / name)
    if allow_local_repository:
        _copy_installer(destination / "install.sh", allow_local_repository=True)
    shutil.copytree(ROOT / "src", destination / "src")
    subprocess.run(["/usr/bin/git", "init", str(destination)], check=True, capture_output=True)
    subprocess.run(["/usr/bin/git", "-C", str(destination), "add", "."], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(destination), "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "current fixture"],
        check=True,
        capture_output=True,
    )


class RuntimeSurfaceTests(unittest.TestCase):
    def test_project_metadata_exposes_only_new_entrypoints_and_dependencies(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["scripts"], ENTRYPOINTS)
        dependencies = project["project"]["dependencies"]
        self.assertEqual(dependencies, ["PyYAML>=6,<7", "jsonschema>=4.23,<5"])
        self.assertFalse(any(item.lower().startswith("mcp") for item in dependencies))

    def test_installed_wheel_contains_and_runs_only_new_console_entrypoints(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheels = root / "wheels"
            source = root / "source"
            source.mkdir()
            shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
            shutil.copy2(ROOT / "build_backend.py", source / "build_backend.py")
            shutil.copytree(ROOT / "src", source / "src")
            stale_package = source / "build/lib/hwskill"
            stale_package.mkdir(parents=True)
            (stale_package / "mcp_server.py").write_text("raise RuntimeError('legacy module leaked')\n", encoding="utf-8")
            (stale_package / "cli.py").write_text("raise RuntimeError('legacy module leaked')\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            wheels.mkdir()
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", ".", "--wheel-dir", str(wheels)],
                cwd=source,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            wheel = next(wheels.glob("hwskill-*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
            self.assertNotIn("hwskill/mcp_server.py", names)
            self.assertNotIn("hwskill/cli.py", names)
            venv = root / "venv"
            subprocess.run(["/usr/bin/python3", "-m", "venv", "--system-site-packages", str(venv)], check=True)
            subprocess.run(
                [str(venv / "bin/python"), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)],
                check=True,
                capture_output=True,
                text=True,
            )
            entry_points = next(venv.glob("lib*/python*/site-packages/hwskill-*.dist-info/entry_points.txt"))
            parser = configparser.ConfigParser()
            parser.read(entry_points)
            self.assertEqual(dict(parser["console_scripts"]), ENTRYPOINTS)
            for command in ENTRYPOINTS:
                completed = subprocess.run(
                    [str(venv / "bin" / command), "--help"],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, f"{command}: {completed.stderr}")
                self.assertIn("usage:", completed.stdout)
            old = subprocess.run(
                [str(venv / "bin/python"), "-m", "hwskill", "--help"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(old.returncode, 0)

    def test_sdist_contains_its_backend_and_builds_the_same_clean_wheel(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            for name in ("pyproject.toml", "build_backend.py", "MANIFEST.in", "README.md"):
                shutil.copy2(ROOT / name, source / name)
            shutil.copytree(ROOT / "src", source / "src")
            stale_package = source / "build/lib/hwskill"
            stale_package.mkdir(parents=True)
            (stale_package / "mcp_server.py").write_text("raise RuntimeError('legacy module leaked')\n", encoding="utf-8")
            sdists = root / "sdists"
            sdists.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import build_backend; build_backend.build_sdist(r'" + str(sdists) + "')",
                ],
                cwd=source,
                env={**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"},
                check=True,
                capture_output=True,
                text=True,
            )
            archive = next(sdists.glob("hwskill-*.tar.gz"))
            unpacked = root / "unpacked"
            shutil.unpack_archive(archive, unpacked)
            sdist_root = next(unpacked.iterdir())
            self.assertTrue((sdist_root / "build_backend.py").is_file())
            wheels = root / "wheels"
            wheels.mkdir()
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", ".", "--wheel-dir", str(wheels)],
                cwd=sdist_root,
                check=True,
                capture_output=True,
                text=True,
            )
            wheel = next(wheels.glob("hwskill-*.whl"))
            with zipfile.ZipFile(wheel) as wheel_archive:
                names = set(wheel_archive.namelist())
            self.assertNotIn("hwskill/mcp_server.py", names)
            venv = root / "venv"
            subprocess.run(["/usr/bin/python3", "-m", "venv", "--system-site-packages", str(venv)], check=True)
            subprocess.run(
                [str(venv / "bin/python"), "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--force-reinstall", str(wheel)],
                check=True,
                capture_output=True,
                text=True,
            )
            for command in ENTRYPOINTS:
                completed = subprocess.run(
                    [str(venv / "bin" / command), "--help"],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, f"{command}: {completed.stderr}")

    def test_directory_module_exposes_migration_preview_subcommand(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "hwskill.directory", "--help"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("migration-preview", completed.stdout)

    def test_verification_has_an_importable_package_cli(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "hwskill.verification.cli", "--help"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout)


class InstallerTests(unittest.TestCase):
    def test_local_installer_installs_project_and_new_commands_without_touching_legacy_config(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            checkout = base / "checkout"
            checkout.mkdir()
            shutil.copy2(ROOT / "install.sh", checkout / "install.sh")
            shutil.copy2(ROOT / "pyproject.toml", checkout / "pyproject.toml")
            (checkout / "src/hwskill").mkdir(parents=True)
            fake_python = base / "python3"
            log = base / "python.log"
            _write_fake_python(fake_python, log)
            home = base / "home"
            legacy = home / ".hwskills"
            legacy.mkdir(parents=True)
            (legacy / "profile.yaml").write_text("profiles: [legacy]\n", encoding="utf-8")
            (home / ".bashrc").write_text("# user config\n", encoding="utf-8")
            before = _digest_tree(home)
            bin_dir = base / "bin"
            completed = subprocess.run(
                [str(checkout / "install.sh")],
                cwd=checkout,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "PYTHON": str(fake_python),
                    "HWSKILL_BIN_DIR": str(bin_dir),
                    "FAKE_PYTHON_LOG": str(log),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(_digest_tree(home), before)
            invocation = log.read_text(encoding="utf-8")
            self.assertIn("-m pip install", invocation)
            self.assertIn(str(checkout), invocation)
            self.assertNotIn("mcp", invocation.lower())
            for command in ENTRYPOINTS:
                installed = bin_dir / command
                self.assertTrue(installed.is_symlink(), command)
                self.assertEqual(installed.resolve(), checkout / ".venv/bin" / command)
                help_result = subprocess.run([str(installed), "--help"], check=False, capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, command)

    def test_local_current_checkout_keeps_head_and_tracked_files_unchanged(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            checkout = base / "checkout"
            checkout.mkdir()
            shutil.copy2(ROOT / "install.sh", checkout / "install.sh")
            shutil.copy2(ROOT / "pyproject.toml", checkout / "pyproject.toml")
            (checkout / "src/hwskill").mkdir(parents=True)
            (checkout / "src/hwskill/__init__.py").write_text("", encoding="utf-8")
            subprocess.run(["/usr/bin/git", "init", str(checkout)], check=True, capture_output=True)
            subprocess.run(["/usr/bin/git", "-C", str(checkout), "add", "."], check=True)
            subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "fixture"],
                check=True,
                capture_output=True,
            )
            before_head = subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            before_diff = subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "diff", "--binary", "HEAD"],
                check=True,
                capture_output=True,
            ).stdout
            fake_python = base / "python3"
            python_log = base / "python.log"
            _write_fake_python(fake_python, python_log)
            completed = subprocess.run(
                [str(checkout / "install.sh")],
                cwd=checkout,
                env={
                    **os.environ,
                    "HOME": str(base / "home"),
                    "PYTHON": str(fake_python),
                    "HWSKILL_BIN_DIR": str(base / "bin"),
                    "FAKE_PYTHON_LOG": str(python_log),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            after_head = subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            after_diff = subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "diff", "--binary", "HEAD"],
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(after_head, before_head)
            self.assertEqual(after_diff, before_diff)

    def test_legacy_fallback_ignores_untrusted_worktree_hooks_and_path_git(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap, allow_local_repository=True)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            path_git_marker = base / "path-git-ran"
            fake_git = fake_bin / "git"
            fake_git.write_text(
                f"""#!/bin/sh
set -eu
printf '%s\\n' path-git > {path_git_marker}
exit 91
""",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)

            source = base / "untrusted-source"
            subprocess.run(
                ["/usr/bin/git", "clone", "--no-checkout", str(ROOT), str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(source), "checkout", "--detach", LEGACY_COMMIT],
                check=True,
                capture_output=True,
                text=True,
            )
            malicious_marker = base / "malicious-install-ran"
            malicious_installer = base / "malicious-install.sh"
            malicious_installer.write_text(
                f"#!/bin/sh\nprintf '%s\\n' malicious > {malicious_marker}\n",
                encoding="utf-8",
            )
            malicious_installer.chmod(0o755)
            (source / "install.sh").unlink()
            (source / "install.sh").symlink_to(malicious_installer)
            subprocess.run(
                ["/usr/bin/git", "-C", str(source), "update-index", "--assume-unchanged", "install.sh"],
                check=True,
            )
            hook_marker = base / "post-checkout-ran"
            hook = source / ".git/hooks/post-checkout"
            hook.write_text(f"#!/bin/sh\nprintf '%s\\n' hook > {hook_marker}\n", encoding="utf-8")
            hook.chmod(0o755)

            home = base / "home"
            legacy_config = home / ".hwskills/profile.yaml"
            legacy_config.parent.mkdir(parents=True)
            legacy_config.write_text("profiles: [legacy]\n", encoding="utf-8")
            before = _digest_tree(home)
            fake_python = base / "python3"
            python_log = base / "python.log"
            _write_fake_python(fake_python, python_log)
            installed_bin = base / "installed-bin"
            target = base / "legacy-install"
            completed = subprocess.run(
                [str(bootstrap), "--legacy", f"--install-path={target}"],
                env={
                    **os.environ,
                    "HOME": str(home),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "PYTHON": str(fake_python),
                    "HWSKILL_BIN_DIR": str(installed_bin),
                    "HWSKILL_SHELL_CONFIG": str(home / ".profile"),
                    "HWSKILL_REPOSITORY_URL": str(source),
                    "FAKE_PYTHON_LOG": str(python_log),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(_digest_tree(home), before)
            self.assertFalse(path_git_marker.exists(), "installer trusted PATH instead of an absolute Git binary")
            self.assertFalse(hook_marker.exists(), "legacy fallback ran checkout hooks")
            self.assertFalse(malicious_marker.exists(), "legacy fallback executed the source worktree's install.sh")
            self.assertTrue((source / "install.sh").is_symlink(), "untrusted source worktree was rewritten")
            self.assertFalse(installed_bin.exists(), "legacy install wrote the caller's requested bin directory")
            installed_target = target / "scripts/hwskill"
            self.assertTrue(installed_target.is_file())
            self.assertTrue(os.access(installed_target, os.X_OK))
            self.assertEqual(installed_target.read_bytes(), subprocess.run(
                ["/usr/bin/git", "-C", str(source), "show", f"{LEGACY_COMMIT}:scripts/hwskill"],
                check=True,
                capture_output=True,
            ).stdout)
            help_result = subprocess.run(
                [str(installed_target), "--help"],
                env={**os.environ, "FAKE_PYTHON_LOG": str(python_log)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("usage:", help_result.stdout)
            final_head = subprocess.run(
                ["/usr/bin/git", "-C", str(target), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(final_head, LEGACY_COMMIT)
            symbolic = subprocess.run(
                ["/usr/bin/git", "-C", str(target), "symbolic-ref", "-q", "HEAD"],
                check=False,
                capture_output=True,
            )
            self.assertEqual(symbolic.returncode, 1)
            status = subprocess.run(
                ["/usr/bin/git", "-C", str(target), "status", "--porcelain=v1", "--untracked-files=all"],
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(status, b"")
            self.assertNotIn("credential", (target / ".git/config").read_text(encoding="utf-8").lower())

    def test_remote_installers_refuse_every_existing_target_without_executing_it(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap)
            malicious_marker = base / "existing-target-ran"
            malicious = base / "malicious.sh"
            malicious.write_text(f"#!/bin/sh\nprintf '%s\\n' ran > {malicious_marker}\n", encoding="utf-8")
            malicious.chmod(0o755)
            for legacy in (False, True):
                with self.subTest(legacy=legacy):
                    target = base / ("legacy-existing" if legacy else "current-existing")
                    (target / ".git").mkdir(parents=True)
                    (target / "install.sh").symlink_to(malicious)
                    before = _digest_tree(target)
                    arguments = [str(bootstrap), f"--install-path={target}"]
                    if legacy:
                        arguments.insert(1, "--legacy")
                    completed = subprocess.run(
                        arguments,
                        env={**os.environ, "HOME": str(base / "home")},
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(_digest_tree(target), before)
                    self.assertFalse(malicious_marker.exists())

    def test_remote_verification_failure_leaves_no_final_or_staging_install(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap, allow_local_repository=True)
            empty_repository = base / "empty.git"
            subprocess.run(["/usr/bin/git", "init", "--bare", str(empty_repository)], check=True, capture_output=True)
            for legacy in (False, True):
                with self.subTest(legacy=legacy):
                    target = base / ("legacy-final" if legacy else "current-final")
                    arguments = [str(bootstrap), f"--install-path={target}"]
                    if legacy:
                        arguments.insert(1, "--legacy")
                    completed = subprocess.run(
                        arguments,
                        env={
                            **os.environ,
                            "HOME": str(base / "home"),
                            "HWSKILL_REPOSITORY_URL": str(empty_repository),
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse(target.exists())
                    for orphan in base.glob(f".{target.name}.hwskill-stage-*"):
                        self.assertEqual(list(orphan.iterdir()), [], f"partial staging content survived: {orphan}")

    def test_remote_install_rejects_symlink_ancestor_without_outside_side_effects(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap)
            requested = base / "requested"
            requested.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (requested / "link").symlink_to(outside, target_is_directory=True)
            empty_repository = base / "empty.git"
            subprocess.run(["/usr/bin/git", "init", "--bare", str(empty_repository)], check=True, capture_output=True)
            for legacy in (False, True):
                with self.subTest(legacy=legacy):
                    target = requested / "link/nested/final"
                    arguments = [str(bootstrap), f"--install-path={target}"]
                    if legacy:
                        arguments.insert(1, "--legacy")
                    completed = subprocess.run(
                        arguments,
                        env={
                            **os.environ,
                            "HOME": str(base / "home"),
                            "HWSKILL_REPOSITORY_URL": str(empty_repository),
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse((outside / "nested").exists())

    def test_remote_install_rejects_unsafe_repository_urls_before_running_git(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            git_marker = base / "git-ran"
            fake_git = base / "git"
            fake_git.write_text(f"#!/bin/sh\nprintf '%s\\n' ran > {git_marker}\nexit 90\n", encoding="utf-8")
            fake_git.chmod(0o755)
            unsafe_urls = (
                "https://user:secret@example.test/hwskills.git",
                "https://example.test/hwskills.git?token=secret",
                "https://example.test/hwskills.git#main",
                "ssh://git@example.test/hwskills.git",
                "https://example.test/bad path.git",
            )
            for index, repository_url in enumerate(unsafe_urls):
                with self.subTest(repository_url=repository_url):
                    git_marker.unlink(missing_ok=True)
                    bootstrap = base / f"install-{index}.sh"
                    _copy_installer(bootstrap, git_binary=fake_git)
                    target = base / f"target-{index}"
                    completed = subprocess.run(
                        [str(bootstrap), f"--install-path={target}"],
                        env={
                            **os.environ,
                            "HOME": str(base / "home"),
                            "HWSKILL_REPOSITORY_URL": repository_url,
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("repository URL", completed.stderr)
                    self.assertFalse(git_marker.exists())
                    self.assertFalse(target.exists())

    def test_git_archive_is_rejected_at_the_streaming_size_limit(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            fake_git = base / "git"
            completed_stream = base / "archive-stream-finished"
            fake_git.write_text(
                f"""#!/bin/sh
set -eu
case " $* " in
  *" clone "*)
    for target do :; done
    mkdir -p "$target/.git"
    : > "$target/.git/config"
    ;;
  *" rev-parse "*)
    printf '%040d\n' 0
    ;;
  *" archive "*)
    head -c 10485760 /dev/zero
    printf '%s\\n' finished > {completed_stream}
    ;;
  *) exit 89 ;;
esac
""",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            bootstrap = base / "install.sh"
            _copy_installer(
                bootstrap,
                allow_local_repository=True,
                git_binary=fake_git,
                archive_limit=1024,
            )
            target = base / "target"
            completed = subprocess.run(
                [str(bootstrap), f"--install-path={target}"],
                env={
                    **os.environ,
                    "HOME": str(base / "home"),
                    "HWSKILL_REPOSITORY_URL": str(base / "source"),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("size limit", completed.stderr)
            self.assertFalse(target.exists())
            self.assertFalse(completed_stream.exists(), "installer consumed the entire oversized archive before rejecting it")

    def test_remote_current_install_publishes_relocatable_commands_after_fds_close(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            _make_current_repository(source, allow_local_repository=True)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap, allow_local_repository=True)
            fake_python = base / "python"
            fake_python_log = base / "python.log"
            _write_fake_python(fake_python, fake_python_log)
            target = base / "target"
            completed = subprocess.run(
                [str(bootstrap), f"--install-path={target}"],
                env={
                    **os.environ,
                    "HOME": str(base / "home"),
                    "HWSKILL_REPOSITORY_URL": str(source),
                    "PYTHON": str(fake_python),
                    "FAKE_PYTHON_LOG": str(fake_python_log),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            config = (target / ".git/config").read_text(encoding="utf-8").lower()
            self.assertNotIn("credential", config)
            for command in ENTRYPOINTS:
                installed = target / "bin" / command
                self.assertTrue(installed.is_file(), command)
                self.assertTrue(os.access(installed, os.X_OK), command)
                help_result = subprocess.run(
                    [str(installed), "--help"],
                    cwd=base,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(help_result.returncode, 0, f"{command}: {help_result.stderr}")
                self.assertIn("usage:", help_result.stdout)

    def test_stage_name_swap_cannot_publish_the_replacement_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            _make_current_repository(source, allow_local_repository=True)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap, allow_local_repository=True)
            target = base / "target"
            replacement_marker = base / "replacement-marker"
            swapped: list[Path] = []

            def swap_stage() -> None:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    candidates = list(base.glob(".target.hwskill-stage-*"))
                    if candidates:
                        candidate = candidates[0]
                        if not (candidate / ".git/config").is_file():
                            time.sleep(0.005)
                            continue
                        saved = base / "owned-stage-saved"
                        try:
                            candidate.rename(saved)
                            candidate.mkdir()
                            (candidate / "later-owner").write_bytes(b"do-not-delete-or-publish")
                        except FileNotFoundError:
                            continue
                        replacement_marker.write_text(str(candidate), encoding="utf-8")
                        swapped.append(candidate)
                        return
                    time.sleep(0.005)

            watcher = threading.Thread(target=swap_stage)
            watcher.start()
            completed = subprocess.run(
                [str(bootstrap), f"--install-path={target}"],
                env={
                    **os.environ,
                    "HOME": str(base / "home"),
                    "HWSKILL_REPOSITORY_URL": str(source),
                    "PIP_NO_INDEX": "1",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            watcher.join(timeout=10)
            self.assertTrue(swapped, "race harness did not replace the staging name")
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(target.exists())
            self.assertEqual((swapped[0] / "later-owner").read_bytes(), b"do-not-delete-or-publish")

    def test_stage_lstat_open_swap_never_cleans_the_later_inode(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            bootstrap = base / "install.sh"
            _copy_installer(bootstrap, allow_local_repository=True, pause_before_stage_open=True)
            target = base / "target"
            empty_repository = base / "empty.git"
            subprocess.run(["/usr/bin/git", "init", "--bare", str(empty_repository)], check=True, capture_output=True)
            swapped: list[Path] = []

            def swap_before_open() -> None:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if not (base / ".stage-lstat-ready").exists():
                        time.sleep(0.005)
                        continue
                    candidates = list(base.glob(".target.hwskill-stage-*"))
                    if candidates:
                        candidate = candidates[0]
                        try:
                            candidate.rename(base / "original-stage")
                            candidate.mkdir()
                            (candidate / "later-owner").write_bytes(b"preserve")
                        except FileNotFoundError:
                            continue
                        swapped.append(candidate)
                        return
                    time.sleep(0.005)

            watcher = threading.Thread(target=swap_before_open)
            watcher.start()
            completed = subprocess.run(
                [str(bootstrap), f"--install-path={target}"],
                env={
                    **os.environ,
                    "HOME": str(base / "home"),
                    "HWSKILL_REPOSITORY_URL": str(empty_repository),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            watcher.join(timeout=10)
            self.assertTrue(swapped, "race harness did not replace the staging name")
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(target.exists())
            self.assertEqual((swapped[0] / "later-owner").read_bytes(), b"preserve")


class CleanupBoundaryTests(unittest.TestCase):
    def test_only_entry_backed_hosted_skills_remain_in_production_source_tree(self) -> None:
        expected = {
            Path("skills-src/l2/local/gitcode-discussion-fetch"),
            Path("skills-src/l2/local/gitcode-pr-review-fetch"),
        }
        actual = {
            path.parent.relative_to(ROOT)
            for path in (ROOT / "skills-src").rglob("SKILL.md")
        }
        self.assertEqual(actual, expected)
        fixture = ROOT / "tests/agent-experience/fixtures/skills-src/l1/local/chinese-thinking/SKILL.md"
        self.assertTrue(fixture.is_file())
        self.assertFalse((ROOT / "skills-src/l1/local/chinese-thinking").exists())

    def test_task8_preparation_restores_chinese_thinking_only_inside_isolated_project(self) -> None:
        observer = ROOT / "tests/agent-experience/observer.py"
        task = ROOT / "tests/agent-experience/tasks/skill-contribution.json"
        with TemporaryDirectory() as temporary:
            destination = Path(temporary) / "project"
            completed = subprocess.run(
                [sys.executable, str(observer), "prepare-task", "--repo-root", str(ROOT), "--task", str(task), "--destination", str(destination)],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((destination / "skills-src/l1/local/chinese-thinking/SKILL.md").is_file())

    def test_legacy_runtime_data_tests_and_demos_are_absent(self) -> None:
        obsolete = [
            "src/hwskill/__main__.py", "src/hwskill/audit.py", "src/hwskill/catalog.py",
            "src/hwskill/claude_code_adapter.py", "src/hwskill/cli.py", "src/hwskill/codex_adapter.py",
            "src/hwskill/configuration.py", "src/hwskill/console_output.py", "src/hwskill/core_timeout.py",
            "src/hwskill/digest.py", "src/hwskill/docker_test_runner.py", "src/hwskill/doctor.py",
            "src/hwskill/eval_events.py", "src/hwskill/eval_observer.py", "src/hwskill/frontmatter.py",
            "src/hwskill/git_source.py", "src/hwskill/hosts.py", "src/hwskill/info.py",
            "src/hwskill/integrity.py", "src/hwskill/integrity_cli.py", "src/hwskill/json_configuration.py",
            "src/hwskill/loader.py", "src/hwskill/maintenance_cli.py", "src/hwskill/maintenance_transaction.py",
            "src/hwskill/mcp_server.py", "src/hwskill/models.py", "src/hwskill/network_guard.py",
            "src/hwskill/opencode_adapter.py", "src/hwskill/paths.py", "src/hwskill/pending_verification.py",
            "src/hwskill/profiles.py", "src/hwskill/projects.py", "src/hwskill/registry.py",
            "src/hwskill/scopes.py", "src/hwskill/search.py", "src/hwskill/skill_export.py",
            "src/hwskill/skill_maintenance.py", "src/hwskill/source_maintenance.py", "src/hwskill/source_manifest.py",
            "src/hwskill/test_agent.py", "src/hwskill/test_artifacts.py", "src/hwskill/test_cli.py",
            "src/hwskill/test_configuration.py", "src/hwskill/test_impact.py", "src/hwskill/test_manifest.py",
            "src/hwskill/test_runner.py", "src/hwskill/test_setup.py", "src/hwskill/test_worker.py",
            "profiles", "registry", "sources", "skills-src/l1/superpowers", "tests/core", "tests/profiles",
            "tests/skills", "examples", "docker", ".dockerignore", "scripts/hwskill",
            "scripts/filter_minimax_auth.sh", "scripts/run_codex_live_eval.sh", "scripts/run_docker_smoke.sh",
            "scripts/run_gitcode_pr_agent_eval.sh", "scripts/run_claude_code_gitcode_pr_agent_eval.sh",
            "scripts/run_opencode_gitcode_pr_agent_eval.sh",
        ]
        present = [relative for relative in obsolete if (ROOT / relative).exists() or (ROOT / relative).is_symlink()]
        self.assertEqual(present, [])

    def test_retained_python_has_no_legacy_runtime_imports(self) -> None:
        allowed_roots = {"directory", "publishing", "sharing", "verification"}
        offenders: list[str] = []
        for path in sorted((ROOT / "src/hwskill").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.startswith("hwskill.") and name.split(".", 2)[1] not in allowed_roots:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(offenders, [])

    def test_active_entry_docs_and_scripts_have_no_obsolete_command_references(self) -> None:
        roots = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "docs/guides", ROOT / "scripts"]
        needles = (
            "hwskill setup", "hwskill profile", "hwskill dump", "hwskill serve-mcp",
            "hwskill test", "hwskill registry", "scripts/hwskill", "tests/core",
            "tests/profiles", "tests/skills", "docker/entrypoint.sh",
        )
        offenders: list[str] = []
        for root in roots:
            paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
            for path in paths:
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for needle in needles:
                    if needle in text:
                        offenders.append(f"{path.relative_to(ROOT)}:{needle}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
