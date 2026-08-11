#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

for entry in "path-a A" "path-b B"; do
  read -r machine path_name <<<"$entry"
  remote=/tmp/5g-nwdaf-gtp5g-check.sh
  echo "GTP5G $machine"
  (cd "$HOST_ROOT" && vagrant upload "$HOST_ROOT/scripts/guest/gtp5g-check.sh" "$remote" "$machine")
  vssh "$machine" "sudo bash '$remote' '$path_name'; status=\$?; rm -f '$remote'; exit \$status"
done
