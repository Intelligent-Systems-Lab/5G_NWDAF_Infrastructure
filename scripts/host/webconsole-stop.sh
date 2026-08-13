#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

core_state=$(vm_state_for core)
if [ "$core_state" = running ]; then
  state=$(vssh core "systemctl is-active 5g-nwdaf@webconsole.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
else
  state=not-running
fi
if [ "$state" = active ] || [ "$state" = activating ] || [ "$state" = failed ]; then
  stop_unit core webconsole
else
  echo "WebConsole is not running."
fi
echo "WebConsole stopped; artifact, toolchain, config, MongoDB, and other Guest services were retained."
