#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
failures=0
warnings=0
virtualbox_storage=

ok() { echo "OK   $*"; }
fail() { echo "FAIL $*" >&2; failures=$((failures + 1)); }
warn() { echo "WARN $*" >&2; warnings=$((warnings + 1)); }

if [ -e "$HOST_ROOT/testbed.local.yaml" ]; then
  fail "testbed.local.yaml is no longer supported; move required settings into the selected TESTBED and remove the legacy file"
fi

if python3 "$HOST_ROOT/scripts/guest/provisioning-lock.py" validate \
  "$HOST_ROOT/provisioning.lock.yaml" >/dev/null; then
  ok "Guest provisioning dependency lock"
else
  fail "invalid Guest provisioning dependency lock"
fi

for command in git go python3 sha256sum tar vagrant docker ip ss flock; do
  command -v "$command" >/dev/null && ok "$command=$(command -v "$command")" || fail "missing command: $command"
done

if command -v vagrant >/dev/null; then
  provider=${VAGRANT_DEFAULT_PROVIDER:-virtualbox}
  case "$provider" in
    virtualbox)
      if command -v VBoxManage >/dev/null && provider_vboxmanage list vms >/dev/null 2>&1; then
        ok "VirtualBox CLI and host driver available"
        virtualbox_storage=$(provider_vboxmanage list systemproperties | sed -n 's/^Default machine folder:[[:space:]]*//p')
        if [ -n "$virtualbox_storage" ] && [ -d "$virtualbox_storage" ]; then
          ok "VirtualBox machine folder available: $virtualbox_storage"
        else
          fail "VirtualBox default machine folder is missing or unavailable: ${virtualbox_storage:-unknown}"
        fi
      else
        fail "VirtualBox selected but VBoxManage cannot initialize the host driver"
      fi
      if [ -r /etc/vbox/networks.conf ]; then
        if python3 - "$testbed" /etc/vbox/networks.conf <<'PY'
import ipaddress
import sys
import yaml

definition_path, allowlist_path = sys.argv[1:]
with open(definition_path, encoding="utf-8") as stream:
    definition = yaml.safe_load(stream)
with open(allowlist_path, encoding="utf-8") as stream:
    allowed = [
        ipaddress.ip_network(line.split()[1])
        for raw in stream
        if (line := raw.strip()) and not line.startswith("#") and line.startswith("*")
    ]
addresses = {
    ipaddress.ip_address(address)
    for machine in definition["machines"].values()
    for address in machine["interfaces"].values()
}
missing = sorted(str(address) for address in addresses if not any(address in network for network in allowed))
if missing:
    print("VirtualBox host-only allowlist excludes: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY
        then
          ok "VirtualBox host-only allowlist covers all machine interfaces"
        else
          fail "VirtualBox host-only allowlist does not cover the testbed address plan"
        fi
      else
        warn "VirtualBox host-only allowlist /etc/vbox/networks.conf is not readable"
      fi
      ;;
    *) fail "unsupported provider '$provider'; the reference deployment supports virtualbox only" ;;
  esac
fi

if docker_info=$(docker info --format '{{.DockerRootDir}} {{.Driver}}' 2>/dev/null); then
  read -r docker_root docker_driver <<<"$docker_info"
  ok "Docker daemon available (root=$docker_root driver=$docker_driver)"
  docker_free_gib=$(df -Pk "$docker_root" | awk 'NR==2 {print int($4/1024/1024)}')
else
  fail "Docker daemon is unavailable to the current user"
  docker_root=""
  docker_free_gib=0
fi

