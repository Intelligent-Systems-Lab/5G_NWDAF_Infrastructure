#!/usr/bin/env bash
set -euo pipefail

HOST_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
MACHINES=(core path-a path-b)
CORE_UNITS=(mongodb nrf nssf udr udm ausf pcf amf smf adrf nwdaf-c)
PATH_A_UNITS=(upf-a nwdaf-a gnb-a ue1 ue2 ue3)
PATH_B_UNITS=(upf-b nwdaf-b gnb-b ue4 ue5 ue6)

vssh() {
  local machine=$1 command=$2
  (cd "$HOST_ROOT" && vagrant ssh "$machine" -c "$command")
}

unit_action() {
  local machine=$1 action=$2 unit=$3
  vssh "$machine" "sudo systemctl $action 5g-nwdaf@$unit.service"
}

wait_active() {
  local machine=$1 unit=$2 attempt
  for attempt in $(seq 1 30); do
    if vssh "$machine" "systemctl is-active --quiet 5g-nwdaf@$unit.service" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "$machine/$unit did not become active" >&2
  vssh "$machine" "sudo journalctl -u 5g-nwdaf@$unit.service -n 40 --no-pager" >&2 || true
  return 1
}

start_unit() {
  local machine=$1 unit=$2
  echo "START $machine/$unit"
  unit_action "$machine" start "$unit"
  wait_active "$machine" "$unit"
}

stop_unit() {
  local machine=$1 unit=$2
  echo "STOP  $machine/$unit"
  unit_action "$machine" stop "$unit" || true
}

config_hash() {
  find "$1" -type f -name '*.yaml' -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
}

effective_config_dir() {
  local testbed=$1 explicit=${2:-}
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" "$explicit" <<'PY'
import sys
from configlib import load_yaml, resolve_config_dir, resolve_path
definition = load_yaml(resolve_path(sys.argv[1]))
print(resolve_config_dir(definition, sys.argv[2] or None))
PY
}

stage_config_all() {
  local config_dir=$1 hash=$2 name archive temporary machine destination
  name=$(basename "$config_dir")
  destination="/etc/5g-nwdaf-infrastructure/config-sets/${name}-${hash:0:16}"
  temporary=$(mktemp -d)
  archive="$temporary/config.tgz"
  trap 'rm -rf "$temporary"' RETURN
  tar -C "$config_dir" -czf "$archive" .
  for machine in "${MACHINES[@]}"; do
    echo "STAGE $machine $destination"
    (cd "$HOST_ROOT" && vagrant upload "$archive" "/tmp/5g-nwdaf-config-${hash:0:16}.tgz" "$machine")
    vssh "$machine" "sudo rm -rf '$destination' && sudo install -d '$destination' && sudo tar -C '$destination' -xzf '/tmp/5g-nwdaf-config-${hash:0:16}.tgz' && sudo rm -f '/tmp/5g-nwdaf-config-${hash:0:16}.tgz' && sudo /usr/local/libexec/5g-nwdaf-infrastructure/config-activate '$machine' '$destination' '$hash'"
  done
  trap - RETURN
  rm -rf "$temporary"
}

print_unit_status() {
  local machine=$1 unit=$2 state
  state=$(vssh "$machine" "systemctl is-active 5g-nwdaf@$unit.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
  printf '%-8s %-14s %s\n' "$machine" "$unit" "${state:-unknown}"
}
