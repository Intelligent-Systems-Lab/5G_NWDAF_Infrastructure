#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: provisioning-check.sh testbed config-dir}
config_dir=${2:?usage: provisioning-check.sh testbed config-dir}
select_testbed_machines "$testbed"

snapshot_dir=$(mktemp -d -t 5g-nwdaf-provisioning.XXXXXX)
cleanup() { rm -rf -- "$snapshot_dir"; }
trap cleanup EXIT

for machine in "${MACHINES[@]}"; do
  vssh "$machine" "sudo cat /etc/5g-nwdaf-infrastructure/provisioning-manifest.yaml" \
    >"$snapshot_dir/$machine.yaml"
done

PYTHONPATH="$HOST_ROOT/scripts/host" python3 - \
  "$testbed" "$config_dir" "$HOST_ROOT/components.lock.yaml" "$snapshot_dir" <<'PY'
import sys
from pathlib import Path

from configlib import load_runtime_manifest, load_yaml, resolve_path


def component_path(service):
    unit = service["unit"]
    kind = service["kind"]
    if unit == "mongodb":
        return None
    if kind == "nwdaf":
        return "NFs/nwdaf"
    if kind == "upf":
        return "NFs/upf"
    if kind in {"gnb", "ue"}:
        return "RAN/UERANSIM"
    if kind == "core" and unit in {
        "nrf", "nssf", "udr", "udm", "ausf", "pcf", "amf", "smf", "adrf"
    }:
        return "NFs/" + unit
    raise SystemExit("unsupported Guest service for component identity: " + unit)


testbed_path, config_path, lock_path, snapshot_path = sys.argv[1:]
definition = load_yaml(resolve_path(testbed_path))
runtime = load_runtime_manifest(resolve_path(config_path))["runtime"]
locked = {
    item["path"]: item["commit"]
    for item in load_yaml(Path(lock_path))["components"]
}
machines = list(definition["machines"])
services = runtime["guestServices"]
for machine in machines:
    paths = {
        path
        for service in services
        if service["machine"] == machine
        if (path := component_path(service)) is not None
    }
    if not paths:
        raise SystemExit("component revision inventory is empty for " + machine)
    expected = [
        {"path": path, "revision": locked[path]}
        for path in sorted(paths)
    ]
    actual = load_yaml(Path(snapshot_path) / (machine + ".yaml"))
    if actual.get("machine") != machine:
        raise SystemExit("provisioning manifest machine mismatch for " + machine)
    if actual.get("components") != expected:
        raise SystemExit("provisioned component revisions differ for " + machine)
    print("PROVISIONING machine={} components={} state=matched".format(machine, len(expected)))
PY
