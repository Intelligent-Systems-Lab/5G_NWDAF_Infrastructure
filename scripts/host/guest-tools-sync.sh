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
  scripts/guest/dataset-activate.sh
  scripts/guest/webconsole-build.sh
  scripts/guest/subscriber-data.js
  scripts/shared/config_hash.py
  scripts/guest/systemd/5g-nwdaf@.service
  scripts/guest/systemd/5g-nwdaf-stack.target
  scripts/guest/systemd/5g-nwdaf-network.service
  scripts/guest/systemd/5g-nwdaf-consumer.service
  tools/nwdaf-consumer/consumer.py
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

for machine in "${selected_machines[@]}"; do
  echo "SYNC RUNTIME TOOLS $machine"
  (cd "$HOST_ROOT" && provider_vagrant upload "$archive" "$remote_archive" "$machine")
  printf -v command \
    'set -euo pipefail; archive=%q; stage=$(mktemp -d); trap '\''rm -rf "$stage" "$archive"'\'' EXIT; tar -C "$stage" -xzf "$archive"; sudo bash "$stage/scripts/guest/runtime-tools-install.sh" %q "$stage"' \
    "$remote_archive" "$machine"
  vssh "$machine" "$command"
done
