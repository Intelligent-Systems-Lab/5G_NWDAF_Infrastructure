#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: services-stop.sh testbed [config-dir]}
explicit_config=${2:-}
select_testbed_machines "$testbed"
config_dir=$(effective_config_dir "$testbed" "$explicit_config")

assert_selected_provider_running
assert_guest_runtime_identity "$config_dir"
vm_records=$(vm_state_records)
service_record_lines=$(config_guest_service_records "$config_dir")
[ -n "$service_record_lines" ] || { echo "selected Guest service inventory is empty" >&2; exit 1; }
mapfile -t service_records <<<"$service_record_lines"
deployment_kind=$(config_deployment_kind "$config_dir")
stop_machine_units() {
  local selected_machine=$1 index machine unit state
  for ((index=${#service_records[@]}-1; index>=0; index--)); do
    IFS='|' read -r machine unit _ <<<"${service_records[$index]}"
    [ "$machine" = "$selected_machine" ] || continue
    state=$(awk -F'|' -v wanted="$machine" '$1 == wanted {print $2}' <<<"$vm_records")
    if [ "$state" = running ]; then
      stop_unit "$machine" "$unit"
    else
      echo "SKIP  $machine/$unit (VM state=${state:-unknown})"
    fi
  done
}
if [ "$deployment_kind" = protocol-hierarchical ]; then
  core_machine=$(config_guest_service_machine "$config_dir" mongodb)
  stop_pids=()
  for machine in "${MACHINES[@]}"; do
    [ "$machine" = "$core_machine" ] && continue
    stop_machine_units "$machine" &
    stop_pids+=("$!")
  done
  stop_failed=false
  for pid in "${stop_pids[@]}"; do
    if ! wait "$pid"; then
      stop_failed=true
    fi
  done
  if $stop_failed; then
    echo "Guest path stop failed" >&2
    exit 1
  fi
  stop_machine_units "$core_machine"
else
  for ((index=${#service_records[@]}-1; index>=0; index--)); do
    IFS='|' read -r machine unit _ <<<"${service_records[$index]}"
    state=$(awk -F'|' -v wanted="$machine" '$1 == wanted {print $2}' <<<"$vm_records")
    if [ "$state" = running ]; then
      stop_unit "$machine" "$unit"
    else
      echo "SKIP  $machine/$unit (VM state=${state:-unknown})"
    fi
  done
fi
assert_no_active_guest_units
echo "Experiment services stopped; VM power state was not changed."
