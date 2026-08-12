#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

cleanup_grace=${ML_CLEANUP_GRACE_SECONDS:-40}
if ! [[ "$cleanup_grace" =~ ^[0-9]+$ ]]; then
  echo "ML_CLEANUP_GRACE_SECONDS must be a non-negative integer (got: $cleanup_grace)." >&2
  exit 2
fi

consumer=$(vssh core "systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
if [ "$consumer" = active ]; then
  "$HOST_ROOT/scripts/host/subscriptions-stop.sh"
  if [ "$cleanup_grace" -gt 0 ]; then
    echo "Waiting ${cleanup_grace}s for asynchronous ML resource cleanup before stopping containers."
    sleep "$cleanup_grace"
  fi
else
  echo "Consumer is not active; no subscriptions were changed."
fi
"$HOST_ROOT/scripts/host/webconsole-stop.sh"
"$HOST_ROOT/scripts/host/ml-stop.sh"
"$HOST_ROOT/scripts/host/services-stop.sh"

echo "Experiment processes stopped; VMs, datasets, databases, artifacts, containers, images, and volumes were retained."
