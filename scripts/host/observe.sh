#!/usr/bin/env bash
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

OBSERVE_ACTIVE_PIDS=()
OBSERVE_ACTIVE_DIR=''
OBSERVE_CACHE_DIR=''

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

observe_cleanup() {
  if [ "${#OBSERVE_ACTIVE_PIDS[@]}" -gt 0 ]; then
    kill "${OBSERVE_ACTIVE_PIDS[@]}" >/dev/null 2>&1 || true
    wait "${OBSERVE_ACTIVE_PIDS[@]}" >/dev/null 2>&1 || true
    OBSERVE_ACTIVE_PIDS=()
  fi
  if [ -n "$OBSERVE_ACTIVE_DIR" ] && [ -d "$OBSERVE_ACTIVE_DIR" ]; then
    rm -rf -- "$OBSERVE_ACTIVE_DIR"
  fi
  OBSERVE_ACTIVE_DIR=''
  if [ -n "$OBSERVE_CACHE_DIR" ] && [ -d "$OBSERVE_CACHE_DIR" ]; then
    rm -rf -- "$OBSERVE_CACHE_DIR"
  fi
  OBSERVE_CACHE_DIR=''
}

observe_collect_vm_records() {
  local timeout_seconds=$1
  VM_STATE_RECORDS_FILE='' timeout --foreground "$timeout_seconds" bash -c '
    source "$1"
    unset VM_STATE_RECORDS_FILE
    vm_state_records
  ' observe-vm-state "$HOST_ROOT/scripts/host/lib.sh"
}

observe_start_section() {
  local label=$1 output_file=$2 timeout_seconds=$3
  shift 3
  (
    observe_section "$label" timeout --foreground "$timeout_seconds" "$@"
  ) >"$output_file" 2>&1 &
  OBSERVE_ACTIVE_PIDS+=("$!")
}

observe_wait_sections() {
  local failures=0 pid
  for pid in "${OBSERVE_ACTIVE_PIDS[@]}"; do
    wait "$pid" || failures=$((failures + 1))
  done
  OBSERVE_ACTIVE_PIDS=()
  [ "$failures" -eq 0 ]
}

observe_snapshot() {
  local timeout_seconds=${OBSERVE_SECTION_TIMEOUT:-30}
  local failures=0 vm_records_file vm_error_file section
  [[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || {
    echo "OBSERVE_SECTION_TIMEOUT must be a positive integer" >&2
    return 2
  }

  OBSERVE_ACTIVE_DIR=$(mktemp -d -t 5g-nwdaf-observe-snapshot.XXXXXX)
  vm_records_file="$OBSERVE_ACTIVE_DIR/vm-records"
  vm_error_file="$OBSERVE_ACTIVE_DIR/vm-error"

  unset VM_STATE_RECORDS_FILE
  if observe_collect_vm_records "$timeout_seconds" >"$vm_records_file" 2>"$vm_error_file"; then
    export VM_STATE_RECORDS_FILE="$vm_records_file"
    observe_section VM vm_status_summary >"$OBSERVE_ACTIVE_DIR/vm" 2>&1 || failures=$((failures + 1))
    observe_start_section SERVICE "$OBSERVE_ACTIVE_DIR/service" "$timeout_seconds" \
      "$HOST_ROOT/scripts/host/services-status.sh"
    observe_start_section WEBCONSOLE "$OBSERVE_ACTIVE_DIR/webconsole" "$timeout_seconds" \
      "$HOST_ROOT/scripts/host/webconsole-status.sh"
    observe_start_section SUBSCRIPTION "$OBSERVE_ACTIVE_DIR/subscription" "$timeout_seconds" \
      "$HOST_ROOT/scripts/host/subscriptions-status.sh"
  else
    cat "$vm_error_file" >"$OBSERVE_ACTIVE_DIR/vm"
    printf '%s\n' 'VM status unavailable' >>"$OBSERVE_ACTIVE_DIR/vm"
    for section in service webconsole subscription; do
      printf 'VM state unavailable\n%s status unavailable\n' "${section^^}" \
        >"$OBSERVE_ACTIVE_DIR/$section"
    done
    failures=$((failures + 4))
  fi

  export ML_STATUS_CACHE_DIR="$OBSERVE_CACHE_DIR/ml"
  observe_start_section ML "$OBSERVE_ACTIVE_DIR/ml" "$timeout_seconds" \
    "$HOST_ROOT/scripts/host/ml-status.sh"
  unset ML_STATUS_CACHE_DIR

  observe_wait_sections || failures=$((failures + 1))

  for section in vm service webconsole ml subscription; do
    [ "$section" = vm ] || echo
    cat "$OBSERVE_ACTIVE_DIR/$section"
  done

  unset VM_STATE_RECORDS_FILE
  rm -rf -- "$OBSERVE_ACTIVE_DIR"
  OBSERVE_ACTIVE_DIR=''
  [ "$failures" -eq 0 ]
}

observe_main() {
  local interval=${OBSERVE_INTERVAL:-5}
  local once=false
  local snapshot_file snapshot_started snapshot_completed started_ns completed_ns elapsed_ms snapshot_status
  [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
    echo "OBSERVE_INTERVAL must be a non-negative number" >&2
    return 2
  }
  [ "${1:-}" = "--once" ] && once=true
  OBSERVE_CACHE_DIR=$(mktemp -d -t 5g-nwdaf-observe-cache.XXXXXX)
  snapshot_file="$OBSERVE_CACHE_DIR/snapshot"
  trap 'observe_cleanup; exit 130' INT TERM
  trap observe_cleanup EXIT
  while :; do
    snapshot_started=$(date --iso-8601=seconds)
    started_ns=$(date +%s%N)
    if observe_snapshot >"$snapshot_file" 2>&1; then
      snapshot_status=0
    else
      snapshot_status=$?
    fi
    completed_ns=$(date +%s%N)
    snapshot_completed=$(date --iso-8601=seconds)
    elapsed_ms=$(( (completed_ns - started_ns) / 1000000 ))
    if ! $once && [ -t 1 ]; then
      printf '\033[H\033[2J'
    fi
    printf 'SNAPSHOT started=%s completed=%s collection=%dms\n\n' \
      "$snapshot_started" "$snapshot_completed" "$elapsed_ms"
    cat "$snapshot_file"
    printf '\n'
    if $once; then
      return "$snapshot_status"
    fi
    sleep "$interval"
  done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  observe_main "$@"
fi
