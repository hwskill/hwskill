#!/bin/sh
set -eu

# Keep the credential in a non-exported shell variable and pin command lookup
# before running any helper.  Only the verified Codex process receives it.
luna_api_key=${CODEX_API_KEY:-}
unset CODEX_API_KEY
PATH=/usr/bin:/bin
export PATH

script_path=$0
while [ -L "$script_path" ]; do
  link_target=$(readlink "$script_path")
  case "$link_target" in
    /*) script_path=$link_target ;;
    *) script_path=$(dirname -- "$script_path")/$link_target ;;
  esac
done
repo_root=$(CDPATH= cd -- "$(dirname -- "$script_path")/../.." && pwd -P)
observer=$repo_root/tests/agent-experience/observer.py
tasks_dir=$repo_root/tests/agent-experience/tasks

execute=false
output=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute) execute=true ;;
    --output)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--output requires a path" >&2
        exit 64
      fi
      output=$1
      ;;
    -h|--help)
      echo "usage: scripts/validation/run_clean_luna_eval.sh [--execute] --output FILE"
      echo "without --execute, writes an honest blocked/not_run record and performs no model call"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 64
      ;;
  esac
  shift
done
if [ -z "$output" ]; then
  echo "--output is required" >&2
  exit 64
fi

python_bin=/usr/bin/python3
if [ ! -x "$python_bin" ]; then
  echo "python3 is required" >&2
  exit 3
fi

temporary_parent=${TMPDIR:-/tmp}
evaluation_root=$(mktemp -d "$temporary_parent/clean-luna-eval.XXXXXX")
cleanup() {
  case "$evaluation_root" in
    "$temporary_parent"/clean-luna-eval.*)
      chmod -R u+w "$evaluation_root" 2>/dev/null || true
      rm -rf -- "$evaluation_root"
      ;;
    *)
      echo "refusing to remove unexpected evaluation path: $evaluation_root" >&2
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM

temporary_home=$evaluation_root/home
temporary_codex=$evaluation_root/codex-config
temporary_project_root=$evaluation_root/projects
temporary_runtime=$evaluation_root/tmp
evidence_root=$evaluation_root/evidence
observations_root=$evaluation_root/observations
mkdir -m 700 "$temporary_home" "$temporary_codex" "$temporary_project_root" "$temporary_runtime" "$evidence_root" "$observations_root"

isolation=$evaluation_root/isolation.json
printf '%s\n' '{"temporary_home":true,"temporary_config_root":true,"temporary_project":true,"inherited_skills":false,"inherited_profile":false,"inherited_hooks":false,"inherited_mcp":false,"public_inputs_only":true,"minimal_authentication":true}' > "$isolation"

blocked() {
  reason=$1
  set +e
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" blocked \
    --tasks-dir "$tasks_dir" \
    --repo-root "$repo_root" \
    --isolation-metadata "$isolation" \
    --reason "$reason" \
    --output "$output"
  status=$?
  set -e
  return "$status"
}

abort_blocked() {
  blocked "$1"
  exit $?
}

if [ "$execute" != true ]; then
  abort_blocked "Luna execution was not requested; deterministic public-input preflight completed"
fi
if [ -z "$luna_api_key" ]; then
  abort_blocked "CODEX_API_KEY is unavailable; Luna tasks were not run and no saved login was consulted"
fi

host_proof=$evidence_root/host-proof.json
codex_bin=
for candidate in /usr/bin/codex /usr/local/bin/codex /opt/codex/bin/codex; do
  if [ -e "$candidate" ]; then
    if ! exec 9< "$candidate"; then
      continue
    fi
    if PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" probe-host-fd \
      --host-executable "$candidate" --host-fd 9 \
      --forbidden-root "$repo_root" \
      --forbidden-root "${HOME:-/nonexistent}" \
      --forbidden-root "${CODEX_HOME:-/nonexistent}" \
      --forbidden-root "$temporary_parent" \
      --output "$host_proof" >/dev/null 2>&1; then
      codex_bin=$candidate
      break
    fi
    exec 9<&-
  fi
done
if [ -z "$codex_bin" ]; then
  abort_blocked "no root-owned, non-writable Codex executable exists in the fixed production allowlist"
fi

for task_file in "$tasks_dir"/*.json; do
  case "$task_file" in
    *.schema.json) continue ;;
  esac
  task_name=$(basename "$task_file" .json)
  project=$temporary_project_root/$task_name
  baseline=$evidence_root/$task_name-baseline.json
  transcript=$evidence_root/$task_name-transcript.jsonl
  run_metadata=$evidence_root/$task_name-run.json
  observation=$observations_root/$task_name.json
  frozen=$evidence_root/$task_name-frozen

  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" prepare-task \
    --repo-root "$repo_root" --task "$task_file" --destination "$project" >/dev/null; then
    abort_blocked "public-input workspace preparation failed before Luna execution"
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" snapshot \
    --workspace "$project" --output "$baseline"; then
    abort_blocked "baseline snapshot failed before Luna execution"
  fi
  if ! prompt=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" task-prompt \
    --task "$project/.evaluation/task.json"); then
    abort_blocked "sanitised task prompt could not be read"
  fi
  started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set +e
  env -i \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME="$temporary_home" \
    CODEX_HOME="$temporary_codex" \
    XDG_CONFIG_HOME="$temporary_codex" \
    TMPDIR="$temporary_runtime" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$project/src" \
    CODEX_API_KEY="$luna_api_key" \
    /proc/self/fd/9 exec \
      --ephemeral \
      --ignore-user-config \
      --ignore-rules \
      --model gpt-5.6-luna \
      -c 'model_reasoning_effort="medium"' \
      -c 'shell_environment_policy.inherit="none"' \
      --sandbox workspace-write \
      --ask-for-approval never \
      --skip-git-repo-check \
      --json \
      -C "$project" \
      "$prompt" > "$transcript" 2>&1
  runner_status=$?
  set -e
  completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [ "$runner_status" -ne 0 ]; then
    abort_blocked "Luna host execution failed before a complete observation could be recorded"
  fi

  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" record-run \
    --transcript "$transcript" \
    --host-executable "$codex_bin" \
    --host-fd 9 \
    --host-proof "$host_proof" \
    --started-at "$started_at" \
    --completed-at "$completed_at" \
    --exit-code "$runner_status" \
    --invocation-argument="$codex_bin" \
    --invocation-argument=exec \
    --invocation-argument=--ephemeral \
    --invocation-argument=--ignore-user-config \
    --invocation-argument=--ignore-rules \
    --invocation-argument=--model \
    --invocation-argument=gpt-5.6-luna \
    --invocation-argument=-c \
    --invocation-argument='model_reasoning_effort="medium"' \
    --invocation-argument=-c \
    --invocation-argument='shell_environment_policy.inherit="none"' \
    --output "$run_metadata"; then
    abort_blocked "Luna host metadata or version recording failed"
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" freeze-workspace \
    --workspace "$project" --destination "$frozen" >/dev/null; then
    abort_blocked "post-run workspace could not be frozen safely"
  fi
  set +e
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" observe \
    --task "$task_file" \
    --workspace "$frozen" \
    --baseline "$baseline" \
    --run-metadata "$run_metadata" \
    --output "$observation"
  observer_status=$?
  set -e
  if [ "$observer_status" -gt 1 ]; then
    abort_blocked "deterministic observation infrastructure failed"
  fi
done
exec 9<&-

set +e
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" "$python_bin" "$observer" aggregate \
  --observations-dir "$observations_root" \
  --isolation-metadata "$isolation" \
  --tasks-dir "$tasks_dir" \
  --output "$output"
status=$?
set -e
exit "$status"
