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
echo "ML lifecycle smoke passed; disposable containers and volumes were removed, images were retained."
