#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=$(ml_project_name)
testbed=${1:-testbed.yaml}
explicit_config=${2:-}
"$HOST_ROOT/scripts/host/ml-status.sh" "$testbed" "$explicit_config"
container_lines=$(
  docker ps -q --filter "label=com.docker.compose.project=$project"
)
container_ids=()
if [ -n "$container_lines" ]; then
  mapfile -t container_ids <<<"$container_lines"
fi
if [ "${#container_ids[@]}" -eq 0 ]; then
  echo "ML project=$project has no running containers."
else
  echo "Stopping ${#container_ids[@]} ML containers in project $project"
  docker stop --time 30 "${container_ids[@]}" >/dev/null
fi
wait_no_running_ml_containers "$project"
"$HOST_ROOT/scripts/host/ml-status.sh" "$testbed" "$explicit_config"
echo "Host ML services stopped; containers, volumes, and images were retained."
echo "ML stop did not modify VM or subscription state."
