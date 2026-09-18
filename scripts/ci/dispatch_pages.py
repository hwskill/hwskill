#!/usr/bin/env python3
"""Dispatch a Pages release only for the current source main commit."""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request


SOURCE_REMOTE = "https://github.com/hwskill/hwskill.git"
DISPATCH_URL = "https://api.github.com/repos/hwskill/hwskill.github.io/actions/workflows/deploy.yml/dispatches"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_sha", help="full source commit SHA from the validated push")
    parser.add_argument("--source-remote", default=SOURCE_REMOTE)
    parser.add_argument("--api-url", default=DISPATCH_URL)
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        print("source SHA must be 40 lowercase hexadecimal characters", file=sys.stderr)
        return 2

    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", args.source_remote, "refs/heads/main"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        fields = result.stdout.strip().split()
        if len(fields) != 2 or fields[1] != "refs/heads/main" or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
            raise ValueError("invalid source main response")
        current_sha = fields[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"cannot verify source main: {type(error).__name__}", file=sys.stderr)
        return 2

    if args.source_sha != current_sha:
        print(f"Skipping obsolete source commit {args.source_sha}; current main is {current_sha}")
        return 0

    token = os.environ.get("PAGES_DISPATCH_TOKEN")
    if not token:
        print("PAGES_DISPATCH_TOKEN is missing", file=sys.stderr)
        return 2

    payload = json.dumps({
        "ref": "main",
        "inputs": {"source_sha": args.source_sha, "release_mode": "automatic"},
    }).encode("utf-8")
    request = urllib.request.Request(
        args.api_url,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.status not in (200, 204):
                print(f"Pages dispatch failed with HTTP {response.status}", file=sys.stderr)
                return 1
    except urllib.error.HTTPError as error:
        print(f"Pages dispatch failed with HTTP {error.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"Pages dispatch failed: {type(error).__name__}", file=sys.stderr)
        return 1

    run_url = None
    if body:
        try:
            run_url = json.loads(body).get("html_url")
        except (ValueError, AttributeError):
            pass
    print(f"Pages dispatch accepted for {args.source_sha}" + (f": {run_url}" if run_url else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
