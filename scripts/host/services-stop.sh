#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")

assert_guest_runtime_identity "$config_dir"
vm_records=$(vm_state_records)
service_record_lines=$(config_guest_service_records "$config_dir")
[ -n "$service_record_lines" ] || { echo "selected Guest service inventory is empty" >&2; exit 1; }
mapfile -t service_records <<<"$service_record_lines"
for ((index=${#service_records[@]}-1; index>=0; index--)); do
  IFS='|' read -r machine unit _ <<<"${service_records[$index]}"
  state=$(awk -F'|' -v wanted="$machine" '$1 == wanted {print $2}' <<<"$vm_records")
  if [ "$state" = running ]; then
    stop_unit "$machine" "$unit"
  else
    echo "SKIP  $machine/$unit (VM state=${state:-unknown})"
  fi
done
assert_no_active_guest_units
echo "Experiment services stopped; VM power state was not changed."
