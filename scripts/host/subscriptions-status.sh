#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

service_state=$(vssh core "systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
echo "consumer=${service_state:-unknown}"
vssh core "/usr/local/libexec/5g-nwdaf-infrastructure/nwdaf-consumer --config /etc/5g-nwdaf-infrastructure/active/consumer.yaml status" 2>/dev/null || true

