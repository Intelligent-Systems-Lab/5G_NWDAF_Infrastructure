#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=$(ml_project_name)
mapfile -t coordinator_ids < <(
  docker ps -q \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=pymtlf-c"
)
if [ "${#coordinator_ids[@]}" -gt 0 ]; then
  echo "Stopping PyMTLF-C before its downstream ML containers"
  docker stop --time 30 "${coordinator_ids[@]}" >/dev/null
fi

mapfile -t remaining_ids < <(
  docker ps -q --filter "label=com.docker.compose.project=$project"
)
if [ "${#remaining_ids[@]}" -eq 0 ] && [ "${#coordinator_ids[@]}" -eq 0 ]; then
  echo "ML project=$project has no running containers."
elif [ "${#remaining_ids[@]}" -gt 0 ]; then
  echo "Stopping ${#remaining_ids[@]} remaining ML containers in project $project"
  docker stop --time 30 "${remaining_ids[@]}" >/dev/null
fi
"$HOST_ROOT/scripts/host/ml-status.sh"
echo "Host ML services stopped; containers, volumes, images, VMs, and subscriptions were retained."
