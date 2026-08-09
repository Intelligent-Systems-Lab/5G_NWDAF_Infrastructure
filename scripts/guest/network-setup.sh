#!/usr/bin/env bash
set -euo pipefail

root=/etc/5g-nwdaf-infrastructure
test "$(id -u)" -eq 0 || { echo "network setup requires root" >&2; exit 1; }
machine=$(cat "$root/machine")
state_dir=/run/5g-nwdaf-infrastructure
state="$state_dir/network-aliases"
state_tmp="$state.$$"
install -d "$state_dir"

clear_managed_aliases() {
  local cidr device address
  [ -f "$state" ] || return 0
  while IFS=$'\t' read -r cidr device; do
    [ -n "$cidr" ] && [ -n "$device" ] || continue
    address=${cidr%/*}
    if ip -o -4 address show dev "$device" | awk -v target="$address" '
      { split($4, item, "/"); if (item[1] == target) found = 1 }
      END { exit(found ? 0 : 1) }
    '; then
      ip address del "$cidr" dev "$device" || true
    fi
  done <"$state"
  rm -f "$state"
}

if [ "${1:-}" = "--clear" ]; then
  clear_managed_aliases
  exit 0
fi

config=${1:-$root/active/network/$machine.yaml}
test -f "$config" || { echo "network config not found: $config" >&2; exit 1; }

plan=$(mktemp)
resolved=$(mktemp)
added=$(mktemp)
rollback_additions=true
cleanup() {
  local status=$? cidr device
  if [ "$status" -ne 0 ] && [ "$rollback_additions" = true ]; then
    while IFS=$'\t' read -r cidr device; do
      [ -n "$cidr" ] && ip address del "$cidr" dev "$device" >/dev/null 2>&1 || true
    done <"$added"
  fi
  rm -f "$plan" "$resolved" "$added" "$state_tmp"
  trap - EXIT
  exit "$status"
}
trap cleanup EXIT

python3 - "$config" "$machine" >"$plan" <<'PY'
import ipaddress
import sys

import yaml

path, expected_machine = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    config = yaml.safe_load(stream) or {}

if config.get("schemaVersion") != 1:
    raise SystemExit("unsupported network config schema")
if config.get("machine") != expected_machine:
    raise SystemExit("network config machine does not match this guest")

aliases = config.get("aliases", [])
if not isinstance(aliases, list):
    raise SystemExit("network aliases must be a list")

seen = set()
for item in aliases:
    if not isinstance(item, dict):
        raise SystemExit("network alias must be an object")
    required = ("owner", "endpoint", "network", "address", "prefixLength", "anchor")
    if any(key not in item for key in required):
        raise SystemExit("network alias is missing a required field")
    for key in ("owner", "endpoint", "network"):
        if not isinstance(item[key], str) or not item[key] or any(
            separator in item[key] for separator in ("\t", "\r", "\n")
        ):
            raise SystemExit("invalid network alias {}".format(key))
    address = ipaddress.ip_address(item["address"])
    anchor = ipaddress.ip_address(item["anchor"])
    prefix = item["prefixLength"]
    if address.version != 4 or anchor.version != 4:
        raise SystemExit("only IPv4 aliases are supported")
    if not isinstance(prefix, int) or not 0 <= prefix <= 32:
        raise SystemExit("invalid alias prefix length")
    if anchor not in ipaddress.ip_network("{}/{}".format(address, prefix), strict=False):
        raise SystemExit("alias and anchor are not in the same subnet")
    if address == anchor:
        raise SystemExit("alias duplicates its anchor address")
    if str(address) in seen:
        raise SystemExit("duplicate alias address: {}".format(address))
    seen.add(str(address))
    print(
        "{}\t{}\t{}\t{}\t{}".format(
            address, prefix, anchor, item["owner"], item["endpoint"]
        )
    )
PY

while IFS=$'\t' read -r address prefix anchor owner endpoint; do
  device=$(ip -o -4 address show | awk -v target="$anchor" '
    { split($4, item, "/"); if (item[1] == target) { print $2; exit } }
  ')
  test -n "$device" || {
    echo "cannot resolve interface for $owner/$endpoint via anchor $anchor" >&2
    exit 1
  }
  printf '%s\t%s\n' "$address/$prefix" "$device" >>"$resolved"
done <"$plan"

while IFS=$'\t' read -r cidr device; do
  address=${cidr%/*}
  existing_device=$(ip -o -4 address show | awk -v target="$address" '
    { split($4, item, "/"); if (item[1] == target) { print $2; exit } }
  ')
  if [ -n "$existing_device" ] && [ "$existing_device" != "$device" ]; then
    echo "$address already exists on $existing_device instead of $device" >&2
    exit 1
  fi
  if [ -z "$existing_device" ]; then
    ip address add "$cidr" dev "$device"
    printf '%s\t%s\n' "$cidr" "$device" >>"$added"
  fi
done <"$resolved"

if [ -f "$state" ]; then
  while IFS=$'\t' read -r cidr device; do
    [ -n "$cidr" ] && [ -n "$device" ] || continue
    if ! grep -Fqx "$cidr"$'\t'"$device" "$resolved"; then
      ip address del "$cidr" dev "$device" >/dev/null 2>&1 || true
    fi
  done <"$state"
fi

cp "$resolved" "$state_tmp"
mv -f "$state_tmp" "$state"
rollback_additions=false
echo "NETWORK machine=$machine aliases=$(wc -l <"$state") config=$config"