read -r required_mib disk_gib host_reserve_mib swap_policy minimum_swap_mib minimum_free_storage_gib ml_bind_address < <(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_ml_bind_address, resolve_path
d = load_yaml(resolve_path(sys.argv[1]))
safety = d["hostSafety"]
print(
    sum(m["resources"]["memoryMiB"] for m in d["machines"].values()),
    sum(m["resources"]["diskGiB"] for m in d["machines"].values()),
    safety["reserveMemoryMiB"],
    safety["swapPolicy"],
    safety["minimumFreeSwapMiB"],
    safety["minimumFreeStorageGiB"],
    resolve_ml_bind_address(d),
)
PY
)
available_mib=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
swap_free_mib=$(awk '/SwapFree:/ {print int($2/1024)}' /proc/meminfo)
free_gib=$(df -Pk "$HOST_ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
required_with_reserve=$((required_mib + host_reserve_mib))
[ "$available_mib" -ge "$required_with_reserve" ] && ok "available RAM ${available_mib}MiB >= VM allocation ${required_mib}MiB + host reserve ${host_reserve_mib}MiB" || fail "available RAM ${available_mib}MiB < VM allocation ${required_mib}MiB + host reserve ${host_reserve_mib}MiB"
if [ "$swap_free_mib" -ge "$minimum_swap_mib" ]; then
  ok "free swap ${swap_free_mib}MiB >= ${minimum_swap_mib}MiB"
elif [ "$swap_policy" = "warn" ]; then
  warn "free swap ${swap_free_mib}MiB < ${minimum_swap_mib}MiB; monitor MemAvailable during long runs"
else
  fail "free swap ${swap_free_mib}MiB < ${minimum_swap_mib}MiB"
fi
[ "$free_gib" -ge "$minimum_free_storage_gib" ] && ok "workspace filesystem free ${free_gib}GiB (VM logical disk ceilings total ${disk_gib}GiB)" || fail "workspace filesystem free ${free_gib}GiB < ${minimum_free_storage_gib}GiB safety threshold"
if [ -n "$virtualbox_storage" ] && [ -d "$virtualbox_storage" ]; then
  vm_storage_free_gib=$(df -Pk "$virtualbox_storage" | awk 'NR==2 {print int($4/1024/1024)}')
  [ "$vm_storage_free_gib" -ge "$minimum_free_storage_gib" ] && ok "VirtualBox storage filesystem free ${vm_storage_free_gib}GiB" || fail "VirtualBox storage filesystem free ${vm_storage_free_gib}GiB < ${minimum_free_storage_gib}GiB safety threshold"
fi
if [ -n "$docker_root" ]; then
  [ "$docker_free_gib" -ge "$minimum_free_storage_gib" ] && ok "Docker data-root filesystem free ${docker_free_gib}GiB" || fail "Docker data-root filesystem free ${docker_free_gib}GiB < ${minimum_free_storage_gib}GiB safety threshold"
fi

if ip -o address show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$ml_bind_address"; then
  ok "Host ML bind address present: $ml_bind_address"
else
  warn "Host ML bind address $ml_bind_address is not present yet; the provider must create/expose it before ml-start"
fi

mapfile -t ml_ports < <(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_path
d = load_yaml(resolve_path(sys.argv[1]))
for port in sorted({service["publishedPort"] for service in d["mlRuntime"]["services"].values()}):
    print(port)
PY
)
ml_bind_regex=${ml_bind_address//./\\.}
for port in "${ml_ports[@]}"; do
  if ss -H -ltn | awk '{print $4}' | grep -Eq "^(0\\.0\\.0\\.0|\\*|\\[::\\]|${ml_bind_regex}):${port}$"; then
    fail "Host ML endpoint ${ml_bind_address}:${port} conflicts with an existing listener"
  else
    ok "Host ML endpoint port available: ${ml_bind_address}:${port}"
  fi
done

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

if python3 "$HOST_ROOT/scripts/host/dataset.py" --testbed "$testbed" --config-dir "$config_dir" check; then
  ok "generated PseudoDriver dataset"
else
  fail "generated PseudoDriver dataset is missing or invalid; run make dataset-generate"
fi

echo "SUMMARY failures=$failures warnings=$warnings"
[ "$failures" -eq 0 ]
