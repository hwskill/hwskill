#!/bin/sh
set -eu

if [ "$#" -eq 1 ] && [ "$1" = preflight ]; then
  test "$(codex --version)" = "codex-cli 0.147.0"
  test "$(claude --version)" = "2.1.141 (Claude Code)"
  test "$(opencode --version)" = "1.14.48"
  exit 0
fi

exec "$@"
