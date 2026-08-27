#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

action=${1:-plan}
testbed=${2:-testbed.yaml}
explicit_config=${3:-}
case "$action" in plan|apply) ;; *) echo "usage: dataset-stage.sh plan|apply [testbed] [config-dir]" >&2; exit 2;; esac

stage_temporary=
cleanup() {
  if [ -n "$stage_temporary" ]; then
    rm -rf "$stage_temporary"
  fi
}
trap cleanup EXIT

for path_name in a b; do
  info_output=$(python3 "$HOST_ROOT/scripts/host/dataset.py" --testbed "$testbed" --config-dir "$explicit_config" locate --path "$path_name")
  mapfile -t info <<<"$info_output"
  set_id=${info[0]}
  path_root=${info[1]}
  schema=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["schemaVersion"])' "$path_root/manifest.json")
  [[ "$schema" =~ ^[1-9][0-9]*$ ]] || { echo "invalid dataset schema: $schema" >&2; exit 1; }
  machine="path-$path_name"
  echo "DATASET $machine schema=$schema set=$set_id source=$path_root target=/var/lib/5g-nwdaf-infrastructure/datasets/active"
  if [ "$action" = plan ]; then
    continue
  fi
  stage_temporary=$(mktemp -d)
  archive="$stage_temporary/dataset.tgz"
  tar -C "$path_root" -czf "$archive" traffic.parquet file.json manifest.json
  remote="/tmp/5g-nwdaf-dataset-${set_id:0:16}-${path_name}.tgz"
  remote_activate="/tmp/5g-nwdaf-dataset-activate-${set_id:0:16}.sh"
  remote_runner="/tmp/5g-nwdaf-service-run-${set_id:0:16}.sh"
  (cd "$HOST_ROOT" && provider_vagrant upload "$archive" "$remote" "$machine")
  (cd "$HOST_ROOT" && provider_vagrant upload "$HOST_ROOT/scripts/guest/dataset-activate.sh" "$remote_activate" "$machine")
  (cd "$HOST_ROOT" && provider_vagrant upload "$HOST_ROOT/scripts/guest/service-run.sh" "$remote_runner" "$machine")
  vssh "$machine" "sudo install -m 0755 '$remote_activate' /usr/local/libexec/5g-nwdaf-infrastructure/dataset-activate && sudo install -m 0755 '$remote_runner' /usr/local/libexec/5g-nwdaf-infrastructure/service-run && rm -f '$remote_activate' '$remote_runner' && sudo /usr/local/libexec/5g-nwdaf-infrastructure/dataset-activate '$machine' '$remote' '$set_id' '$schema'"
  rm -rf "$stage_temporary"
  stage_temporary=
done
