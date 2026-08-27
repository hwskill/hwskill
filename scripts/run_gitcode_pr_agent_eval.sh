#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image_name=hwskill-gitcode-agent-eval:0.1.0
run_root=$(mktemp -d /tmp/hwskill-gitcode-agent-XXXXXX)
workspace="$run_root/workspace"
codex_home="$run_root/codex-home"
artifact_parent="$repo_root/artifacts/gitcode-pr-agent-eval"
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
artifact_root="$artifact_parent/$run_id"
trap 'rm -rf "$run_root"' EXIT INT TERM

mkdir -p "$workspace" "$codex_home" "$artifact_parent"
chmod 700 "$codex_home"

if [ -z "${CODEX_API_KEY:-}" ]; then
  auth_source="${CODEX_HOME:-$HOME/.codex}/auth.json"
  if [ ! -f "$auth_source" ]; then
    echo "Codex authentication required: CODEX_API_KEY or existing codex login" >&2
    exit 2
  fi
  cp "$auth_source" "$codex_home/auth.json"
  chmod 600 "$codex_home/auth.json"
fi

docker build -f "$repo_root/docker/Dockerfile" -t "$image_name" "$repo_root"

if [ -n "${CODEX_API_KEY:-}" ]; then
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/codex-home --env CODEX_HOME=/codex-home --env CODEX_API_KEY \
    --volume "$workspace:/workspace" \
    --volume "$codex_home:/codex-home" \
    --entrypoint /opt/hwskills/docker/gitcode-agent-eval.sh \
    "$image_name"
else
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/codex-home --env CODEX_HOME=/codex-home \
    --volume "$workspace:/workspace" \
    --volume "$codex_home:/codex-home" \
    --entrypoint /opt/hwskills/docker/gitcode-agent-eval.sh \
    "$image_name"
fi

mkdir -p "$artifact_root"
cp "$workspace/artifacts/codex.jsonl" "$artifact_root/codex.jsonl"
cp "$workspace/artifacts/hwskill-audit.jsonl" "$artifact_root/hwskill-audit.jsonl"
cp "$workspace/artifacts/script-resolution.json" "$artifact_root/script-resolution.json"
cp "$workspace/artifacts/pr-587.patch" "$artifact_root/pr-587.patch"
echo "Artifacts: $artifact_root"
