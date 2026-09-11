#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: experiment-status.sh testbed [config-dir]}
explicit_config=${2:-}
select_testbed_machines "$testbed"
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
read -r scenario policy < <(
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import load_yaml, resolve_path

directory = resolve_path(sys.argv[1])
manifest = load_yaml(directory / "manifest.yaml")
print(manifest["scenario"]["name"], manifest["runtime"]["mlDevicePolicy"])
PY
)
available_mib=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
free_gib=$(df -Pk "$HOST_ROOT" | awk 'NR==2 {print int($4/1024/1024)}')

echo "EXPERIMENT scenario=$scenario config=$config_dir hash=$(config_hash "$config_dir")"
echo "ML DEVICE policy=$policy"
PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import load_runtime_manifest, load_yaml, resolve_path
directory = resolve_path(sys.argv[1])
for service in load_runtime_manifest(directory)["runtime"]["hostContainers"]:
    config = load_yaml(directory / (service + ".yaml"))
    if service.startswith("pyanlf-"):
        device = config.get("model", {}).get("device", "cpu")
    else:
        client = config.get("federated_learning", {}).get("client")
        device = client.get("training", {}).get("device", "cpu") if client else "cpu"
    print("  {}={}".format(service, device))
PY
echo "HOST available_ram=${available_mib}MiB workspace_free=${free_gib}GiB"
echo
failures=0
"$HOST_ROOT/scripts/host/observe.sh" --once "$testbed" "$explicit_config" || failures=$((failures + 1))

if [ "$(config_deployment_kind "$config_dir")" = protocol-hierarchical ]; then
  echo
  "$HOST_ROOT/scripts/host/registration-check.sh" --once "$testbed" "$config_dir" || failures=$((failures + 1))
  echo
  "$HOST_ROOT/scripts/host/backend-check.sh" "$testbed" "$config_dir" || failures=$((failures + 1))
  echo
  if [ -n "${RUN_ID:-}" ]; then
    training_args=(
      training-status --testbed "$testbed" --config-dir "$config_dir"
      --run-id "$RUN_ID"
    )
    if [ -n "${MODEL_FAMILY_ID:-}" ]; then
      training_args+=(--model-family-id "$MODEL_FAMILY_ID")
    fi
    python3 "$HOST_ROOT/scripts/host/fl-control.py" "${training_args[@]}" || failures=$((failures + 1))
  else
    echo "TRAINING state=not-selected reason=RUN_ID-not-provided"
  fi
fi

[ "$failures" -eq 0 ]
