#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

for pair in path-a:nwdaf-a path-b:nwdaf-b; do
  machine=${pair%%:*}; unit=${pair##*:}
  vssh "$machine" "systemctl is-active --quiet 5g-nwdaf@$unit.service" || {
    echo "$machine/$unit must be active before creating subscriptions" >&2
    exit 1
  }
done

vssh core "sudo systemctl start 5g-nwdaf-consumer.service"
for attempt in $(seq 1 30); do
  state=$(vssh core "/usr/local/libexec/5g-nwdaf-infrastructure/nwdaf-consumer --config /etc/5g-nwdaf-infrastructure/active/consumer.yaml status" 2>/dev/null || true)
  if python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="active" and len(d.get("subscriptions",[]))==2 and len({x["nfInstanceId"] for x in d["subscriptions"]})==2' <<<"$state" 2>/dev/null; then
    echo "$state"
    exit 0
  fi
  sleep 1
done
vssh core "sudo journalctl -u 5g-nwdaf-consumer.service -n 60 --no-pager" >&2 || true
exit 1

