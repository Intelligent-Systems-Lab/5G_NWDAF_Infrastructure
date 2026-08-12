#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
read -r scenario policy anlf_a anlf_b mtlf_a mtlf_b mtlf_c < <(
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import load_yaml, resolve_path

directory = resolve_path(sys.argv[1])
manifest = load_yaml(directory / "manifest.yaml")
devices = [
    load_yaml(directory / ("pyanlf-" + name + ".yaml"))["model"]["device"]
    for name in ("a", "b")
]
devices.extend(
    load_yaml(directory / ("pymtlf-" + name + ".yaml"))["federated_learning"]
    ["client"]["training"]["device"]
    for name in ("a", "b")
)
devices.append("cpu")
print(manifest["scenario"]["name"], manifest["runtime"]["mlDevicePolicy"], *devices)
PY
)
available_mib=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
free_gib=$(df -Pk "$HOST_ROOT" | awk 'NR==2 {print int($4/1024/1024)}')

echo "EXPERIMENT scenario=$scenario config=$config_dir hash=$(config_hash "$config_dir")"
echo "ML DEVICE policy=$policy pyanlf-a=$anlf_a pyanlf-b=$anlf_b pymtlf-a=$mtlf_a pymtlf-b=$mtlf_b pymtlf-c=$mtlf_c"
echo "HOST available_ram=${available_mib}MiB workspace_free=${free_gib}GiB"
echo
"$HOST_ROOT/scripts/host/observe.sh" --once
