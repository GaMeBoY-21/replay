#!/usr/bin/env bash
# Build the Lambda code - both entry points, one folder - without Docker.
#
#   scripts/bundle_lambda.sh   ->  .scratch/lambda-bundle/   (Code.from_asset points here)
#
# Python 3.12 on arm64, as the functions run. What goes in is what the replay-only
# path imports: replay and replay-events, and pydantic's tree pinned from uv.lock
# as Linux aarch64 wheels. Strands is left out on purpose - replay-only never
# imports it (tests/test_aws_entry.py proves it) - and boto3 is left out because
# the Lambda Python runtime provides it.
set -euo pipefail

cd "$(dirname "$0")/.."
OUT=.scratch/lambda-bundle
REQUIREMENTS=.scratch/lambda-requirements.txt

rm -rf "$OUT"
mkdir -p "$OUT"

uv export --frozen --no-hashes --no-dev --no-emit-workspace --package replay-events -o "$REQUIREMENTS" >/dev/null
uv pip install --quiet --target "$OUT" --python-version 3.12 --python-platform aarch64-manylinux2014 \
  --only-binary :all: -r "$REQUIREMENTS"
uv pip install --quiet --target "$OUT" --no-deps ./packages/events ./packages/replay

find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$OUT"/*.dist-info/RECORD

size_kb=$(du -sk "$OUT" | cut -f1)
limit_kb=$((250 * 1024))
echo "bundle: $OUT  $((size_kb / 1024)) MB unzipped (limit 250 MB)"
if [ "$size_kb" -ge "$limit_kb" ]; then
  echo "bundle exceeds the 250 MB unzipped limit" >&2
  exit 1
fi
