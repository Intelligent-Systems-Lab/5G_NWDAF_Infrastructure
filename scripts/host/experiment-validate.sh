#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
check_args=(--testbed "$testbed")
if [ -n "$explicit_config" ]; then
  check_args+=(--config-dir "$explicit_config")
fi

"$HOST_ROOT/scripts/host/preflight.sh" "$testbed" "$explicit_config"
export ML_DEVICE_POLICY
ML_DEVICE_POLICY=$(config_ml_device_policy "$config_dir")
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" "${check_args[@]}"
ml_runtime_gate
(cd "$HOST_ROOT" && TESTBED="$testbed" provider_vagrant validate)

echo "Experiment inputs and Host prerequisites are valid; no runtime state was changed."
