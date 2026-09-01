#!/bin/sh
set -eu

script_path=$0
while [ -L "$script_path" ]; do
  link_target=$(readlink "$script_path")
  case "$link_target" in
    /*) script_path=$link_target ;;
    *) script_path=$(dirname -- "$script_path")/$link_target ;;
  esac
done
repo_root=$(CDPATH= cd -- "$(dirname -- "$script_path")/.." && pwd -P)
auth_source=${OPENCODE_AUTH_FILE:-"$HOME/.local/share/opencode/auth.json"}

if [ ! -r "$auth_source" ]; then
  echo "Claude Code GitCode eval requires the existing OpenCode MiniMax login: $auth_source" >&2
  exit 2
fi

credential_dir=$(mktemp -d /tmp/hwskill-claude-minimax-XXXXXX)
trap 'rm -rf "$credential_dir"' EXIT INT TERM
filtered_auth="$credential_dir/minimax-auth.json"
if ! "$repo_root/scripts/filter_minimax_auth.sh" "$auth_source" "$filtered_auth"; then
  exit 2
fi

if [ -x "$repo_root/.venv/bin/hwskill" ]; then
  hwskill="$repo_root/.venv/bin/hwskill"
elif command -v hwskill >/dev/null 2>&1; then
  hwskill=$(command -v hwskill)
else
  echo "Install hwskill first (for example: .venv/bin/pip install -e .)" >&2
  exit 2
fi

if HWSKILL_MINIMAX_AUTH_FILE="$filtered_auth" "$hwskill" \
  test tests/skills/local/gitcode-pr-review-fetch/test.yaml \
  --runner docker --host claude-code --repo-root "$repo_root" "$@"; then
  exit 0
else
  exit $?
fi
