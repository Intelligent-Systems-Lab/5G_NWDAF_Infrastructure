#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

service_state=$(vssh core "systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
echo "consumer=${service_state:-unknown}"
consumer_cli status 2>/dev/null || true
