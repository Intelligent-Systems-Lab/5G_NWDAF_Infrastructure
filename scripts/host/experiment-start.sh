#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
webconsole_enabled=$(config_webconsole_enabled "$config_dir")
subscriptions_mode=$(config_subscriptions_mode "$config_dir")
dataset_args=(--testbed "$testbed")
if [ -n "$explicit_config" ]; then
  dataset_args+=(--config-dir "$explicit_config")
fi
services_started=false
webconsole_started=false
ml_started=false
subscriptions_attempted=false

vm_state() {
  local machine=$1
  (cd "$HOST_ROOT" && provider_vagrant status "$machine" --machine-readable 2>/dev/null) |
    awk -F, '$3 == "state" {state=$4} END {print state}'
}

assert_clean_start() {
  local machine state active running consumer
  for machine in "${MACHINES[@]}"; do
    state=$(vm_state "$machine")
    if [ "$state" != running ]; then
      echo "$machine must be running before experiment-start (state=${state:-unknown})" >&2
      return 1
    fi
  done
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
  consumer=$(vssh core "systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
  if [ "$consumer" = active ]; then
    echo "consumer is already active; use subscriptions-status" >&2
    return 1
  fi
}

rollback() {
  local status=$?
  trap - EXIT
  echo "experiment startup failed; rolling back domains started by this invocation" >&2
  if $subscriptions_attempted; then
    if ! "$HOST_ROOT/scripts/host/subscriptions-stop.sh"; then
      echo "subscription cleanup failed; retaining ML and Guest services so exact DELETE can be retried" >&2
      exit "$status"
    fi
  fi
  if $ml_started; then
    "$HOST_ROOT/scripts/host/ml-stop.sh" "$testbed" "$explicit_config" || true
  fi
  if $webconsole_started; then
    "$HOST_ROOT/scripts/host/webconsole-stop.sh" || true
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
if [ "$webconsole_enabled" = true ]; then
  "$HOST_ROOT/scripts/host/webconsole-start.sh" "$testbed" "$explicit_config"
  webconsole_started=true
fi
"$HOST_ROOT/scripts/host/ml-start.sh" "$testbed" "$explicit_config"
ml_started=true
if [ "$subscriptions_mode" = consumer ]; then
  subscriptions_attempted=true
  "$HOST_ROOT/scripts/host/subscriptions-start.sh"
else
  echo "SUBSCRIPTIONS skipped (mode=$subscriptions_mode)"
fi
trap - EXIT

echo "Experiment processes are active; VM lifecycle and retained state were not changed."
