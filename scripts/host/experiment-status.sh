#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
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
"$HOST_ROOT/scripts/host/observe.sh" --once "$testbed" "$explicit_config"
