#!/bin/sh
set -eu

usage() {
  echo "usage: verify_release.sh [--repo-root PATH] --report PATH [--scale]" >&2
}

repo_root=.
report=
scale=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo-root)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      repo_root=$2
      shift 2
      ;;
    --report)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      report=$2
      shift 2
      ;;
    --scale)
      scale=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[ -n "$report" ] || { usage; exit 2; }
[ -d "$repo_root" ] || { echo "repository root is not a directory: $repo_root" >&2; exit 2; }
[ ! -e "$report" ] && [ ! -L "$report" ] || { echo "report already exists: $report" >&2; exit 2; }

repo_root=$(CDPATH= cd -- "$repo_root" && pwd -P)
report_parent=$(dirname -- "$report")
mkdir -p "$report_parent"
report_parent=$(CDPATH= cd -- "$report_parent" && pwd -P)
report=$report_parent/$(basename -- "$report")
[ ! -e "$report" ] && [ ! -L "$report" ] || { echo "report already exists: $report" >&2; exit 2; }

run_root=$(mktemp -d "${TMPDIR:-/tmp}/hwskill-release-verification.XXXXXX")
report_stage=$(mktemp "$report_parent/.verify-release.XXXXXX")
cleanup() {
  rm -rf -- "$run_root"
  rm -f -- "$report_stage"
}
trap cleanup EXIT HUP INT TERM

