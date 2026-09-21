#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

mode=wait
if [ "${1:-}" = --once ]; then
  mode=once
  shift
fi
testbed=${1:?usage: registration-check.sh [--once] testbed config-dir}
config_dir=${2:?usage: registration-check.sh [--once] testbed config-dir}
select_testbed_machines "$testbed"
database_machine=$(config_guest_service_machine "$config_dir" mongodb)
read -r mongo_uri database instance_ids < <(
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" "$config_dir" <<'PY'
import sys
from configlib import load_runtime_manifest, load_yaml, resolve_path
testbed = load_yaml(resolve_path(sys.argv[1]))
runtime = load_runtime_manifest(resolve_path(sys.argv[2]))["runtime"]
scope = runtime["resetScope"]["nrf"]
ids = []
if "nfInstanceIds" in scope:
    active_units = {item["unit"] for item in runtime["guestServices"]}
    ids = [
        item["nfInstanceId"] for item in runtime["nwdafs"]
        if item["unit"] in active_units
    ]
    ids.append(runtime["resetScope"]["adrf"]["nfInstanceId"])
endpoint = testbed["coreServices"]["mongodb"]["endpoint"]
print(
    "mongodb://{}:{}".format(endpoint["address"], endpoint["port"]),
    scope["database"],
    ",".join(ids) or "-",
)
PY
)
[ "$instance_ids" != - ] || { echo "NRF exact registration check skipped (legacy scope)"; exit 0; }

expected_count=$(awk -F, '{print NF}' <<<"$instance_ids")
attempts=120
[ "$mode" = once ] && attempts=1
for attempt in $(seq 1 "$attempts"); do
  if vssh "$database_machine" "mongosh --quiet '$mongo_uri/$database' --eval \"const ids='$instance_ids'.split(','); quit(ids.every(id => db.NfProfile.countDocuments({nfInstanceId:id}) === 1) ? 0 : 1)\"" >/dev/null 2>&1; then
    echo "NRF REGISTRATIONS selected=$expected_count state=ready"
    exit 0
  fi
  [ "$attempt" -eq "$attempts" ] || sleep 1
done
echo "NRF registrations did not reach the exact selected instance inventory" >&2
vssh "$database_machine" "mongosh --quiet '$mongo_uri/$database' --eval \"printjson(db.NfProfile.find({nfInstanceId:{\\\$in:'$instance_ids'.split(',')}},{_id:0,nfInstanceId:1,nfType:1}).toArray())\"" >&2 || true
exit 1
