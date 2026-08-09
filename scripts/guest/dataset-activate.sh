#!/usr/bin/env bash
set -euo pipefail

machine=${1:?machine is required}
archive=${2:?dataset archive is required}
expected_set=${3:?dataset set ID is required}
case "$machine" in path-a|path-b) ;; *) echo "datasets may only be activated on path-a or path-b" >&2; exit 2;; esac
[[ "$expected_set" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid dataset set ID" >&2; exit 2; }
test "$(id -u)" -eq 0 || { echo "dataset activation requires root" >&2; exit 1; }
test -f "$archive" || { echo "dataset archive not found: $archive" >&2; exit 1; }

root=/var/lib/5g-nwdaf-infrastructure/datasets
sets=$root/sets
install -d -o 5g-nwdaf -g 5g-nwdaf "$root" "$sets"
stage=$(mktemp -d "$root/.stage.XXXXXX")
cleanup() { rm -rf "$stage"; rm -f "$archive"; }
trap cleanup EXIT

mapfile -t members < <(tar -tzf "$archive" | LC_ALL=C sort)
expected=(file.json manifest.json traffic.parquet)
if [ "${members[*]}" != "${expected[*]}" ]; then
  echo "dataset archive must contain only: ${expected[*]}" >&2
  exit 1
fi
tar -C "$stage" -xzf "$archive"

verify() {
  python3 - "$1" "$machine" "$expected_set" <<'PY'
import hashlib
import json
import pathlib
import sys

root, machine, expected_set = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
manifest = json.loads((root / "manifest.json").read_text())
expected_path = machine
if manifest.get("schemaVersion") != 1:
    raise SystemExit("unsupported path manifest schema")
if manifest.get("datasetSetId") != expected_set or manifest.get("path") != expected_path:
    raise SystemExit("dataset identity does not match target machine")
artifact_name = manifest.get("artifactFile")
if artifact_name != "traffic.parquet":
    raise SystemExit("unexpected dataset artifact name")
artifact = root / artifact_name
if artifact.stat().st_size != manifest.get("bytes"):
    raise SystemExit("dataset byte count does not match manifest")
digest = hashlib.sha256()
with artifact.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest() != manifest.get("sha256"):
    raise SystemExit("dataset hash does not match manifest")
metadata = json.loads((root / "file.json").read_text())
if metadata.get("breaking time") != manifest["profile"]["breakingTimeSeconds"]:
    raise SystemExit("PseudoDriver breaking time does not match profile")
PY
}

verify "$stage"
destination="$sets/$expected_set"
if [ -e "$destination" ]; then
  test -d "$destination" || { echo "dataset destination is not a directory" >&2; exit 1; }
  verify "$destination"
else
  chown -R 5g-nwdaf:5g-nwdaf "$stage"
  mv "$stage" "$destination"
  stage="$root/.stage.consumed"
fi

link="$root/.active.$$.link"
ln -s "$destination" "$link"
mv -Tf "$link" "$root/active"
echo "ACTIVE machine=$machine dataset=$expected_set directory=$destination"
