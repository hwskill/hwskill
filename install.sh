#!/bin/sh
set -eu

legacy_commit=45825a59489e96160ad73b7074717ddedd292ef5
commands="hwskill-directory hwskill-publish hwskill-sharing hwskill-verify"

script_path=$0
case "$script_path" in
  */*) ;;
  *) script_path=$(command -v "$script_path" 2>/dev/null || printf '%s' "$script_path") ;;
esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$script_path")" 2>/dev/null && pwd -P || true)

legacy=false
install_path=
for argument in "$@"; do
  case "$argument" in
    --legacy) legacy=true ;;
    --install-path=*) install_path=${argument#--install-path=} ;;
    *) echo "unknown installer argument: $argument" >&2; exit 2 ;;
  esac
done

install_current_checkout() {
  repo_root=$1
  python_command=${PYTHON:-/usr/bin/python3}
  venv=$repo_root/.venv
  bin_dir=${HWSKILL_BIN_DIR:-"$HOME/.local/bin"}

  if [ ! -x "$venv/bin/python" ]; then
    "$python_command" -m venv "$venv"
  fi
  marker=$venv/.hwskill-installed
  rm -f "$marker"
  "$venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir "$repo_root"
  "$venv/bin/python" -c \
    "import hwskill.directory, hwskill.publishing, hwskill.sharing, hwskill.verification"

  for command in $commands; do
    target=$venv/bin/$command
    if [ ! -x "$target" ]; then
      echo "installed package did not provide expected command: $command" >&2
      exit 2
    fi
  done
  mkdir -p "$bin_dir"
  for command in $commands; do
    target=$venv/bin/$command
    destination=$bin_dir/$command
    if [ -L "$destination" ]; then
      if [ "$(readlink "$destination")" != "$target" ]; then
        echo "existing symlink is not owned by this checkout: $destination" >&2
        exit 2
      fi
    elif [ -e "$destination" ]; then
      echo "existing command is not owned by hwskill: $destination" >&2
      exit 2
    fi
  done
  for command in $commands; do
    target=$venv/bin/$command
    destination=$bin_dir/$command
    if [ ! -L "$destination" ]; then
      ln -s "$target" "$destination"
    fi
  done
  touch "$marker"
  echo "hwskill sharing tools installed from $repo_root"
  echo "commands: $commands"
  echo "add $bin_dir to PATH if it is not already available"
}

if [ "$legacy" = false ] \
  && [ -z "$install_path" ] \
  && [ -n "$script_dir" ] \
  && [ -f "$script_dir/pyproject.toml" ] \
  && [ -d "$script_dir/src/hwskill" ]; then
  install_current_checkout "$script_dir"
  exit 0
fi

if [ -z "$install_path" ]; then
  if [ "$legacy" = true ]; then
    install_path=${HWSKILL_LEGACY_HOME:-"$HOME/.local/share/hwskill-legacy-v0.1.0"}
  else
    install_path=${HWSKILL_INSTALL_HOME:-"$HOME/.local/share/hwskill"}
  fi
fi
repository_url=${HWSKILL_REPOSITORY_URL:-"https://gitcode.com/linkeo2012/hwskills.git"}
mode=current
if [ "$legacy" = true ]; then
  mode=legacy
fi

exec /usr/bin/python3 - "$mode" "$install_path" "$repository_url" "$script_path" "$legacy_commit" <<'PY'
from __future__ import annotations

import ctypes
import errno
import io
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit


MODE, TARGET_TEXT, REPOSITORY_URL, TRUSTED_INSTALLER_TEXT, LEGACY_COMMIT = sys.argv[1:]
GIT = "/usr/bin/git"
LEGACY_TAG = "hwskill-legacy-v0.1.0"
COMMANDS = (
    ("hwskill-directory", "hwskill.directory.cli"),
    ("hwskill-publish", "hwskill.publishing.cli"),
    ("hwskill-sharing", "hwskill.sharing.cli"),
    ("hwskill-verify", "hwskill.verification.cli"),
)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
ALLOW_LOCAL_TEST_REPOSITORY = False


def fail(message: str) -> None:
    raise RuntimeError(message)


def safe_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in {"HOME", "XDG_CONFIG_HOME"}
    }
    environment.update(
        {
            "HOME": "/nonexistent",
            "XDG_CONFIG_HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false",
            "SSH_ASKPASS": "/bin/false",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def git_command(*arguments: str) -> list[str]:
    return [
        GIT,
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        "-c", "credential.helper=",
        "-c", "filter.lfs.process=",
        "-c", "filter.lfs.smudge=",
        "-c", "filter.lfs.clean=",
        "-c", "filter.lfs.required=false",
        "-c", "protocol.ext.allow=never",
        "-c", "protocol.file.allow=always",
        *arguments,
    ]


def run_git(stage_fd: int, environment: dict[str, str], *arguments: str, capture: bool = False) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        git_command(*arguments),
        env=environment,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        pass_fds=(stage_fd,),
    )


def read_git_archive(stage_fd: int, environment: dict[str, str], repository: Path, commit: str) -> bytes:
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            git_command("-C", str(repository), "archive", "--format=tar", commit),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=errors,
            pass_fds=(stage_fd,),
        )
        assert process.stdout is not None
        chunks: list[bytes] = []
        size = 0
        try:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    process.kill()
                    process.stdout.close()
                    process.wait()
                    fail("Git archive exceeds the installer streaming size limit")
                chunks.append(chunk)
            return_code = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if return_code != 0:
            errors.seek(0)
            detail = errors.read(4096).decode("utf-8", "replace").strip()
            fail(f"Git archive failed: {detail or return_code}")
        return b"".join(chunks)


def validate_repository_url(value: str) -> None:
    if ALLOW_LOCAL_TEST_REPOSITORY and os.path.isabs(value):
        return
    if not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        fail("repository URL contains whitespace or control characters")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        fail(f"repository URL is invalid: {error}")
    if parsed.scheme != "https" or not parsed.hostname:
        fail("repository URL must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        fail("repository URL must not contain credentials")
    if parsed.query or parsed.fragment:
        fail("repository URL must not contain a query or fragment")


def verify_legacy_checkout(stage_fd: int, environment: dict[str, str], repository: Path) -> None:
    head = run_git(
        stage_fd,
        environment,
        "-C", str(repository), "rev-parse", "--verify", "HEAD^{commit}",
        capture=True,
    ).stdout.decode("ascii").strip()
    if head != LEGACY_COMMIT:
        fail(f"legacy checkout HEAD is not the fixed commit: {head}")
    symbolic = subprocess.run(
        git_command("-C", str(repository), "symbolic-ref", "-q", "HEAD"),
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(stage_fd,),
    )
    if symbolic.returncode != 1 or symbolic.stdout:
        fail("legacy checkout HEAD is not detached")
    status_output = run_git(
        stage_fd,
        environment,
        "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all",
        capture=True,
    ).stdout
    if status_output:
        fail("legacy checkout is not clean after fixed-commit materialization")


def open_directory_chain(path: Path, *, create: bool) -> list[int]:
    absolute = Path(os.path.abspath(path))
    descriptors = [os.open("/", DIRECTORY_FLAGS)]
    try:
        for part in absolute.parts[1:]:
            parent = descriptors[-1]
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=parent)
                os.fsync(parent)
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=parent)
            descriptors.append(child)
        return descriptors
    except BaseException:
        while descriptors:
            os.close(descriptors.pop())
        raise


def verify_directory_chain(path: Path, expected: list[int]) -> None:
    reopened = open_directory_chain(path, create=False)
    try:
        if len(reopened) != len(expected):
            fail(f"install parent namespace changed: {path}")
        for actual_fd, expected_fd in zip(reopened, expected):
            actual = os.fstat(actual_fd)
            wanted = os.fstat(expected_fd)
            if (actual.st_dev, actual.st_ino) != (wanted.st_dev, wanted.st_ino):
                fail(f"install parent namespace changed: {path}")
    finally:
        while reopened:
            os.close(reopened.pop())


def create_stage(parent_fd: int, target_name: str) -> tuple[str, int, os.stat_result]:
    for _ in range(32):
        name = f".{target_name}.hwskill-stage-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        os.fsync(parent_fd)
        expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        actual = os.fstat(descriptor)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            os.close(descriptor)
            fail("installer staging name changed before it was opened")
        return name, descriptor, actual
    fail("unable to allocate a unique installer staging directory")


def verify_stage_name(parent_fd: int, stage_name: str, expected: os.stat_result) -> None:
    actual = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        fail("installer staging name no longer identifies the owned directory")


def safe_parts(name: str) -> tuple[str, ...]:
    if "\\" in name or name.startswith("/") or name.endswith("/"):
        fail(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        fail(f"unsafe archive member path: {name!r}")
    return parts


def open_or_create_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        fail(f"archive member parent is not a directory: {name}")
    return descriptor


def directory_for(root_fd: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            child = open_or_create_directory(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def extract_archive(stage_fd: int, archive_bytes: bytes) -> None:
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        fail("Git archive exceeds the installer size limit")
    seen: set[tuple[str, ...]] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for member in archive:
            raw_name = member.name.rstrip("/") if member.isdir() else member.name
            parts = safe_parts(raw_name)
            if parts in seen:
                fail(f"duplicate archive member: {member.name}")
            seen.add(parts)
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                fail(f"forbidden archive member type: {member.name}")
            if member.isdir():
                descriptor = directory_for(stage_fd, parts)
                os.fsync(descriptor)
                os.close(descriptor)
                continue
            if not member.isfile():
                fail(f"unsupported archive member type: {member.name}")
            total += member.size
            if member.size < 0 or total > MAX_ARCHIVE_BYTES:
                fail("Git archive expands beyond the installer size limit")
            parent_fd = directory_for(stage_fd, parts[:-1])
            source = archive.extractfile(member)
            if source is None:
                os.close(parent_fd)
                fail(f"archive member has no body: {member.name}")
            file_fd = os.open(
                parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                member.mode & 0o777,
                dir_fd=parent_fd,
            )
            try:
                remaining = member.size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        fail(f"short archive member: {member.name}")
                    view = memoryview(chunk)
                    while view:
                        written = os.write(file_fd, view)
                        view = view[written:]
                    remaining -= len(chunk)
                if source.read(1):
                    fail(f"oversized archive member: {member.name}")
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
                source.close()
                os.fsync(parent_fd)
                os.close(parent_fd)
    os.fsync(stage_fd)


def read_regular_at(directory_fd: int, name: str) -> tuple[int, bytes]:
    descriptor = os.open(name, FILE_FLAGS, dir_fd=directory_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        fail(f"required installer file is not regular: {name}")
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor, b"".join(chunks)


def run_legacy_installer(stage: Path, stage_fd: int) -> None:
    installer_fd, _ = read_regular_at(stage_fd, "install.sh")
    try:
        os.set_inheritable(installer_fd, True)
        isolated_home = stage / ".legacy-home"
        isolated_bin = stage / ".legacy-bin"
        isolated_home.mkdir(mode=0o700)
        isolated_bin.mkdir(mode=0o700)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("HWSKILL_")
            and key not in {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"}
        }
        environment.update(
            {
                "HOME": str(isolated_home),
                "XDG_CONFIG_HOME": str(isolated_home / ".config"),
                "XDG_DATA_HOME": str(isolated_home / ".local/share"),
                "XDG_CACHE_HOME": str(isolated_home / ".cache"),
                "HWSKILL_BIN_DIR": str(isolated_bin),
                "HWSKILL_SHELL_CONFIG": str(isolated_home / ".profile"),
                "PIP_NO_CACHE_DIR": "1",
                "PATH": "/usr/bin:/bin",
            }
        )
        if "PYTHON" in os.environ:
            environment["PYTHON"] = os.environ["PYTHON"]
        subprocess.run(
            ["/bin/sh", "-c", f". /proc/self/fd/{installer_fd}", str(stage / "install.sh")],
            cwd=stage,
            env=environment,
            pass_fds=(installer_fd, stage_fd),
            check=True,
        )
    finally:
        os.close(installer_fd)
    scripts_fd = directory_for(stage_fd, ("scripts",))
    try:
        command_fd, _ = read_regular_at(scripts_fd, "hwskill")
        os.close(command_fd)
    finally:
        os.close(scripts_fd)
    shutil.rmtree(stage / ".legacy-home")
    shutil.rmtree(stage / ".legacy-bin")


def run_current_installer(stage: Path, stage_fd: int) -> None:
    python = os.environ.get("PYTHON", "/usr/bin/python3")
    if not os.path.isabs(python) or not os.access(python, os.X_OK):
        fail("PYTHON must name an executable absolute path")
    subprocess.run([python, "-m", "venv", ".venv"], cwd=stage, pass_fds=(stage_fd,), check=True)
    venv_python = ".venv/bin/python"
    subprocess.run(
        [venv_python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--no-build-isolation", "."],
        cwd=stage,
        check=True,
        pass_fds=(stage_fd,),
    )
    subprocess.run(
        [venv_python, "-c", "import hwskill.directory, hwskill.publishing, hwskill.sharing, hwskill.verification"],
        cwd=stage,
        check=True,
        pass_fds=(stage_fd,),
    )
    command_directory = stage / "bin"
    command_directory.mkdir(mode=0o755)
    for command, _module in COMMANDS:
        generated = stage / ".venv/bin" / command
        if not generated.is_file() or not os.access(generated, os.X_OK):
            fail(f"installed package did not provide expected command: {command}")
        wrapper = command_directory / command
        descriptor = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
        try:
            body = (
                "#!/bin/sh\n"
                "set -eu\n"
                "root=$(CDPATH= cd -- \"$(dirname -- \"$0\")/..\" && pwd -P)\n"
                f'exec "$root/.venv/bin/python" -c '
                f"'from {_module} import main; raise SystemExit(main())' \"$@\"\n"
            ).encode()
            os.write(descriptor, body)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def remove_contents(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                remove_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)


def rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        fail("atomic no-replace install publication is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1) == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        fail(f"install target already exists: {destination}")
    raise OSError(error, os.strerror(error), destination)


def main() -> None:
    if MODE not in {"current", "legacy"}:
        fail("unknown install mode")
    if not os.path.isfile(GIT) or not os.access(GIT, os.X_OK):
        fail(f"trusted Git binary is unavailable: {GIT}")
    validate_repository_url(REPOSITORY_URL)
    target = Path(os.path.abspath(TARGET_TEXT))
    if target.name in {"", ".", ".."}:
        fail("install target must have a final path component")
    parent_chain = open_directory_chain(target.parent, create=True)
    parent_fd = parent_chain[-1]
    stage_fd = -1
    published = False
    try:
        try:
            os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail(f"install target already exists: {target}")
        stage_name, stage_fd, expected_stage = create_stage(parent_fd, target.name)
        stage = Path(f"/proc/self/fd/{stage_fd}")
        git_environment = safe_environment()
        run_git(stage_fd, git_environment, "clone", "--no-checkout", "--no-hardlinks", "--", REPOSITORY_URL, str(stage))
        config_fd = os.open(".git/config", FILE_FLAGS, dir_fd=stage_fd)
        try:
            config_bytes = b""
            while True:
                chunk = os.read(config_fd, 1024 * 1024)
                if not chunk:
                    break
                config_bytes += chunk
        finally:
            os.close(config_fd)
        if b"credential" in config_bytes.lower():
            fail("cloned repository config contains credential settings")
        if MODE == "legacy":
            resolved = run_git(
                stage_fd,
                git_environment,
                "-C", str(stage), "rev-parse", "--verify", f"refs/tags/{LEGACY_TAG}^{{commit}}",
                capture=True,
            ).stdout.decode("ascii").strip()
            if resolved != LEGACY_COMMIT:
                fail(f"legacy tag resolved to unexpected commit: {resolved}")
            commit = LEGACY_COMMIT
            run_git(stage_fd, git_environment, "-C", str(stage), "update-ref", "--no-deref", "HEAD", commit)
            run_git(stage_fd, git_environment, "-C", str(stage), "read-tree", commit)
        else:
            commit = run_git(
                stage_fd,
                git_environment,
                "-C", str(stage), "rev-parse", "--verify", "HEAD^{commit}",
                capture=True,
            ).stdout.decode("ascii").strip()
            if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                fail("current repository HEAD is not a full commit ID")
        archive_bytes = read_git_archive(stage_fd, git_environment, stage, commit)
        extract_archive(stage_fd, archive_bytes)
        verify_stage_name(parent_fd, stage_name, expected_stage)
        if MODE == "legacy":
            run_legacy_installer(stage, stage_fd)
        else:
            trusted_path = Path(TRUSTED_INSTALLER_TEXT)
            trusted_fd = os.open(trusted_path, FILE_FLAGS)
            try:
                if not stat.S_ISREG(os.fstat(trusted_fd).st_mode):
                    fail("current bootstrap installer is not a regular file")
                trusted_bytes = b""
                while True:
                    chunk = os.read(trusted_fd, 1024 * 1024)
                    if not chunk:
                        break
                    trusted_bytes += chunk
            finally:
                os.close(trusted_fd)
            extracted_fd, extracted_bytes = read_regular_at(stage_fd, "install.sh")
            os.close(extracted_fd)
            if extracted_bytes != trusted_bytes:
                fail("cloned current installer does not match the trusted bootstrap bytes")
            run_current_installer(stage, stage_fd)
        os.fsync(stage_fd)
        if MODE == "legacy":
            verify_legacy_checkout(stage_fd, git_environment, stage)
        verify_stage_name(parent_fd, stage_name, expected_stage)
        verify_directory_chain(target.parent, parent_chain)
        rename_noreplace(parent_fd, stage_name, target.name)
        published = True
        os.fsync(parent_fd)
        verify_directory_chain(target.parent, parent_chain)
        published_metadata = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if (published_metadata.st_dev, published_metadata.st_ino) != (expected_stage.st_dev, expected_stage.st_ino):
            fail("published install target does not match the prepared staging inode")
        if MODE == "legacy":
            verify_legacy_checkout(stage_fd, git_environment, stage)
            command = target / "scripts/hwskill"
            print(f"legacy hwskill installed at immutable commit {LEGACY_COMMIT}")
            print(f"command: {command}")
        else:
            print(f"hwskill sharing tools installed at {target}")
            print("commands:")
            for command, _module in COMMANDS:
                print(f"  {target / 'bin' / command}")
    finally:
        if stage_fd >= 0:
            if not published:
                remove_contents(stage_fd)
            os.close(stage_fd)
        while parent_chain:
            os.close(parent_chain.pop())


try:
    main()
except (OSError, RuntimeError, subprocess.SubprocessError, tarfile.TarError, UnicodeError) as error:
    print(f"hwskill install failed: {error}", file=sys.stderr)
    raise SystemExit(2)
PY
