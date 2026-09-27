#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: experiment-stop.sh testbed [config-dir]}
explicit_config=${2:-}
select_testbed_machines "$testbed"
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
assert_selected_provider_running
assert_guest_runtime_identity "$config_dir"

"$HOST_ROOT/scripts/host/ml-stop.sh" "$testbed" "$explicit_config"
"$HOST_ROOT/scripts/host/services-stop.sh" "$testbed" "$explicit_config"

echo "Experiment processes stopped; VMs, datasets, databases, artifacts, containers, images, and volumes were retained."
