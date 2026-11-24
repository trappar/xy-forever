#!/bin/sh
# Run a command in the firmware build container (#321 proof), this checkout mounted at /src.
#
#   docker/run.sh arm64|amd64 xyf-build --xyf-product --out build/product
#
# The image is xyf-toolchain:<platform> (docker/toolchain.Dockerfile). The venv and uv's cache live in named volumes
# per platform, so a run only syncs what uv.lock changed. The private folder is an empty one inside the container,
# as in CI: no stock files.
set -eu
platform=$1
shift
root=$(cd "$(dirname "$0")/.." && pwd)
exec docker run --rm --platform "linux/$platform" \
  -v "$root:/src" -v "xyf-venv-$platform:/opt/venv" -v "xyf-uv-$platform:/root/.cache/uv" \
  -e XYF_PRIVATE=/tmp/private \
  "xyf-toolchain:$platform" \
  sh -c 'mkdir -p "$XYF_PRIVATE" && uv sync --locked --no-group native --quiet && exec uv run --no-sync "$@"' sh "$@"
