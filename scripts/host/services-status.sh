#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

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
  local machine snapshot line record unit state invocation encoded journal registration pdu_session vm_records
  local -a units=()
  local -a service_records=()
  local -a ue_records=()
  local -A vm_states=()
  vm_records=$(vm_state_records) || return
  while IFS='|' read -r machine state; do
    vm_states["$machine"]=$state
  done <<<"$vm_records"
  for machine in core path-a path-b; do
    case "$machine" in
      core) units=("${CORE_UNITS[@]}");;
      path-a) units=("${PATH_A_UNITS[@]}");;
      path-b) units=("${PATH_B_UNITS[@]}");;
    esac
    if [ "${vm_states[$machine]:-unknown}" != running ]; then
      for unit in "${units[@]}"; do
        service_records+=("SERVICE|$machine|$unit|not-running")
        case "$unit" in ue[0-9]*) ue_records+=("UE|$machine|$unit|not-running||");; esac
      done
      continue
    fi
    snapshot=$(machine_snapshot "$machine" "${units[@]}")
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
  services_status_main "$@"
fi
