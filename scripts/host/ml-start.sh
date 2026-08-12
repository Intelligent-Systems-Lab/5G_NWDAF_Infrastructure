#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
mode=$(ml_runtime_mode)
project=$(ml_project_name)
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
hash=$(config_hash "$config_dir")
config_name=$(basename "$config_dir")

check_args=(--testbed "$testbed" --config-dir "$config_dir")
if [ "$mode" = cpu-smoke ]; then
  bind_address=127.0.0.1
else
  bind_address=$(effective_ml_bind_address "$testbed")
fi

python3 "$HOST_ROOT/scripts/host/config-check.py" "${check_args[@]}"
device_policy=$(config_ml_device_policy "$config_dir")
export ML_DEVICE_POLICY="$device_policy"
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" \
  --testbed "$testbed" --config-dir "$config_dir" --mode "$mode"

if ! host_has_address "$bind_address"; then
  echo "ML bind address is not present on the Host: $bind_address" >&2
  exit 1
fi
ml_host_resource_gate "$testbed"

ml_runtime_gate

export CONFIG_DIR="$config_dir"
export CONFIG_SET_NAME="$config_name"
export CONFIG_HASH="$hash"
export ML_BIND_ADDRESS="$bind_address"

echo "ML CONFIG project=$project mode=$mode device_policy=$device_policy set=$config_name hash=$hash bind=$bind_address"
ml_compose build pyanlf-a pymtlf-a

if [ "$device_policy" = gpu ]; then
  cdi_device=nvidia.com/gpu=all
  echo "GPU PROBE device=$cdi_device image=5g-nwdaf-infrastructure/pymtlf:local"
  docker run --rm --runtime nvidia \
    --env "NVIDIA_VISIBLE_DEVICES=$cdi_device" \
    --env NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    --label "io.5g-nwdaf.project=$project" \
    --entrypoint python \
    5g-nwdaf-infrastructure/pymtlf:local \
    -c 'import torch; assert torch.cuda.is_available(), "CUDA is not visible"; print("cuda_device=" + torch.cuda.get_device_name(0))'
fi

rollback_needed=false
rollback() {
  status=${1:-$?}
  if $rollback_needed; then
    echo "ML startup failed; stopping containers in project $project" >&2
    ml_compose stop --timeout 30 >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM
rollback_needed=true
ml_compose up --detach --no-build --wait --wait-timeout 240
"$HOST_ROOT/scripts/host/ml-status.sh"
rollback_needed=false
trap - ERR INT TERM

echo "Host ML services are active; VM services and subscriptions were not changed."
