#!/usr/bin/env python3

"""Fetch a complete GitCode pull-request change as a standard text patch."""

import argparse
import os
import pathlib
import sys
from typing import Mapping, Optional, Sequence

from gitcode_patch import PatchFormatError, render_git_patch
from gitcode_pr_common import (
    DEFAULT_API_BASE,
    DEFAULT_TOKEN_ENV,
    GitCodeApi,
    GitCodeApiError,
    fetch_changed_files,
    fetch_pull_request,
    parse_target,
    pull_paths,
    write_text_atomic,
)


def _positive_int_argument(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _per_page_argument(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 100:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a text-only GitCode PR patch for git apply against its "
            "comparison preimage."
        ),
        epilog=(
            "For non-empty patches, files[].patch corresponds to a comparison "
            "preimage; api_base/head/state are API metadata only. Merged or "
            "target-advanced PRs may require an earlier historical preimage.\n"
            "Binary and too-large diffs are rejected.\n"
            "Empty PRs emit a zero-byte patch; verify with "
            "git apply --allow-empty."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", help="GitCode PR URL or owner/repo")
    parser.add_argument("number", nargs="?", help="PR number when target is owner/repo")
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        help="atomically write only patch text to PATH instead of stdout",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--per-page", type=_per_page_argument, default=100)
    parser.add_argument("--timeout", type=_positive_int_argument, default=30)
    return parser


def _pull_sha(pull_request: Mapping[str, object], side: str) -> str:
    reference = pull_request.get(side)
    sha = reference.get("sha") if isinstance(reference, Mapping) else None
    if not isinstance(sha, str) or not sha:
        raise ValueError(f"pull request is missing {side}.sha")
    return sha


def _pull_state(pull_request: Mapping[str, object]) -> str:
    state = pull_request.get("state")
    if not isinstance(state, str) or not state.strip():
        return "unknown"
    return state.strip().lower()


def _file_path(file_info: Mapping[str, object]) -> str:
    patch = file_info.get("patch")
    if isinstance(patch, Mapping):
        path = patch.get("new_path") or patch.get("old_path")
        if isinstance(path, str) and path:
            return path
    return "<unknown>"


def _render_with_file_context(
    files: Sequence[Mapping[str, object]], number: int
) -> str:
    try:
        output = render_git_patch(files)
    except PatchFormatError as error:
        for file_info in files:
            try:
                render_git_patch([file_info])
            except PatchFormatError as file_error:
                raise PatchFormatError(
                    f"PR #{number} file {_file_path(file_info)}: {file_error}"
                ) from error
        raise PatchFormatError(f"PR #{number}: {error}") from error
    if files and not output:
        raise PatchFormatError(f"PR #{number} rendered an empty patch")
    return output


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        owner, repo, number = parse_target(args.target, args.number)
        api = GitCodeApi(
            api_base=args.api_base,
            token=os.environ.get(DEFAULT_TOKEN_ENV),
            timeout=args.timeout,
            per_page=args.per_page,
        )
        _, pull_path = pull_paths(owner, repo, number)
        pull_request = fetch_pull_request(api, pull_path)
        files = fetch_changed_files(api, pull_path)
        output = _render_with_file_context(files, number)
        state = _pull_state(pull_request)
        base_sha = _pull_sha(pull_request, "base")
        head_sha = _pull_sha(pull_request, "head")

        if args.output:
            write_text_atomic(args.output, output)
        else:
            sys.stdout.write(output)
        print(
            f"patch: state={state} api_base={base_sha} "
            f"head={head_sha} files={len(files)}",
            file=sys.stderr,
        )
        return 0
    except (GitCodeApiError, PatchFormatError, ValueError, OSError) as error:
        message = str(error)
        if (
            isinstance(error, GitCodeApiError)
            and error.status_code == 401
            and not os.environ.get(DEFAULT_TOKEN_ENV)
        ):
            message += f"; set {DEFAULT_TOKEN_ENV} for a private repository"
        print(f"error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
