#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

export ML_RUNTIME_MODE=cpu-smoke
export ML_PROJECT_NAME=5g-nwdaf-infrastructure-lifecycle-smoke
config_dir="$HOST_ROOT/config/generated/ml-lifecycle-smoke"

cleanup() {
  ml_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$config_dir"
}

on_error() {
  status=$?
  echo "ML lifecycle smoke failed; recent project logs follow" >&2
  "$HOST_ROOT/scripts/host/logs.sh" --source ml --since "10 minutes ago" \
    --tail 80 --no-follow >&2 || true
  cleanup
  exit "$status"
}

trap on_error ERR INT TERM
cleanup
python3 "$HOST_ROOT/scripts/host/ml-smoke-config.py" --output "$config_dir"
"$HOST_ROOT/scripts/host/ml-start.sh" "$HOST_ROOT/testbed.yaml" "$config_dir"
"$HOST_ROOT/scripts/host/ml-status.sh"
"$HOST_ROOT/scripts/host/logs.sh" --source ml --service pyanlf-a \
  --since "5 minutes ago" --tail 10 --no-follow

for service in "${ML_SERVICES[@]}"; do
  runtime_uid=$(ml_compose exec -T "$service" id -u)
  if [ "$runtime_uid" != 10001 ]; then
    echo "$service runs as unexpected UID $runtime_uid" >&2
    false
  fi
  ml_compose exec -T "$service" python -c \
    'import torch; print("uid=10001 torch=" + torch.__version__ + " cuda_available=" + str(torch.cuda.is_available()))'
done

for service in pymtlf-a pymtlf-b; do
  ml_compose exec -T "$service" python -c \
    'from py_mtlf.config import load_settings; print("training_device=" + load_settings("/etc/5g-nwdaf/config.yaml").federated_learning.client.training.device)'
done

mapfile -t running_containers < <(ml_compose ps -q)
if [ "${#running_containers[@]}" -ne 5 ]; then
  echo "expected five smoke containers, got ${#running_containers[@]}" >&2
  false
fi
docker stats --no-stream --format \
  'container={{.Name}} memory={{.MemUsage}} cpu={{.CPUPerc}} pids={{.PIDs}}' \
  "${running_containers[@]}"

"$HOST_ROOT/scripts/host/ml-stop.sh"

if docker ps -q --filter "label=com.docker.compose.project=$ML_PROJECT_NAME" | read -r _; then
  echo "lifecycle smoke containers are still running after ml-stop" >&2
  false
fi
mapfile -t retained_containers < <(
  docker ps -aq --filter "label=com.docker.compose.project=$ML_PROJECT_NAME"
)
mapfile -t retained_volumes < <(
  docker volume ls -q --filter "label=com.docker.compose.project=$ML_PROJECT_NAME"
)
if [ "${#retained_containers[@]}" -ne 5 ] || [ "${#retained_volumes[@]}" -ne 5 ]; then
  echo "ml-stop did not retain the expected five containers and five volumes" >&2
  false
fi
"$HOST_ROOT/scripts/host/ml-status.sh"

trap - ERR INT TERM
cleanup
echo "ML container test passed; disposable containers and volumes were removed, images were retained."
