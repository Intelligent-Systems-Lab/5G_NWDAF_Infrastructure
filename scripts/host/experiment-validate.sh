#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
check_args=(--testbed "$testbed")
if [ -n "$explicit_config" ]; then
  check_args+=(--config-dir "$explicit_config")
fi

"$HOST_ROOT/scripts/host/preflight.sh" "$testbed" "$explicit_config"
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" "${check_args[@]}"
ml_runtime_gate
(cd "$HOST_ROOT" && TESTBED="$testbed" vagrant validate)

echo "Experiment inputs and Host prerequisites are valid; no runtime state was changed."
