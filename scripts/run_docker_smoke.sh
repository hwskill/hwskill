#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image_name=hwskill-smoke:0.1.0

docker build -f "$repo_root/docker/Dockerfile" -t "$image_name" "$repo_root"
docker run --rm --network none "$image_name"
