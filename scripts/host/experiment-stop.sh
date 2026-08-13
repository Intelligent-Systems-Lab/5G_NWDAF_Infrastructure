#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

cleanup_grace_seconds=40

if consumer_unit_active; then
  "$HOST_ROOT/scripts/host/subscriptions-stop.sh"
  echo "Waiting ${cleanup_grace_seconds}s for asynchronous ML resource cleanup before stopping containers."
  sleep "$cleanup_grace_seconds"
else
  echo "Consumer is not active; no subscriptions were changed."
fi
"$HOST_ROOT/scripts/host/webconsole-stop.sh"
"$HOST_ROOT/scripts/host/ml-stop.sh"
"$HOST_ROOT/scripts/host/services-stop.sh"

echo "Experiment processes stopped; VMs, datasets, databases, artifacts, containers, images, and volumes were retained."
