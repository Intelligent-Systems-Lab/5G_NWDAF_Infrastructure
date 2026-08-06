#!/usr/bin/env bash
set -euo pipefail

machine=$(cat /etc/5g-nwdaf-infrastructure/machine)
add_alias() {
  local address=$1 anchor=$2 device
  ip -4 address show | grep -qw "$address" && return 0
  device=$(ip -o route get "$anchor" | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')
  test -n "$device" || { echo "cannot resolve interface for $address via $anchor" >&2; return 1; }
  ip address add "$address/24" dev "$device"
}

case "$machine" in
  core)
    for address in 192.168.57.{10..19} 192.168.57.{30..32}; do add_alias "$address" 192.168.57.2; done
    add_alias 192.168.58.10 192.168.58.2
    add_alias 192.168.61.10 192.168.61.2
    ;;
  path-a)
    for address in 192.168.57.{40..43}; do add_alias "$address" 192.168.57.3; done
    add_alias 192.168.58.20 192.168.58.3
    for address in 192.168.59.10 192.168.59.20; do add_alias "$address" 192.168.59.2; done
    add_alias 192.168.61.20 192.168.61.3
    add_alias 192.168.62.10 192.168.62.2
    ;;
  path-b)
    for address in 192.168.57.{50..53}; do add_alias "$address" 192.168.57.4; done
    add_alias 192.168.58.30 192.168.58.4
    for address in 192.168.60.10 192.168.60.20; do add_alias "$address" 192.168.60.2; done
    add_alias 192.168.61.30 192.168.61.4
    add_alias 192.168.63.10 192.168.63.2
    ;;
  *) echo "invalid machine identity: $machine" >&2; exit 2;;
esac

