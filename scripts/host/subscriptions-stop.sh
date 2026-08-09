#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if ! consumer_cli delete; then
  echo "one or more exact-resource DELETE operations failed; callback and state remain available for retry" >&2
  exit 1
fi
vssh core "sudo systemctl stop 5g-nwdaf-consumer.service"
echo "Both NWDAF subscriptions were deleted and the callback was stopped."
