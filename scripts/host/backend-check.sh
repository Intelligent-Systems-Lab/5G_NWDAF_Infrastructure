#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: backend-check.sh testbed config-dir}
config_dir=${2:?usage: backend-check.sh testbed config-dir}
select_testbed_machines "$testbed"
records=$(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" "$config_dir" <<'PY'
import sys
from configlib import load_runtime_manifest, load_yaml, resolve_path
testbed = load_yaml(resolve_path(sys.argv[1]))
runtime = load_runtime_manifest(resolve_path(sys.argv[2]))["runtime"]
services = testbed["mlRuntime"]["services"]
host = testbed["mlRuntime"]["advertisedAddress"]
active_units = {item["unit"] for item in runtime["guestServices"]}
for item in runtime["nwdafs"]:
    if item["unit"] not in active_units:
        continue
    backend = item["backends"]["mtlf"]
    print(
        item["machine"], item["unit"], item["nfInstanceId"],
        "http://{}:{}/internal/v1/nwdaf-context".format(
            item["internalApi"]["mtlf"]["address"], item["internalApi"]["mtlf"]["port"]
        ),
        "http://{}:{}/health/ready".format(host, services[backend]["publishedPort"]),
        sep="|",
    )
PY
)
[ -n "$records" ] || { echo "NWDAF/backend reachability inventory is empty" >&2; exit 1; }

check_backend() {
  local machine=$1 unit=$2 instance_id=$3 nwdaf_context=$4 backend_health=$5
  python3 - "$nwdaf_context" "$instance_id" <<'PY'
import json, sys, urllib.request
url, expected = sys.argv[1:]
with urllib.request.urlopen(url, timeout=5) as response:
    body = json.load(response)
if response.status != 200 or body.get("nfInstanceId") != expected or not body.get("processInstanceId"):
    raise SystemExit("NWDAF context identity mismatch: " + url)
PY
  vssh "$machine" "curl --fail --silent --show-error --max-time 5 '$backend_health'" >/dev/null
  echo "BACKEND unit=$unit host_to_nwdaf=ready guest_to_pymtlf=ready"
}

wait_backend_batch() {
  local pid failed=false
  for pid in "$@"; do
    if ! wait "$pid"; then
      failed=true
    fi
  done
  ! $failed
}

backend_pids=()
while IFS='|' read -r machine unit instance_id nwdaf_context backend_health; do
  check_backend "$machine" "$unit" "$instance_id" "$nwdaf_context" "$backend_health" &
  backend_pids+=("$!")
  if [ "${#backend_pids[@]}" -ge 4 ]; then
    wait_backend_batch "${backend_pids[@]}"
    backend_pids=()
  fi
done <<<"$records"
if [ "${#backend_pids[@]}" -gt 0 ]; then
  wait_backend_batch "${backend_pids[@]}"
fi
