#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

consumer=$(vssh core "systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
if [ "$consumer" = active ]; then
  "$HOST_ROOT/scripts/host/subscriptions-stop.sh"
else
  echo "Consumer is not active; no subscriptions were changed."
fi
"$HOST_ROOT/scripts/host/ml-stop.sh"
"$HOST_ROOT/scripts/host/services-stop.sh"

echo "Experiment processes stopped; VMs, datasets, databases, artifacts, containers, images, and volumes were retained."
