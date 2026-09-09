#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: services-start.sh testbed [config-dir]}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
hash=$(config_hash "$config_dir")
echo "EFFECTIVE testbed=$testbed config=$config_dir hash=$hash"

rollback() {
  local status=$?
  trap - EXIT
  echo "service startup failed; stopping the experiment stack" >&2
  "$HOST_ROOT/scripts/host/services-stop.sh" "$testbed" "$explicit_config" || true
  exit "$status"
}
trap rollback EXIT
"$HOST_ROOT/scripts/host/guest-tools-sync.sh"
stage_config_all "$config_dir" "$hash"
assert_guest_runtime_identity "$config_dir"
"$HOST_ROOT/scripts/host/dataset-stage.sh" apply "$testbed" "$config_dir"

# Persistent Netplan aliases should already match the active config.  Reconcile
# only migration or runtime drift before any NF binds its topology address.
for machine in "${MACHINES[@]}"; do
  echo "NETWORK VERIFY $machine"
  if ! vssh "$machine" "sudo /usr/local/libexec/5g-nwdaf-infrastructure/network-setup --verify"; then
    echo "NETWORK RECONCILE $machine"
    vssh "$machine" "sudo systemctl restart 5g-nwdaf-network.service"
  fi
done

"$HOST_ROOT/scripts/host/gtp5g-preflight.sh"

subscriber_data_applied=false
service_records=$(config_guest_service_records "$config_dir")
[ -n "$service_records" ] || { echo "selected Guest service inventory is empty" >&2; exit 1; }
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
trap - EXIT
echo "Guest experiment services are active; Host ML containers and subscriptions have not been started."
