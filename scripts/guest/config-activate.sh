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
test -f "$staged/network/$role.yaml" || { echo "missing network config for $role" >&2; exit 1; }

if systemctl list-units --state=active --no-legend '5g-nwdaf@*.service' | grep -q .; then
  echo "stop stack services before changing the active config" >&2
  exit 1
fi
if systemctl is-active --quiet 5g-nwdaf-consumer.service; then
  echo "stop NWDAF subscriptions before changing the active config" >&2
  exit 1
fi

actual_hash=$(
  cd "$staged"
  find . -type f -name '*.yaml' -print0 |
    LC_ALL=C sort -z |
    xargs -0 sha256sum |
    sha256sum |
    awk '{print $1}'
)
test "$actual_hash" = "$expected_hash" || { echo "config hash mismatch: $actual_hash" >&2; exit 1; }
old_target=$(readlink "$root/active" 2>/dev/null || true)
old_hash=$(cat "$root/active.sha256" 2>/dev/null || true)
temporary="$root/.active.$$"
ln -s "$staged" "$temporary"
mv -Tf "$temporary" "$root/active"
if ! systemctl restart 5g-nwdaf-network.service; then
  echo "network activation failed; restoring previous config" >&2
  if [ -n "$old_target" ]; then
    ln -s "$old_target" "$temporary"
    mv -Tf "$temporary" "$root/active"
    systemctl restart 5g-nwdaf-network.service || true
  else
    rm -f "$root/active"
    /usr/local/libexec/5g-nwdaf-infrastructure/network-setup --clear || true
  fi
  if [ -n "$old_hash" ]; then
    printf '%s\n' "$old_hash" >"$root/active.sha256"
  else
    rm -f "$root/active.sha256"
  fi
  exit 1
fi
printf '%s\n' "$expected_hash" >"$root/active.sha256"