source_root=$run_root/source
isolated_home=$run_root/home
logs=$run_root/logs
mkdir -p "$source_root" "$isolated_home" "$logs"
tar -C "$repo_root" \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./build' \
  --exclude='./src/*.egg-info' \
  --exclude='./site/dist' \
  --exclude='./site/.generated' \
  -cf "$run_root/source.tar" .
tar -C "$source_root" -xf "$run_root/source.tar"
touch "$source_root/.hwskill-verification-owned"

failures=0
blocked=0
run_step() {
  step=$1
  shift
  log=$logs/$step.log
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  started_epoch=$(date +%s)
  set +e
  (
    cd "$source_root"
    HOME=$isolated_home
    PYTHONDONTWRITEBYTECODE=1
    PYTHONPATH=$source_root/src
    HWSKILL_RELEASE_VERIFICATION_ACTIVE=1
    export HOME PYTHONDONTWRITEBYTECODE PYTHONPATH HWSKILL_RELEASE_VERIFICATION_ACTIVE
    "$@"
  ) >"$log" 2>&1
  status=$?
  set -e
  finished_epoch=$(date +%s)
  elapsed=$((finished_epoch - started_epoch))
  if [ "$status" -eq 0 ]; then
    result=pass
  elif [ "$status" -eq 125 ]; then
    result=blocked
    blocked=$((blocked + 1))
  else
    result=fail
    failures=$((failures + 1))
  fi
  /usr/bin/python3 - "$step" "$result" "$status" "$started" "$elapsed" "$log" >>"$report_stage" <<'PY'
import base64
import hashlib
import json
from pathlib import Path
import sys

step, result, status, started, elapsed, log_name = sys.argv[1:]
body = Path(log_name).read_bytes()
record = {
    "kind": "step",
    "step": step,
    "result": result,
    "exit_code": int(status),
    "started_at": started,
    "elapsed_seconds": int(elapsed),
    "log_sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
    "log_tail_base64": base64.b64encode(body[-8192:]).decode("ascii"),
    "log_truncated": len(body) > 8192,
}
print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
PY
}

python_command=${PYTHON:-/usr/bin/python3}

initialize_source_snapshot() {
  origin=$1
  /usr/bin/git init -q .
  /usr/bin/git fetch -q --no-tags "$origin" \
    refs/tags/hwskill-legacy-v0.1.0:refs/tags/hwskill-legacy-v0.1.0
  /usr/bin/git add -A
  /usr/bin/git -c user.name=HWSkill-Verification \
    -c user.email=verification@invalid \
    commit -q -m 'isolated release verification snapshot'
}

site_toolchain() {
  command -v node >/dev/null 2>&1 || {
    echo "blocked: supported Linux Node.js is not available" >&2
    return 125
  }
  command -v npm >/dev/null 2>&1 || {
    echo "blocked: npm is not available" >&2
    return 125
  }
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major === 22 && minor >= 19 ? 0 : 1)' || {
    echo "blocked: Node.js must satisfy >=22.19.0 <23" >&2
    return 125
  }
}

site_build() {
  site_toolchain || return $?
  npm --prefix site run build
}

site_index() {
  [ -d site/dist ] || {
    echo "blocked: site build did not produce site/dist" >&2
    return 125
  }
  site_toolchain || return $?
  npm --prefix site run index
}

local_http_read() {
  [ -f site/dist/index.html ] && [ -f site/dist/data/catalog.json ] || {
    echo "blocked: static site artifacts are unavailable" >&2
    return 125
  }
  "$python_command" -c 'from functools import partial; from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path; from threading import Thread; from urllib.request import urlopen; import sys; root=Path(sys.argv[1]); server=ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root))); thread=Thread(target=server.serve_forever, daemon=True); thread.start(); origin=f"http://127.0.0.1:{server.server_port}"; assert urlopen(origin+"/", timeout=5).status == 200; assert urlopen(origin+"/data/catalog.json", timeout=5).status == 200; server.shutdown(); thread.join()' site/dist
}

run_step source-snapshot initialize_source_snapshot "$repo_root"
run_step test-input \
  "$python_command" -m hwskill.directory build --repo-root . --out site/.generated/directory --json
run_step python-tests \
  "$python_command" -m unittest discover -s tests -t . -q
run_step directory-validate \
  "$python_command" -m hwskill.directory validate --repo-root . --json
run_step directory-build \
  "$python_command" -m hwskill.directory build --repo-root . --out "$run_root/directory" --json
run_step site-build site_build
run_step site-index site_index
run_step local-http-read local_http_read
run_step publishing-recovery \
  "$python_command" -m unittest tests.publishing.test_recovery -q
run_step sharing-prepare-ack \
  "$python_command" -m unittest tests.sharing.test_cli.SharingCliTests.test_prepare_writes_batch_without_advancing_until_separate_ack -q

scale_case() {
  size=$1
  site_toolchain || return $?
  "$python_command" scripts/validation/generate_scale_fixture.py --repo-root . --size "$size" &&
    "$python_command" -m hwskill.directory validate --repo-root . --json &&
    "$python_command" -m hwskill.directory build --repo-root . --out "$run_root/scale-$size-directory" --json &&
    npm --prefix site run build &&
    npm exec --prefix site -- pagefind --site site/dist &&
    node site/scripts/check-build.mjs
}

if [ "$scale" = true ]; then
  for size in 1000 10000; do
    run_step "scale-$size" scale_case "$size"
    artifact_kib=0
    index_kib=0
    if [ -d "$source_root/site/dist" ]; then
      artifact_kib=$(du -sk "$source_root/site/dist" | awk '{print $1}')
    fi
    if [ -d "$source_root/site/dist/pagefind" ]; then
      index_kib=$(du -sk "$source_root/site/dist/pagefind" | awk '{print $1}')
    fi
    printf '{"kind":"scale","size":%s,"result":"%s","artifact_kib":%s,"search_index_kib":%s,"environment":"local-only"}\n' \
      "$size" "$result" "$artifact_kib" "$index_kib" >>"$report_stage"
  done
fi

if [ "$failures" -gt 0 ]; then
  overall=fail
  overall_status=1
elif [ "$blocked" -gt 0 ]; then
  overall=blocked
  overall_status=2
else
  overall=pass
  overall_status=0
fi
printf '{"kind":"summary","result":"%s","failed_steps":%s,"blocked_steps":%s,"completed_at":"%s","remote_deployment":"not_run","luna":"not_run","group_delivery":"not_run"}\n' \
  "$overall" "$failures" "$blocked" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$report_stage"

chmod 600 "$report_stage"
if ! ln "$report_stage" "$report"; then
  echo "could not publish report without overwriting: $report" >&2
  exit 2
fi
rm -f -- "$report_stage"
echo "$report"
exit "$overall_status"
