#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

vm=all
service='*'
since='10 minutes ago'
while [ "$#" -gt 0 ]; do
  case "$1" in
    --vm) vm=$2; shift 2;;
    --service) service=$2; shift 2;;
    --since) since=$2; shift 2;;
    *) echo "usage: logs.sh [--vm core|path-a|path-b|all] [--service name|*] [--since value]" >&2; exit 2;;
  esac
done
case "$vm" in all) selected=(core path-a path-b);; core|path-a|path-b) selected=("$vm");; *) echo "invalid VM: $vm" >&2; exit 2;; esac
[[ "$service" =~ ^[A-Za-z0-9*?-]+$ ]] || { echo "invalid service filter" >&2; exit 2; }

pids=()
cleanup() { [ "${#pids[@]}" -eq 0 ] || kill "${pids[@]}" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
printf -v remote_since '%q' "$since"
for machine in "${selected[@]}"; do
  (
    vssh "$machine" "sudo journalctl -f --since $remote_since -u '5g-nwdaf@$service.service' -o cat" 2>&1 |
      sed -u "s/^/[$machine] /"
  ) &
  pids+=("$!")
done
wait
