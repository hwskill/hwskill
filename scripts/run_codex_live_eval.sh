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

if [ -z "${CODEX_API_KEY:-}" ] && ! codex login status >/dev/null 2>&1; then
  echo "Codex authentication is required: existing login or CODEX_API_KEY" >&2
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

exec "$hwskill" test tests/profiles/codex-demo/test.yaml \
  --runner docker --host codex --repo-root "$repo_root" "$@"
