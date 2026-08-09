#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT

first="$temporary/first"
second="$temporary/second"
python3 "$HOST_ROOT/scripts/host/dataset.py" --testbed "$testbed" --config-dir "$explicit_config" --output-root "$first" generate
python3 "$HOST_ROOT/scripts/host/dataset.py" --testbed "$testbed" --config-dir "$explicit_config" --output-root "$second" generate

python3 - "$first" "$second" <<'PY'
import json
import pathlib
import sys

roots = []
for parent in map(pathlib.Path, sys.argv[1:]):
    children = [item for item in parent.iterdir() if item.is_dir()]
    if len(children) != 1:
        raise SystemExit("expected exactly one generated dataset under {}".format(parent))
    roots.append(children[0])
left = json.loads((roots[0] / "manifest.json").read_text())
right = json.loads((roots[1] / "manifest.json").read_text())
if left != right:
    raise SystemExit("independent generations produced different manifests")
print("OK deterministic dataset={} paths={}".format(left["datasetSetId"], len(left["paths"])))
PY

info_output=$(python3 "$HOST_ROOT/scripts/host/dataset.py" --testbed "$testbed" --config-dir "$explicit_config" --output-root "$first" locate)
mapfile -t info <<<"$info_output"
set_root=${info[1]}
printf 'tamper' >>"$set_root/path-a/traffic.parquet"
if python3 "$HOST_ROOT/scripts/host/dataset.py" --testbed "$testbed" --config-dir "$explicit_config" --output-root "$first" check >/dev/null 2>&1; then
  echo "tampered dataset passed semantic validation" >&2
  exit 1
fi
echo "OK tampered Parquet is rejected"
