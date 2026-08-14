#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
enabled=$(config_webconsole_enabled "$config_dir")
if [ "$enabled" != true ]; then
  echo "WebConsole is disabled by $config_dir; no toolchain, artifact, or process was changed."
  exit 0
fi

expected_hash=$(config_hash "$config_dir")
active_hash=$(vssh core "cat /etc/5g-nwdaf-infrastructure/active.sha256 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
test "$active_hash" = "$expected_hash" || {
  echo "Core active config does not match $config_dir; start Guest services with this config first" >&2
  exit 1
}
for prerequisite in mongodb nrf; do
  vssh core "systemctl is-active --quiet 5g-nwdaf@$prerequisite.service" || {
    echo "Core $prerequisite must be active before WebConsole" >&2
    exit 1
  }
done

"$HOST_ROOT/scripts/host/guest-tools-sync.sh" core
"$HOST_ROOT/scripts/host/webconsole-prepare.sh" "$testbed" "$explicit_config"
echo "WARN pinned WebConsole recreates its admin tenant/account on every start; credentials reset to admin/free5gc."
start_unit core webconsole

read -r address port < <(config_webconsole_endpoint "$config_dir")
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://$address:$port/" >/dev/null; then
    "$HOST_ROOT/scripts/host/webconsole-status.sh"
    echo "WebConsole is available at http://$address:$port"
    exit 0
  fi
  sleep 1
done
echo "WebConsole HTTP endpoint did not become ready" >&2
vssh core "sudo journalctl -u 5g-nwdaf@webconsole.service -n 80 --no-pager" >&2 || true
stop_unit core webconsole
exit 1
