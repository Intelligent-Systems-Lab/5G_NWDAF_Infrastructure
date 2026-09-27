#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: experiment-start.sh testbed [config-dir]}
explicit_config=${2:-}
select_testbed_machines "$testbed"
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
dataset_args=(--testbed "$testbed")
if [ -n "$explicit_config" ]; then
  dataset_args+=(--config-dir "$explicit_config")
fi
services_started=false
ml_started=false

assert_clean_start() {
  local machine active running
  assert_selected_provider_running
  running=$(docker ps -q --filter "label=com.docker.compose.project=$(ml_project_name)")
  if [ -n "$running" ]; then
    echo "ML containers are already running; use the independent lifecycle targets instead" >&2
    return 1
  fi
  for machine in "${MACHINES[@]}"; do
    active=$(vssh "$machine" "systemctl list-units --state=active --no-legend '5g-nwdaf@*.service' | awk '{print \$1}'" 2>/dev/null | tr -d '\r')
    if [ -n "$active" ]; then
      echo "$machine already has active experiment services; use services-status" >&2
      return 1
    fi
  done
}

rollback() {
  local status=$?
  trap - EXIT
  echo "experiment startup failed; rolling back domains started by this invocation" >&2
  if $ml_started; then
    "$HOST_ROOT/scripts/host/ml-stop.sh" "$testbed" "$explicit_config" || true
  fi
  if $services_started; then
    "$HOST_ROOT/scripts/host/services-stop.sh" "$testbed" "$explicit_config" || true
  fi
  exit "$status"
}

assert_clean_start
python3 "$HOST_ROOT/scripts/host/dataset.py" "${dataset_args[@]}" generate

trap rollback EXIT
"$HOST_ROOT/scripts/host/services-start.sh" "$testbed" "$explicit_config"
services_started=true
"$HOST_ROOT/scripts/host/ml-start.sh" "$testbed" "$explicit_config"
ml_started=true
"$HOST_ROOT/scripts/host/backend-check.sh" "$testbed" "$config_dir"
assert_guest_runtime_identity "$config_dir"
assert_ml_runtime_identity "$testbed" "$config_dir"
trap - EXIT

echo "Experiment processes are active; VM lifecycle and retained state were not changed."
