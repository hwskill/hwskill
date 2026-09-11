from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import shutil

from .digest import sha256_bytes


def hosted_directory(root: Path, source_path: str) -> Path:
    return root.joinpath(*PurePosixPath(source_path).parts)


def directory_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def copy_hosted_content(source: Path, destination: Path) -> str:
    shutil.copytree(source, destination)
    return directory_digest(source)
