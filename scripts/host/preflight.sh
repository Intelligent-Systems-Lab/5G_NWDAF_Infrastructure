#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
failures=0
warnings=0

ok() { echo "OK   $*"; }
fail() { echo "FAIL $*" >&2; failures=$((failures + 1)); }
warn() { echo "WARN $*" >&2; warnings=$((warnings + 1)); }

for command in git python3 sha256sum tar vagrant; do
  command -v "$command" >/dev/null && ok "$command=$(command -v "$command")" || fail "missing command: $command"
done

if command -v vagrant >/dev/null; then
  provider=${VAGRANT_DEFAULT_PROVIDER:-}
  if [ -z "$provider" ] && [ -f "$HOST_ROOT/testbed.local.yaml" ]; then
    provider=$(python3 -c 'import sys,yaml; print((yaml.safe_load(open(sys.argv[1])) or {}).get("provider",{}).get("name", ""))' "$HOST_ROOT/testbed.local.yaml")
  fi
  case "$provider" in
    virtualbox) command -v VBoxManage >/dev/null && ok "VirtualBox CLI available" || fail "VirtualBox selected but VBoxManage is missing" ;;
    libvirt) command -v virsh >/dev/null && ok "libvirt CLI available" || fail "libvirt selected but virsh is missing" ;;
    "") warn "provider not selected; set testbed.local.yaml or VAGRANT_DEFAULT_PROVIDER" ;;
    *) warn "provider '$provider' has no dedicated feasibility check" ;;
  esac
fi

read -r required_mib disk_gib < <(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_path
d = load_yaml(resolve_path(sys.argv[1]))
print(sum(m["resources"]["memoryMiB"] for m in d["machines"].values()), sum(m["resources"]["diskGiB"] for m in d["machines"].values()))
PY
)
available_mib=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
free_gib=$(df -Pk "$HOST_ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
[ "$available_mib" -ge "$required_mib" ] && ok "available RAM ${available_mib}MiB >= VM allocation ${required_mib}MiB" || fail "available RAM ${available_mib}MiB < VM allocation ${required_mib}MiB"
[ "$free_gib" -ge 120 ] && ok "workspace filesystem free ${free_gib}GiB (disk budgets total ${disk_gib}GiB)" || fail "workspace filesystem free ${free_gib}GiB < 120GiB safety threshold"

if git -C "$HOST_ROOT" submodule status --recursive | grep -Eq '^[-+U]'; then
  fail "submodules are missing, conflicted, or not at parent gitlinks"
else
  ok "submodule gitlinks initialized"
fi
if git -C "$HOST_ROOT" submodule foreach --quiet 'test -z "$(git status --porcelain)"' >/dev/null; then
  ok "submodule worktrees clean"
else
  fail "one or more submodule worktrees are dirty"
fi

PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$HOST_ROOT" "$HOST_ROOT/components.lock.yaml" <<'PY' || failures=$((failures + 1))
import subprocess, sys, yaml
root, lock_path = sys.argv[1:]
lock = yaml.safe_load(open(lock_path))
for item in lock["components"]:
    actual = subprocess.check_output(["git", "-C", root + "/" + item["path"], "rev-parse", "HEAD"], text=True).strip()
    if actual != item["commit"]:
        raise SystemExit("LOCK MISMATCH {} expected={} actual={}".format(item["path"], item["commit"], actual))
print("OK   {} component locks match".format(len(lock["components"])))
PY

config_dir=$(effective_config_dir "$testbed" "$explicit_config")
if python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$config_dir"; then
  ok "effective config check"
else
  fail "effective config check"
fi

echo "SUMMARY failures=$failures warnings=$warnings"
[ "$failures" -eq 0 ]
