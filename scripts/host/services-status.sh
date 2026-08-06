#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

machine_status() {
  local machine=$1; shift
  local units="$*"
  vssh "$machine" "for unit in $units; do state=\$(systemctl is-active 5g-nwdaf@\$unit.service 2>/dev/null || true); printf '%-8s %-14s %s\\n' '$machine' \"\$unit\" \"\$state\"; done"
}

machine_status core "${CORE_UNITS[@]}"
machine_status path-a "${PATH_A_UNITS[@]}"
machine_status path-b "${PATH_B_UNITS[@]}"
