#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [ -n "${TESTBED:-}" ]; then
  select_testbed_machines "$TESTBED"
fi

files=(
  scripts/guest/runtime-tools-install.sh
  scripts/guest/service-run.sh
  scripts/guest/config-activate.sh
  scripts/guest/network-config.py
  scripts/guest/network-setup.sh
  scripts/shared/config_hash.py
  scripts/guest/systemd/5g-nwdaf@.service
  scripts/guest/systemd/5g-nwdaf-stack.target
  scripts/guest/systemd/5g-nwdaf-network.service
)

temporary=$(mktemp -d)
cleanup() {
  rm -rf "$temporary"
}
trap cleanup EXIT
archive="$temporary/runtime-tools.tgz"
tar -C "$HOST_ROOT" -czf "$archive" "${files[@]}"
remote_archive="/tmp/5g-nwdaf-runtime-tools-${UID}-$$-${RANDOM}.tgz"

selected_machines=("${MACHINES[@]}")
if [ "$#" -gt 0 ]; then
  selected_machines=("$@")
  for machine in "${selected_machines[@]}"; do
    [[ " ${MACHINES[*]} " == *" $machine "* ]] || {
      echo "invalid guest-tools-sync machine: $machine" >&2
      exit 2
    }
  done
fi

sync_machine() {
  local machine=$1 command
  echo "SYNC RUNTIME TOOLS $machine"
  guest_upload "$archive" "$remote_archive" "$machine" || return
  printf -v command \
    'set -euo pipefail; archive=%q; stage=$(mktemp -d); trap '\''rm -rf "$stage" "$archive"'\'' EXIT; tar -C "$stage" -xzf "$archive"; sudo bash "$stage/scripts/guest/runtime-tools-install.sh" %q "$stage"' \
    "$remote_archive" "$machine"
  vssh "$machine" "$command" || return
}

pids=()
for machine in "${selected_machines[@]}"; do
  sync_machine "$machine" >"$temporary/$machine.log" 2>&1 &
  pids+=("$!")
done
failed=false
for index in "${!selected_machines[@]}"; do
  if ! wait "${pids[$index]}"; then
    failed=true
  fi
  sed -n '1,$p' "$temporary/${selected_machines[$index]}.log"
done
! $failed
