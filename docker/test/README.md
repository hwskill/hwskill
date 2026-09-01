# Standard test image

`docker/test/Dockerfile` is the standard execution environment for `hwskill test`.
The base image digest, Agent CLI versions, exact-version Python dependency
closure, and image identity labels are fixed in this directory. The Python
lock currently pins every resolved package version but does not include wheel
content hashes. Build only through
`hwskill test setup`; `setup --check` performs a read-only identity inspection.

At run time the repository and `tests/` are mounted read-only. The artifact and
workspace roots are separate writable mounts. The request contains only test
selection, model configuration, and approved credential environment *names*;
secret values remain in the Docker client's inherited environment. Supported
login files are mounted read-only at fixed `/credentials/...` destinations.

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

Offline worker smoke (after the image has been built) must mount the repository
and tests read-only, provide separate writable artifact/workspace roots, and
invoke the same strict worker entry point used by `hwskill test`. For example,
create a request containing this payload:

```json
{
  "schema_version": 1,
  "selections": [{"kind": "core", "path": "tests/core/test_models.py"}],
  "host": "codex",
  "model": "smoke-model",
  "reasoning": "high",
  "timeout_seconds": 120,
  "credential_environment": []
}
```

Mount that file read-only at `/run/request.json`, then run:

```sh
docker run --rm --init --read-only --network none --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 512 \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m \
  --mount type=bind,src="$PWD",dst=/registry,readonly \
  --mount type=bind,src="$PWD/tests",dst=/tests,readonly \
  --mount type=bind,src="$ARTIFACTS",dst=/artifacts \
  --mount type=bind,src="$WORKSPACE",dst=/workspace \
  --mount type=bind,src="$REQUEST",dst=/run/request.json,readonly \
  --env HOME=/workspace/home \
  --env HWSKILL_REGISTRY_ROOT=/registry \
  hwskill-test:0.1.0 python -m hwskill.test_worker --request /run/request.json
```

Success is exit code 0 with a schema-versioned `/artifacts/result.json`. Failure
is 1 and an infrastructure/configuration block is 3.
