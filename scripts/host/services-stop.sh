#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

stop_machine_units() {
  local machine=$1 state=$2 unit
  shift 2
  if [ "$state" != running ]; then
    echo "SKIP  $machine services (VM state=$state)"
    return
  fi
  for unit in "$@"; do
    stop_unit "$machine" "$unit"
  done
}

vm_records=$(vm_state_records)
path_b_state=$(awk -F'|' '$1 == "path-b" {print $2}' <<<"$vm_records")
path_a_state=$(awk -F'|' '$1 == "path-a" {print $2}' <<<"$vm_records")
core_state=$(awk -F'|' '$1 == "core" {print $2}' <<<"$vm_records")

stop_machine_units path-b "$path_b_state" ue6 ue5 ue4 gnb-b nwdaf-b upf-b
stop_machine_units path-a "$path_a_state" ue3 ue2 ue1 gnb-a nwdaf-a upf-a
stop_machine_units core "$core_state" nwdaf-c adrf smf amf pcf ausf udm udr nssf nrf mongodb
echo "Experiment services stopped; VM power state was not changed."
