#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

interval=${OBSERVE_INTERVAL:-5}
once=false
[ "${1:-}" = "--once" ] && once=true

while :; do
  if ! $once; then
    command -v clear >/dev/null && clear || true
  fi
  date --iso-8601=seconds
  echo
  (cd "$HOST_ROOT" && vagrant status --machine-readable 2>/dev/null | awk -F, '$3=="state" {printf "%-8s vm=%s\n", $2, $4}') || true
  echo
  "$HOST_ROOT/scripts/host/services-status.sh" 2>/dev/null || echo "service status unavailable"
  echo
  "$HOST_ROOT/scripts/host/ml-status.sh" 2>/dev/null || echo "ML container status unavailable"
  if [ -x "$HOST_ROOT/scripts/host/subscriptions-status.sh" ]; then
    echo
    "$HOST_ROOT/scripts/host/subscriptions-status.sh" 2>/dev/null || echo "subscription status unavailable"
  fi
  $once && break
  sleep "$interval"
done
