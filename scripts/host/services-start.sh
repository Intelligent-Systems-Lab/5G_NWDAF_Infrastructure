#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: services-start.sh testbed [config-dir]}
explicit_config=${2:-}
select_testbed_machines "$testbed"
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
deployment_kind=$(config_deployment_kind "$config_dir")
hash=$(config_hash "$config_dir")
echo "EFFECTIVE testbed=$testbed config=$config_dir hash=$hash"
assert_selected_provider_running

rollback() {
  local status=$?
  trap - EXIT
  echo "service startup failed; stopping the experiment stack" >&2
  "$HOST_ROOT/scripts/host/services-stop.sh" "$testbed" "$explicit_config" || true
  exit "$status"
}
trap rollback EXIT
"$HOST_ROOT/scripts/host/guest-tools-sync.sh" "${MACHINES[@]}"
"$HOST_ROOT/scripts/host/provisioning-check.sh" "$testbed" "$config_dir"
stage_config_all "$config_dir" "$hash"
assert_guest_runtime_identity "$config_dir"
if [ "$deployment_kind" != protocol-hierarchical ]; then
  "$HOST_ROOT/scripts/host/dataset-stage.sh" apply "$testbed" "$config_dir"
fi

# Persistent Netplan aliases should already match the active config.  Reconcile
# only migration or runtime drift before any NF binds its topology address.
verify_network() {
  local machine=$1
  echo "NETWORK VERIFY $machine"
  if ! vssh "$machine" "sudo /usr/local/libexec/5g-nwdaf-infrastructure/network-setup --verify"; then
    echo "NETWORK RECONCILE $machine"
    vssh "$machine" "sudo systemctl restart 5g-nwdaf-network.service"
  fi
}
network_pids=()
for machine in "${MACHINES[@]}"; do
  verify_network "$machine" &
  network_pids+=("$!")
done
network_failed=false
for pid in "${network_pids[@]}"; do
  if ! wait "$pid"; then
    network_failed=true
  fi
done
if $network_failed; then
  echo "Guest network verification failed" >&2
  exit 1
fi

if [ "$deployment_kind" = protocol-hierarchical ]; then
  "$HOST_ROOT/scripts/host/clock-check.sh" "$testbed"
else
  "$HOST_ROOT/scripts/host/gtp5g-preflight.sh"
fi

subscriber_data_applied=false
service_records=$(config_guest_service_records "$config_dir")
[ -n "$service_records" ] || { echo "selected Guest service inventory is empty" >&2; exit 1; }
if [ "$deployment_kind" = protocol-hierarchical ]; then
  core_machine=$(config_guest_service_machine "$config_dir" mongodb)
  start_machine_units() {
    local selected_machine=$1 machine unit kind
    while IFS='|' read -r machine unit kind; do
      [ "$machine" = "$selected_machine" ] || continue
      start_unit "$machine" "$unit" || return
    done <<<"$service_records"
  }
  start_machine_units "$core_machine"
  start_pids=()
  for machine in "${MACHINES[@]}"; do
    [ "$machine" = "$core_machine" ] && continue
    start_machine_units "$machine" &
    start_pids+=("$!")
  done
  start_failed=false
  for pid in "${start_pids[@]}"; do
    if ! wait "$pid"; then
      start_failed=true
    fi
  done
  if $start_failed; then
    echo "Guest path startup failed" >&2
    exit 1
  fi
else
  while IFS='|' read -r machine unit kind; do
    if ! $subscriber_data_applied && { [ "$kind" = upf ] || [ "$unit" = smf ]; }; then
      "$HOST_ROOT/scripts/host/subscriber-data.sh" apply "$testbed" "$config_dir"
      subscriber_data_applied=true
    fi
    start_unit "$machine" "$unit"
  done <<<"$service_records"
  if ! $subscriber_data_applied; then
    "$HOST_ROOT/scripts/host/subscriber-data.sh" apply "$testbed" "$config_dir"
  fi
fi
if [ "$deployment_kind" = protocol-hierarchical ]; then
  "$HOST_ROOT/scripts/host/registration-check.sh" "$testbed" "$config_dir"
fi
trap - EXIT
echo "Guest experiment services are active; Host ML containers and subscriptions have not been started."
