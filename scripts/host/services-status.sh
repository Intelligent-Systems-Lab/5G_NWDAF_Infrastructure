#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SERVICE_STATUS_ACTIVE_DIR=''

services_status_cleanup() {
  if [ -n "$SERVICE_STATUS_ACTIVE_DIR" ] && [ -d "$SERVICE_STATUS_ACTIVE_DIR" ]; then
    rm -rf -- "$SERVICE_STATUS_ACTIVE_DIR"
  fi
  SERVICE_STATUS_ACTIVE_DIR=''
}

machine_snapshot() {
  local machine=$1; shift
  local units="$*"
  local remote_script
  remote_script="for unit in $units; do
    service=\"5g-nwdaf@\$unit.service\"
    state=\$(systemctl is-active \"\$service\" 2>/dev/null || true)
    state=\${state:-unknown}
    printf 'SERVICE|$machine|%s|%s\\n' \"\$unit\" \"\$state\"
    case \"\$unit\" in
      ue[0-9]*)
        invocation=''
        journal=''
        if [ \"\$state\" = active ]; then
          if ! invocation=\$(systemctl show \"\$service\" --property=InvocationID --value); then
            echo \"failed to read InvocationID for $machine/\$unit\" >&2
            exit 1
          fi
          if [ -z \"\$invocation\" ]; then
            echo \"active UE has no InvocationID: $machine/\$unit\" >&2
            exit 1
          fi
          if ! journal=\$(sudo journalctl -u \"\$service\" \"_SYSTEMD_INVOCATION_ID=\$invocation\" --no-pager -o cat); then
            echo \"failed to read current journal for $machine/\$unit\" >&2
            exit 1
          fi
        fi
        encoded=\$(printf '%s' \"\$journal\" | base64 -w0)
        printf 'UE|$machine|%s|%s|%s|%s\\n' \"\$unit\" \"\$state\" \"\$invocation\" \"\$encoded\"
        ;;
    esac
  done"
  vssh "$machine" "$remote_script"
}

services_status_main() {
  local machine snapshot line record unit state invocation encoded journal registration pdu_session vm_records snapshot_dir snapshot_failure
  local -a units=()
  local -a service_records=()
  local -a ue_records=()
  local -A vm_states=()
  local -A snapshot_pids=()
  local testbed=${1:?usage: services-status.sh testbed [config-dir]} explicit_config=${2:-} config_dir
  select_testbed_machines "$testbed" || return
  config_dir=$(effective_config_dir "$testbed" "$explicit_config")
  assert_guest_runtime_identity "$config_dir" || return
  vm_records=$(vm_state_records) || return
  while IFS='|' read -r machine state; do
    vm_states["$machine"]=$state
  done <<<"$vm_records"

  snapshot_dir=$(mktemp -d -t 5g-nwdaf-service-status.XXXXXX)
  SERVICE_STATUS_ACTIVE_DIR=$snapshot_dir
  for machine in "${MACHINES[@]}"; do
    unit_lines=$(config_guest_units "$config_dir" "$machine")
    [ -n "$unit_lines" ] || {
      echo "selected Guest service inventory is empty for $machine" >&2
      services_status_cleanup
      return 1
    }
    mapfile -t units <<<"$unit_lines"
    if [ "${vm_states[$machine]:-unknown}" != running ]; then
      for unit in "${units[@]}"; do
        service_records+=("SERVICE|$machine|$unit|not-running")
        case "$unit" in ue[0-9]*) ue_records+=("UE|$machine|$unit|not-running||");; esac
      done
      continue
    fi
    (
      machine_snapshot "$machine" "${units[@]}"
    ) >"$snapshot_dir/$machine" 2>"$snapshot_dir/$machine.error" &
    snapshot_pids["$machine"]=$!
  done

  snapshot_failure=0
  for machine in "${MACHINES[@]}"; do
    [ -n "${snapshot_pids[$machine]:-}" ] || continue
    if ! wait "${snapshot_pids[$machine]}"; then
      cat "$snapshot_dir/$machine.error" >&2
      snapshot_failure=1
    fi
  done
  if [ "$snapshot_failure" -ne 0 ]; then
    services_status_cleanup
    return 1
  fi

  for machine in "${MACHINES[@]}"; do
    [ -n "${snapshot_pids[$machine]:-}" ] || continue
    snapshot=$(<"$snapshot_dir/$machine")
    while IFS= read -r line; do
      line=${line%$'\r'}
      case "$line" in
        SERVICE\|*) service_records+=("$line");;
        UE\|*) ue_records+=("$line");;
        '') ;;
        *) printf '%s\n' "$line" >&2;;
      esac
    done <<<"$snapshot"
  done
  services_status_cleanup

  printf '%-8s %-14s %s\n' VM SERVICE STATE
  for record in "${service_records[@]}"; do
    IFS='|' read -r _ machine unit state <<<"$record"
    printf '%-8s %-14s %s\n' "$machine" "$unit" "$state"
  done

  printf '\n%-8s %-5s %-10s %-14s %s\n' VM UE SERVICE REGISTRATION PDU_SESSION
  for record in "${ue_records[@]}"; do
    IFS='|' read -r _ machine unit state invocation encoded <<<"$record"
    journal=$(printf '%s' "$encoded" | base64 --decode)
    IFS='|' read -r registration pdu_session < <(ue_readiness_states "$state" "$journal")
    printf '%-8s %-5s %-10s %-14s %s\n' "$machine" "$unit" "$state" "$registration" "$pdu_session"
  done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  trap 'services_status_cleanup; exit 130' INT TERM
  trap services_status_cleanup EXIT
  services_status_main "$@"
fi
