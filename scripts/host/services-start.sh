#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$config_dir"
hash=$(config_hash "$config_dir")
echo "EFFECTIVE testbed=$testbed config=$config_dir hash=$hash"

rollback() {
  local status=$?
  trap - EXIT
  echo "service startup failed; stopping the experiment stack" >&2
  "$HOST_ROOT/scripts/host/services-stop.sh" || true
  exit "$status"
}
trap rollback EXIT
stage_config_all "$config_dir" "$hash"
"$HOST_ROOT/scripts/host/dataset-stage.sh" apply "$testbed" "$config_dir"

# Staging can overlap a base-box unattended-upgrade on an older VM.  Reconcile
# the real interface state immediately before any NF binds its topology address.
for machine in "${MACHINES[@]}"; do
  echo "NETWORK $machine"
  vssh "$machine" "sudo systemctl restart 5g-nwdaf-network.service"
done

start_unit core mongodb
start_unit core nrf
for unit in nssf udr udm ausf pcf amf; do start_unit core "$unit"; done
"$HOST_ROOT/scripts/host/subscriber-data.sh" apply "$testbed" "$config_dir"
start_unit path-a upf-a
start_unit path-b upf-b
start_unit core smf
for unit in adrf nwdaf-c; do start_unit core "$unit"; done
start_unit path-a nwdaf-a
start_unit path-b nwdaf-b
start_unit path-a gnb-a
start_unit path-b gnb-b
for unit in ue1 ue2 ue3; do start_unit path-a "$unit"; done
for unit in ue4 ue5 ue6; do start_unit path-b "$unit"; done
trap - EXIT
echo "Guest experiment services are active; Host ML containers and subscriptions have not been started."
