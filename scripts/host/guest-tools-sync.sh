#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

files=(
  scripts/guest/runtime-tools-install.sh
  scripts/guest/service-run.sh
  scripts/guest/config-activate.sh
  scripts/guest/network-config.py
  scripts/guest/network-setup.sh
  scripts/guest/dataset-activate.sh
  scripts/guest/subscriber-data.js
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
source_hash=$(sha256sum "$archive" | awk '{print $1}')
remote_archive="/tmp/5g-nwdaf-runtime-tools-${source_hash:0:16}.tgz"

for machine in "${MACHINES[@]}"; do
  echo "SYNC RUNTIME TOOLS $machine source=$source_hash"
  (cd "$HOST_ROOT" && vagrant upload "$archive" "$remote_archive" "$machine")
  printf -v command \
    'set -euo pipefail; archive=%q; expected=%q; actual=$(sha256sum "$archive" | awk '\''{print $1}'\''); test "$actual" = "$expected"; stage=$(mktemp -d); trap '\''rm -rf "$stage" "$archive"'\'' EXIT; tar -C "$stage" -xzf "$archive"; sudo bash "$stage/scripts/guest/runtime-tools-install.sh" %q "$stage" "$expected"' \
    "$remote_archive" "$source_hash" "$machine"
  vssh "$machine" "$command"
done
