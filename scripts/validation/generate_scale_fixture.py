#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic directory entries inside an owned release-verification copy.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--size", required=True, type=int, choices=(1000, 10000))
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    if not (root / ".hwskill-verification-owned").is_file():
        parser.error("repo root is not an owned release-verification copy")

    for relative in ("entries", "recommendations", "skills-src"):
        target = root / relative
        if target.exists():
            shutil.rmtree(target)
    entry_root = root / "entries/l3/scale"
    entry_root.mkdir(parents=True)
    template = """schema_version: 1
id: scale/item-{index:05d}
name: Scale Item {index:05d}
summary: 本地构造规模验证条目 {index:05d}。
layer: l3
purposes: [规模验证]
examples:
  - prompt: 检查构造条目 {index:05d}。
    expected_outcome: 返回构造条目。
source:
  kind: external
  publicity: public
  locator:
    type: git
    repository: https://example.test/scale.git
    path: skills/item-{index:05d}
    requested_ref: 0123456789abcdef0123456789abcdef01234567
install:
  method: unknown
  default_scope: project
compatibility:
  agents: [codex]
  systems: [linux]
  requirements: []
license:
  status: unknown
limitations: [仅用于本机临时规模验证]
"""
    for index in range(args.size):
        (entry_root / f"item-{index:05d}.yaml").write_text(template.format(index=index), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
