"""PEP 517 adapter that gives every wheel build a fresh setuptools tree."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Iterator

from setuptools import build_meta as _setuptools


@contextmanager
def _fresh_source_tree() -> Iterator[None]:
    source_root = Path.cwd()
    with TemporaryDirectory(prefix="hwskill-wheel-source-") as temporary:
        build_root = Path(temporary)
        for name in ("pyproject.toml", "build_backend.py", "README.md"):
            candidate = source_root / name
            if candidate.is_file():
                shutil.copy2(candidate, build_root / name)
        shutil.copytree(source_root / "src", build_root / "src", symlinks=True)
        previous = Path.cwd()
        os.chdir(build_root)
        try:
            yield
        finally:
            os.chdir(previous)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    with _fresh_source_tree():
        return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)


build_sdist = _setuptools.build_sdist
build_editable = _setuptools.build_editable
get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_editable = _setuptools.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable
