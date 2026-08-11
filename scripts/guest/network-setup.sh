#!/usr/bin/env bash
set -euo pipefail

root=/etc/5g-nwdaf-infrastructure
fragment=/etc/netplan/60-5g-nwdaf-aliases.yaml
renderer=/usr/local/libexec/5g-nwdaf-infrastructure/network-config
legacy_state=/run/5g-nwdaf-infrastructure/network-aliases

test "$(id -u)" -eq 0 || { echo "network setup requires root" >&2; exit 1; }
machine=$(cat "$root/machine")
action=apply
config=$root/active/network/$machine.yaml
case "${1:-}" in
  "") ;;
  --verify) action=verify ;;
  --clear) action=clear ;;
  *) config=$1 ;;
esac

temporary=$(mktemp -d /tmp/5g-nwdaf-network.XXXXXX)
addresses=$temporary/addresses.json
candidate=$temporary/60-5g-nwdaf-aliases.yaml
plan=$temporary/plan.json
previous=$temporary/previous.yaml
validation_root=$temporary/validation
had_previous=false
rollback_needed=false

if [ -f "$fragment" ]; then
  cp "$fragment" "$previous"
  had_previous=true
fi

plan_devices() {
  jq -r '.affectedDevices[]' "$plan"
  if [ -f "$legacy_state" ]; then
    awk -F '\t' 'NF >= 2 && $2 != "" {print $2}' "$legacy_state"
  fi
}

reject_default_route_aliases() {
  local default_device
  default_device=$(ip -4 route show default | awk 'NR == 1 {for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
  [ -n "$default_device" ] || return 0
  if jq -e --arg device "$default_device" \
    '.aliases[] | select(.device == $device)' "$plan" >/dev/null; then
    echo "refusing managed aliases on default-route interface $default_device" >&2
    return 1
  fi
}

verify_effective_addresses() {
  local cidr device address prefix
  while IFS=$'\t' read -r cidr device; do
    [ -n "$cidr" ] && [ -n "$device" ] || continue
    address=${cidr%/*}
    prefix=${cidr#*/}
    ip -o -4 address show dev "$device" | awk -v target="$address/$prefix" '
      $4 == target { found = 1 }
      END { exit(found ? 0 : 1) }
    ' || {
      echo "managed alias $cidr is missing from $device" >&2
      return 1
    }
  done < <(jq -r '.aliases[] | [.cidr, .device] | @tsv' "$plan")

  while IFS=$'\t' read -r cidr device; do
    [ -n "$cidr" ] && [ -n "$device" ] || continue
    if jq -e --arg cidr "$cidr" --arg device "$device" \
      '.aliases[] | select(.cidr == $cidr and .device == $device)' "$plan" >/dev/null; then
      continue
    fi
    if ip -o -4 address show dev "$device" | awk -v target="$cidr" '
      $4 == target { found = 1 }
      END { exit(found ? 0 : 1) }
    '; then
      echo "stale managed alias $cidr remains on $device" >&2
      return 1
    fi
  done < <(jq -r '.staleAliases[] | [.cidr, .device] | @tsv' "$plan")

  if [ -f "$legacy_state" ]; then
    while IFS=$'\t' read -r cidr device; do
      [ -n "$cidr" ] && [ -n "$device" ] || continue
      if jq -e --arg cidr "$cidr" --arg device "$device" \
        '.aliases[] | select(.cidr == $cidr and .device == $device)' "$plan" >/dev/null; then
        continue
      fi
      if ip -o -4 address show dev "$device" | awk -v target="$cidr" '
        $4 == target { found = 1 }
        END { exit(found ? 0 : 1) }
      '; then
        echo "legacy runtime alias $cidr remains on $device" >&2
        return 1
      fi
    done <"$legacy_state"
  fi
}

