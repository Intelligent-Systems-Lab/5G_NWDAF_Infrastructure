#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=$(ml_project_name)
mapfile -t container_ids < <(
  docker ps -q --filter "label=com.docker.compose.project=$project"
)
if [ "${#container_ids[@]}" -eq 0 ]; then
  echo "ML project=$project has no running containers."
else
  echo "Stopping ${#container_ids[@]} ML containers in project $project"
  docker stop --time 30 "${container_ids[@]}" >/dev/null
fi
"$HOST_ROOT/scripts/host/ml-status.sh"
echo "Host ML services stopped; containers, volumes, images, VMs, and subscriptions were retained."
