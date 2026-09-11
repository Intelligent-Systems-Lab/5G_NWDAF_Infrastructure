#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

cleanup_grace_seconds=40
testbed=${1:?usage: experiment-stop.sh testbed [config-dir]}
explicit_config=${2:-}
select_testbed_machines "$testbed"
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
subscriptions_mode=$(config_subscriptions_mode "$config_dir")
assert_selected_provider_running
assert_guest_runtime_identity "$config_dir"

if [ "$subscriptions_mode" = none ]; then
  echo "Subscriptions are disabled by the selected config; no subscription resources were changed."
elif consumer_unit_active; then
  "$HOST_ROOT/scripts/host/subscriptions-stop.sh"
  echo "Waiting ${cleanup_grace_seconds}s for asynchronous ML resource cleanup before stopping containers."
  sleep "$cleanup_grace_seconds"
else
  echo "Consumer is not active; no subscriptions were changed."
fi
if [ "$(config_webconsole_enabled "$config_dir")" = true ]; then
  "$HOST_ROOT/scripts/host/webconsole-stop.sh"
fi
"$HOST_ROOT/scripts/host/ml-stop.sh" "$testbed" "$explicit_config"
"$HOST_ROOT/scripts/host/services-stop.sh" "$testbed" "$explicit_config"

echo "Experiment processes stopped; VMs, datasets, databases, artifacts, containers, images, and volumes were retained."
