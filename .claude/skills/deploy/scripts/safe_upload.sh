#!/usr/bin/env bash
# Upload a local directory to a Railway volume without the nesting bug.
#
# Railway's `volume files upload` nests the source INSIDE the destination whenever the
# destination already exists (e.g. a previous upload that failed partway through, or a
# retry after a timeout). The result is silent: files land at
# <remote>/<basename>/*.parquet instead of <remote>/*.parquet, and the API's DuckDB
# views quietly skip anything the "if path.exists()" check doesn't find at the expected
# path -- no crash, just a 500 on the endpoints that needed the missing table.
#
# This script makes the safe order (delete destination -> upload -> verify count) the
# only order, rather than a step someone has to remember on every retry.
#
# Usage: safe_upload.sh <local_dir> <remote_path> <volume_name>
set -euo pipefail

local_dir="$1"
remote_path="$2"
volume="$3"

if [ ! -d "$local_dir" ]; then
  echo "error: local directory '$local_dir' does not exist" >&2
  exit 1
fi

expected=$(find "$local_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
echo "local: $expected files in $local_dir"

echo "cleaning $remote_path on the volume (required even on a retry -- see script header)..."
npx --yes @railway/cli ssh -- rm -rf "$remote_path"

echo "verifying it's actually gone..."
if npx --yes @railway/cli ssh -- test -e "$remote_path" 2>/dev/null; then
  echo "error: $remote_path still exists after rm -rf -- aborting rather than risk a nested upload" >&2
  exit 1
fi

echo "uploading..."
npx --yes @railway/cli volume files --volume "$volume" upload "$local_dir" "$remote_path" \
  --overwrite --concurrency 2

echo "verifying the upload landed flat (no nested duplicate directory)..."
actual=$(npx --yes @railway/cli ssh -- find "$remote_path" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')

# The nesting bug specifically creates a subdirectory whose name matches the
# destination's OWN basename (e.g. .../marts/marts/*.parquet). A legitimate
# subdirectory with any OTHER name (data/marts/shapes/, for instance) is fine and
# must not trip this check -- checking "any subdirectory at all" was tried first and
# produced a false positive on marts/shapes/ despite a perfectly correct upload.
base=$(basename "$remote_path")
nested_dup="no"
if npx --yes @railway/cli ssh -- test -d "$remote_path/$base" 2>/dev/null; then
  nested_dup="yes"
fi

echo "remote: $actual files (local had $expected); self-named nested duplicate: $nested_dup"

if [ "$actual" != "$expected" ]; then
  echo "error: expected $expected files, remote has $actual -- upload is incomplete or nested" >&2
  echo "check with: railway ssh -- find $remote_path" >&2
  exit 1
fi

if [ "$nested_dup" = "yes" ]; then
  echo "error: $remote_path/$base exists -- classic nesting bug, upload landed inside itself" >&2
  echo "check with: railway ssh -- find $remote_path" >&2
  exit 1
fi

echo "OK: $remote_path matches local ($expected files, no nested duplicate)"
