#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image_name=hwskill-agent-eval:0.1.0
run_root=$(mktemp -d /tmp/hwskill-claude-agent-XXXXXX)
agent_home="$run_root/agent-home"
credentials="$run_root/credentials"
auth_source=${OPENCODE_AUTH_FILE:-"$HOME/.local/share/opencode/auth.json"}
artifact_parent="$repo_root/artifacts/claude-code-gitcode-pr-agent-eval"
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
artifact_root="$artifact_parent/$run_id"
workspace="$artifact_root/workspace"
diagnostic="$artifact_root/credential-diagnostic.txt"
trap 'rm -rf "$run_root"' EXIT INT TERM

mkdir -p "$agent_home" "$credentials" "$workspace" "$artifact_parent"
if ! "$repo_root/scripts/filter_minimax_auth.sh" \
  "$auth_source" "$credentials/minimax-auth.json" 2>"$diagnostic"; then
  cat "$diagnostic" >&2
  echo "Artifacts: $artifact_root" >&2
  exit 2
fi
rm -f "$diagnostic"

docker build -f "$repo_root/docker/Dockerfile" -t "$image_name" "$repo_root"
if docker run --rm \
  --user "$(id -u):$(id -g)" \
  --env HOME=/agent-home --env XDG_DATA_HOME=/agent-home/.local/share \
  --env XDG_CONFIG_HOME=/agent-home/.config \
  --volume "$workspace:/workspace" \
  --volume "$agent_home:/agent-home" \
  --volume "$credentials/minimax-auth.json:/credentials/minimax-auth.json:ro" \
  --entrypoint /opt/hwskills/docker/claude-code-gitcode-agent-eval.sh \
  "$image_name"; then
  status=0
else
  status=$?
fi

echo "Artifacts: $artifact_root"
exit "$status"
