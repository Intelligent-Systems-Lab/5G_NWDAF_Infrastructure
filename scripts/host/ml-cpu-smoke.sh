#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=5g-nwdaf-infrastructure-smoke
config_dir="$HOST_ROOT/config/generated/ml-cpu-smoke"
compose=(docker compose -p "$project" -f "$HOST_ROOT/compose.yaml" -f "$HOST_ROOT/compose.cpu-smoke.yaml")

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$config_dir"
}

on_error() {
  status=$?
  echo "CPU ML smoke failed; recent project logs follow" >&2
  "${compose[@]}" logs --no-color --tail 80 >&2 || true
  cleanup
  exit "$status"
}

trap on_error ERR INT TERM
python3 "$HOST_ROOT/scripts/host/ml-smoke-config.py" --force --output "$config_dir"
python3 "$HOST_ROOT/scripts/host/config-check.py" \
  --testbed "$HOST_ROOT/testbed.yaml" \
  --config-dir "$config_dir" \
  --ml-device-override cpu

export CONFIG_DIR="$config_dir"
export CONFIG_SET_NAME=ml-cpu-smoke
export ML_BIND_ADDRESS=127.0.0.1
"${compose[@]}" config --quiet
# The remaining services reuse these two image tags, so build each target once
# instead of asking Compose to schedule five equivalent builds in parallel.
"${compose[@]}" build pyanlf-a pymtlf-a
"${compose[@]}" up --detach --no-build --wait --wait-timeout 240
"${compose[@]}" ps

for service in pyanlf-a pyanlf-b pymtlf-a pymtlf-b pymtlf-c; do
  runtime_uid=$("${compose[@]}" exec -T "$service" id -u)
  if [ "$runtime_uid" != 10001 ]; then
    echo "$service runs as unexpected UID $runtime_uid" >&2
    false
  fi
  "${compose[@]}" exec -T "$service" python -c \
    'import torch; print("uid=10001 torch=" + torch.__version__ + " cuda_available=" + str(torch.cuda.is_available()))'
done

for service in pymtlf-a pymtlf-b; do
  "${compose[@]}" exec -T "$service" python -c \
    'from py_mtlf.config import load_settings; print("training_device=" + load_settings("/etc/5g-nwdaf/config.yaml").federated_learning.client.training.device)'
done

mapfile -t container_ids < <("${compose[@]}" ps -q)
if [ "${#container_ids[@]}" -ne 5 ]; then
  echo "expected five smoke containers, got ${#container_ids[@]}" >&2
  false
fi
docker stats --no-stream --format \
  'container={{.Name}} memory={{.MemUsage}} cpu={{.CPUPerc}} pids={{.PIDs}}' \
  "${container_ids[@]}"

trap - ERR INT TERM
cleanup
echo "CPU ML smoke passed; smoke containers and volumes were removed, images were retained."
