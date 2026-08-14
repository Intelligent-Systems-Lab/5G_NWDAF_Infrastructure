#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

render_subscription_status() {
  python3 -c '
import json
import sys

state = json.load(sys.stdin)
subscriptions = state.get("subscriptions", [])
callbacks = state.get("callbacksByPath", {})
if not isinstance(subscriptions, list):
    raise ValueError("subscriptions must be a list")
if not isinstance(callbacks, dict):
    raise ValueError("callbacksByPath must be an object")

print("local_resource_state={} total_callback_requests={}".format(
    state.get("status", "unknown"), state.get("notificationCount", 0)
))
print("{:<5} {:<10} {:<36} {:<14} {:<28} {:<10} {:<27} {}".format(
    "PATH", "RESOURCE", "PROVIDER", "TAC", "CORRELATION", "CALLBACKS", "LAST_CALLBACK_UTC", "LOCATION"
))
for item in sorted(subscriptions, key=lambda value: str(value.get("path", ""))):
    if not isinstance(item, dict):
        raise ValueError("subscription entries must be objects")
    path = str(item.get("path", "unknown"))
    summary = callbacks.get(path)
    if summary is None:
        count = "unknown"
        last = "unknown"
    elif not isinstance(summary, dict):
        raise ValueError("callback summary for path {} must be an object".format(path))
    else:
        count = summary.get("requestCount", 0)
        last = summary.get("lastCallbackAt", "not-seen")
    print("{:<5} {:<10} {:<36} {:<14} {:<28} {:<10} {:<27} {}".format(
        path,
        item.get("status", "unknown"),
        item.get("nfInstanceId", "unknown"),
        item.get("tac", "unknown"),
        item.get("correlationId", "unknown"),
        count,
        last,
        item.get("location", "unknown"),
    ))

unknown = state.get("unknownCallbacks")
if unknown:
    if not isinstance(unknown, dict):
        raise ValueError("unknownCallbacks must be an object")
    print("WARN unknown_callback_requests={} last={} correlations={}".format(
        unknown.get("requestCount", 0),
        unknown.get("lastCallbackAt", "unknown"),
        ",".join(str(value) for value in unknown.get("correlationIds", [])) or "none",
    ))
'
}

subscriptions_status_main() {
  local core_state service_state state
  core_state=$(vm_state_for core)
  if [ "$core_state" != running ]; then
    echo "consumer_service=not-running reason=core-not-running"
    echo "local_resource_state=not-readable reason=core-not-running"
    return 0
  fi
  if ! service_state=$(vssh core "state=\$(systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true); printf '%s\\n' \"\${state:-unknown}\"" | tr -d '\r' | tail -n 1); then
    echo "failed to query Core Consumer service state" >&2
    return 1
  fi
  printf 'consumer_service=%s\n' "${service_state:-unknown}"
  if ! state=$(consumer_cli status); then
    echo "failed to read Consumer saved subscription state" >&2
    return 1
  fi
  if ! render_subscription_status <<<"$state"; then
    echo "failed to parse Consumer saved subscription state" >&2
    return 1
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  subscriptions_status_main "$@"
fi
