#!/usr/bin/env bash
set -euo pipefail

role=${1:?usage: config-activate.sh role staged-directory expected-hash}
staged=${2:?usage: config-activate.sh role staged-directory expected-hash}
expected_hash=${3:?usage: config-activate.sh role staged-directory expected-hash}
root=/etc/5g-nwdaf-infrastructure
test "$(id -u)" -eq 0 || { echo "activation requires root" >&2; exit 1; }
test "$role" = "$(cat "$root/machine")" || { echo "role does not match this guest" >&2; exit 1; }
case "$staged" in "$root"/config-sets/*) ;; *) echo "staged directory is outside config-sets" >&2; exit 1;; esac
test -d "$staged" && test -f "$staged/manifest.yaml" || { echo "incomplete staged config" >&2; exit 1; }

if systemctl list-units --state=active --no-legend '5g-nwdaf@*.service' | grep -q .; then
  echo "stop stack services before changing the active config" >&2
  exit 1
fi

actual_hash=$(find "$staged" -type f -name '*.yaml' -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')
test "$actual_hash" = "$expected_hash" || { echo "config hash mismatch: $actual_hash" >&2; exit 1; }
temporary="$root/.active.$$"
ln -s "$staged" "$temporary"
mv -Tf "$temporary" "$root/active"
printf '%s\n' "$expected_hash" >"$root/active.sha256"

