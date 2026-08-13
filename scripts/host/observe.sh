#!/usr/bin/env bash
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

observe_section() {
  local label=$1; shift
  local output
  if output=$("$@" 2>&1); then
    printf '%s\n' "$output"
    return 0
  fi
  [ -z "$output" ] || printf '%s\n' "$output"
  printf '%s status unavailable\n' "$label" >&2
  return 1
}

vm_status_summary() {
  local machine state records
  records=$(vm_state_records) || return
  while IFS='|' read -r machine state; do
    printf '%-8s vm=%s\n' "$machine" "$state"
  done <<<"$records"
}

observe_snapshot() {
  local failures=0
  date --iso-8601=seconds
  echo
  observe_section VM vm_status_summary || failures=$((failures + 1))
  echo
  observe_section SERVICE "$HOST_ROOT/scripts/host/services-status.sh" || failures=$((failures + 1))
  echo
  observe_section WEBCONSOLE "$HOST_ROOT/scripts/host/webconsole-status.sh" || failures=$((failures + 1))
  echo
  observe_section ML "$HOST_ROOT/scripts/host/ml-status.sh" || failures=$((failures + 1))
  echo
  observe_section SUBSCRIPTION "$HOST_ROOT/scripts/host/subscriptions-status.sh" || failures=$((failures + 1))
  [ "$failures" -eq 0 ]
}

observe_main() {
  local interval=${OBSERVE_INTERVAL:-5}
  local once=false
  [ "${1:-}" = "--once" ] && once=true
  while :; do
    if ! $once; then
      command -v clear >/dev/null && clear || true
    fi
    if ! observe_snapshot; then
      $once && return 1
    fi
    $once && return 0
    sleep "$interval"
  done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  observe_main "$@"
fi
