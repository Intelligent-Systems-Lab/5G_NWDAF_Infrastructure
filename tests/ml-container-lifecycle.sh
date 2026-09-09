#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/host/lib.sh"

export ML_RUNTIME_MODE=cpu-smoke
export ML_DEVICE_POLICY=cpu
export ML_PROJECT_NAME=5g-nwdaf-infrastructure-container-test
config_dir=
config_dirs=(
  "$HOST_ROOT/.generated/tests/config/ml-container-test-flat"
  "$HOST_ROOT/.generated/tests/config/ml-container-test-hfl"
)

cleanup() {
  local directory
  for directory in "${config_dirs[@]}"; do
    if [ -f "$directory/compose.yaml" ]; then
      CONFIG_DIR="$directory" ml_compose down --volumes --remove-orphans \
        >/dev/null 2>&1 || true
    fi
    rm -rf "$directory"
    rm -f "$directory.cpu-smoke.yaml"
  done
}

on_error() {
  status=$?
  echo "ML container test failed; recent project logs follow" >&2
  "$HOST_ROOT/scripts/host/logs.sh" --source ml --since "10 minutes ago" \
    --tail 80 --no-follow >&2 || true
  cleanup
  exit "$status"
}

trap on_error ERR INT TERM
cleanup

run_case() {
  local label=$1 testbed=$2 source=$3 expected=$4
  local service runtime_uid coordinator
  local -a services pymtlf_services running_containers retained_containers retained_volumes
  config_dir="$HOST_ROOT/.generated/tests/config/ml-container-test-$label"
  python3 "$HOST_ROOT/tests/support/ml-cpu-config.py" \
    --source "$source" --output "$config_dir"
  export CONFIG_DIR="$config_dir"
  mapfile -t services < <(config_host_containers "$config_dir")
  mapfile -t pymtlf_services < <(printf '%s\n' "${services[@]}" | grep '^pymtlf-')
  coordinator=$(config_coordinator_container "$config_dir")

  "$HOST_ROOT/scripts/host/ml-start.sh" "$testbed" "$config_dir"
  "$HOST_ROOT/scripts/host/ml-status.sh" "$testbed" "$config_dir"
  "$HOST_ROOT/scripts/host/logs.sh" --source ml --service "${services[0]}" \
    --since "5 minutes ago" --tail 10 --no-follow \
    --testbed "$testbed" --config-dir "$config_dir"

  for service in "${services[@]}"; do
    runtime_uid=$(ml_compose exec -T "$service" id -u)
    if [ "$runtime_uid" != 10001 ]; then
      echo "$service runs as unexpected UID $runtime_uid" >&2
      false
    fi
    ml_compose exec -T "$service" python -c \
      'import torch; print("uid=10001 torch=" + torch.__version__ + " cuda_available=" + str(torch.cuda.is_available()))'
  done

  for service in "${pymtlf_services[@]}"; do
    ml_compose exec -T "$service" python /opt/app/pymtlf-smoke-health.py
  done
  ml_compose exec -T "$coordinator" python -c \
    'from py_mtlf.config import load_settings; s=load_settings("/etc/5g-nwdaf/config.yaml"); print("orchestration=" + s.federated_learning.orchestration.mode + " participant_source=" + s.federated_learning.orchestration.participant_source)'

  mapfile -t running_containers < <(ml_compose ps -q)
  if [ "${#running_containers[@]}" -ne "$expected" ]; then
    echo "expected $expected $label test containers, got ${#running_containers[@]}" >&2
    false
  fi
  docker stats --no-stream --format \
    'container={{.Name}} memory={{.MemUsage}} cpu={{.CPUPerc}} pids={{.PIDs}}' \
    "${running_containers[@]}"

  "$HOST_ROOT/scripts/host/ml-stop.sh" "$testbed" "$config_dir"
  if docker ps -q --filter "label=com.docker.compose.project=$ML_PROJECT_NAME" | read -r _; then
    echo "$label test containers are still running after ml-stop" >&2
    false
  fi
  mapfile -t retained_containers < <(
    docker ps -aq --filter "label=com.docker.compose.project=$ML_PROJECT_NAME"
  )
  mapfile -t retained_volumes < <(
    docker volume ls -q --filter "label=com.docker.compose.project=$ML_PROJECT_NAME"
  )
  if [ "${#retained_containers[@]}" -ne "$expected" ] || \
    [ "${#retained_volumes[@]}" -ne "$expected" ]; then
    echo "ml-stop did not retain the expected $expected $label containers and volumes" >&2
    false
  fi
  "$HOST_ROOT/scripts/host/ml-status.sh" "$testbed" "$config_dir"
  ml_compose down --volumes --remove-orphans >/dev/null
  rm -rf "$config_dir"
  rm -f "$config_dir.cpu-smoke.yaml"
  config_dir=
}

run_case flat "$HOST_ROOT/testbed.yaml" "$HOST_ROOT/config/default" 5
run_case hfl "$HOST_ROOT/testbed.static-hierarchical.yaml" \
  "$HOST_ROOT/config/local/static-hierarchical-container-test" 7

trap - ERR INT TERM
cleanup
echo "ML container tests passed; disposable Flat/HFL containers and volumes were removed, images were retained."
