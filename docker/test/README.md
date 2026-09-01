# Standard test image

`docker/test/Dockerfile` is the standard execution environment for `hwskill test`.
The base image digest, Agent CLI versions, exact-version Python dependency
closure, and image identity labels are fixed in this directory. The Python
lock currently pins every resolved package version but does not include wheel
content hashes. Build only through
`hwskill test setup`; `setup --check` performs a read-only identity inspection.

At run time the repository and `tests/` are mounted read-only. The workspace is
a container-private executable tmpfs, while action artifacts first live in a
separate noexec `/artifacts` tmpfs. The host artifact root is mounted separately at `/export`
and is not exposed to Agent or command subprocesses. The request contains only
test selection, model configuration, and approved credential environment *names*;
secret values remain in the Docker client's inherited environment. Supported
login files are mounted read-only at fixed `/credentials/...` destinations.

Before any credentialed worker starts, the host launches the already-inspected
immutable image digest in a separate preflight container. That container has no
credential environment, credential files, repository, workspace, or artifact
mounts; it is read-only, capability-free, network-disabled, and verifies all
three Agent CLI versions exactly. Only a successful same-digest preflight
allows the worker request to run. The request binds the selected host to that
verified exact version, and the worker reports the bound value without probing
Agent CLIs again inside the credentialed container.

Command-only selections run with Docker networking disabled. A selection that
contains an Agent action uses Docker's ordinary bridge network because the
Agent needs its configured provider; the container is still read-only,
capability-free, unprivileged, and has no Docker socket. There is no fallback to
host-local execution. Use `--runner local` explicitly when debugging.
Within an Agent-capable container, every ordinary command and command
post-check additionally runs under an unprivileged Landlock filesystem
allowlist and the socket seccomp guard. It receives no Agent credential
environment, cannot read `/proc` or `/credentials`, and is reported BLOCKED if
the kernel cannot install either isolation layer.

The Agent process receives configured credentials and is therefore part of the
credential-holding trust boundary. It runs under a filesystem allowlist that
cannot access `/export`. After all actions finish, the trusted worker exports
only descriptor-anchored regular files, rejects links and unsafe mutations,
enforces file/count/total-size limits, and removes configured raw secret bytes
from binary as well as text content before writing the host mount. Detection
makes the run BLOCKED. This contract prevents configured raw credential values
from reaching host artifacts. An Agent that deliberately encodes or transforms
a credential creates content that a general-purpose runner cannot reliably
distinguish from legitimate business output; such deliberate transformation is
outside this raw-secret boundary.

Offline worker smoke (after the image has been built) must mount the repository
and tests read-only, provide a writable host artifact root plus a private
temporary workspace, and
invoke the same strict worker entry point used by `hwskill test`. For example,
create a request containing this payload:

```json
{
  "schema_version": 1,
  "selections": [{"kind": "core", "path": "tests/core/test_models.py"}],
  "host": "codex",
  "host_version": "0.147.0",
  "model": "smoke-model",
  "reasoning": "high",
  "timeout_seconds": 120,
  "credential_environment": []
}
```

Mount that file read-only at `/run/request.json`, then run:

```sh
docker run --rm --init --read-only --network none --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 \
  --user "$(id -u):$(id -g)" \
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=$(id -u),gid=$(id -g)" \
  --env HOME=/tmp \
  <verified-image-digest> preflight

docker run --rm --init --read-only --network none --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 512 \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m \
  --tmpfs "/artifacts:rw,nosuid,nodev,noexec,size=256m,mode=0700,uid=$(id -u),gid=$(id -g)" \
  --tmpfs "/workspace:rw,nosuid,nodev,exec,size=256m,mode=0700,uid=$(id -u),gid=$(id -g)" \
  --mount type=bind,src="$PWD",dst=/registry,readonly \
  --mount type=bind,src="$PWD/tests",dst=/tests,readonly \
  --mount type=bind,src="$ARTIFACTS",dst=/export \
  --mount type=bind,src="$REQUEST",dst=/run/request.json,readonly \
  --env HOME=/workspace/home \
  --env HWSKILL_REGISTRY_ROOT=/registry \
  <verified-image-digest> python -m hwskill.test_worker --request /run/request.json
```

Success is exit code 0 with a schema-versioned `/export/result.json`. Failure
is 1 and an infrastructure/configuration block is 3.