reconfigure_affected_devices() {
  local device default_device
  default_device=$(ip -4 route show default | awk 'NR == 1 {for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
  netplan generate
  networkctl reload
  while IFS= read -r device; do
    [ -n "$device" ] || continue
    if ! ip link show dev "$device" >/dev/null 2>&1; then
      continue
    fi
    if [ -n "$default_device" ] && [ "$device" = "$default_device" ]; then
      echo "refusing to reconfigure default-route interface $device" >&2
      return 1
    fi
    networkctl reconfigure "$device"
  done < <(plan_devices | LC_ALL=C sort -u)

  for _attempt in $(seq 1 10); do
    verify_effective_addresses && return 0
    sleep 1
  done
  verify_effective_addresses
}

restore_previous_fragment() {
  set +e
  if [ "$had_previous" = true ]; then
    install -m 0600 "$previous" "$fragment.rollback"
    mv -f "$fragment.rollback" "$fragment"
  else
    rm -f "$fragment"
  fi
  netplan generate
  networkctl reload
  while IFS= read -r device; do
    [ -n "$device" ] || continue
    ip link show dev "$device" >/dev/null 2>&1 && networkctl reconfigure "$device"
  done < <(plan_devices | LC_ALL=C sort -u)
  set -e
}

cleanup() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rollback_needed" = true ]; then
    echo "network reconciliation failed; restoring previous Netplan fragment" >&2
    restore_previous_fragment
  fi
  rm -rf "$temporary"
  exit "$status"
}
trap cleanup EXIT

if [ "$action" = clear ]; then
  "$renderer" --clear --previous "$fragment" --plan-output "$plan"
  if [ ! -f "$fragment" ] && [ ! -f "$legacy_state" ]; then
    echo "NETWORK machine=$machine aliases=0 state=clear"
    exit 0
  fi
  rollback_needed=true
  rm -f "$fragment"
  reconfigure_affected_devices
  rm -f "$legacy_state"
  rollback_needed=false
  echo "NETWORK machine=$machine aliases=0 state=clear"
  exit 0
fi

test -f "$config" || { echo "network config not found: $config" >&2; exit 1; }
ip -j -4 address show >"$addresses"
render_args=(
  --config "$config"
  --machine "$machine"
  --addresses "$addresses"
  --output "$candidate"
  --plan-output "$plan"
)
[ -f "$fragment" ] && render_args+=(--previous "$fragment")
"$renderer" "${render_args[@]}"
reject_default_route_aliases

if [ "$action" = verify ]; then
  test -f "$fragment" || { echo "managed Netplan fragment is missing" >&2; exit 1; }
  cmp -s "$candidate" "$fragment" || {
    echo "managed Netplan fragment does not match the active config" >&2
    exit 1
  }
  test ! -f "$legacy_state" || {
    echo "legacy runtime alias state still requires migration" >&2
    exit 1
  }
  verify_effective_addresses
  echo "NETWORK machine=$machine aliases=$(jq '.aliases | length' "$plan") state=verified"
  exit 0
fi

if [ -f "$fragment" ] && cmp -s "$candidate" "$fragment" && \
   [ ! -f "$legacy_state" ] && verify_effective_addresses; then
  echo "NETWORK machine=$machine aliases=$(jq '.aliases | length' "$plan") state=unchanged"
  exit 0
fi

install -d "$validation_root/etc/netplan"
while IFS= read -r -d '' source_file; do
  install -m 0600 "$source_file" "$validation_root/etc/netplan/$(basename "$source_file")"
done < <(find /etc/netplan -maxdepth 1 -type f -name '*.yaml' ! -name '60-5g-nwdaf-aliases.yaml' -print0)
install -m 0600 "$candidate" "$validation_root/etc/netplan/60-5g-nwdaf-aliases.yaml"
netplan generate --root-dir "$validation_root"

rollback_needed=true
install -m 0600 "$candidate" "$fragment.new"
mv -f "$fragment.new" "$fragment"
reconfigure_affected_devices
rm -f "$legacy_state"
rollback_needed=false
echo "NETWORK machine=$machine aliases=$(jq '.aliases | length' "$plan") state=applied config=$config"
