#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=$(ml_project_name)
testbed=${1:?usage: ml-status.sh testbed [config-dir]}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
services=$(config_host_containers "$config_dir" | paste -sd, -)
coordinator=$(config_coordinator_container "$config_dir")
deployment_kind=$(config_deployment_kind "$config_dir")
config_set=$(basename "$config_dir")
selected_hash=$(config_hash "$config_dir")
args=(--project "$project" --services "$services" --coordinator "$coordinator" \
  --deployment-kind "$deployment_kind" --config-set "$config_set" \
  --config-hash "$selected_hash")
[ -z "${ML_STATUS_CACHE_DIR:-}" ] || args+=(--cache-dir "$ML_STATUS_CACHE_DIR")
python3 "$HOST_ROOT/scripts/host/ml-status.py" "${args[@]}"
