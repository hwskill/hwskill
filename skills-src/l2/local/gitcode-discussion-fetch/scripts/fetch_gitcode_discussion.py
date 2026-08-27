#!/usr/bin/env python3
"""Fetch a GitCode Discussion and render one complete Markdown archive."""

import argparse
import pathlib
import sys

from gitcode_discussion import (
    GitCodeApi,
    GitCodeApiError,
    fetch_discussion_archive,
    parse_target,
    render_markdown,
    write_text_atomic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch one GitCode Discussion URL, or an owner/repo plus Discussion number, "
            "as a complete Markdown archive."
        )
    )
    parser.add_argument(
        "target",
        help="GitCode Discussion URL, or an owner/repo target when NUMBER is supplied",
    )
    parser.add_argument(
        "number",
        nargs="?",
        help="Discussion number when TARGET is owner/repo",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        help="Write Markdown atomically to this path instead of stdout",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        owner, repo, number = parse_target(args.target, args.number)
        archive = fetch_discussion_archive(GitCodeApi(), owner, repo, number)
        markdown = render_markdown(archive)
        if args.output is None:
            sys.stdout.write(markdown)
        else:
            write_text_atomic(args.output, markdown)
    except (ValueError, GitCodeApiError, OSError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
