#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

action=${1:-show}
testbed=${2:-testbed.yaml}
explicit_config=${3:-}
case "$action" in validate|plan|apply|show|clear) ;; *) echo "usage: subscriber-data.sh validate|plan|apply|show|clear [testbed] [config-dir]" >&2; exit 2;; esac

config_dir=$(effective_config_dir "$testbed" "$explicit_config")
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$config_dir"
subscriber_fixture="$HOST_ROOT/fixtures/full-core/ue-subscribers.json"
group_fixture="$HOST_ROOT/fixtures/full-core/group-memberships.json"
fixture_hash=$(
  sha256sum "$subscriber_fixture" "$group_fixture" |
    sha256sum |
    awk '{print $1}'
)
read -r mongo_uri mongo_database < <(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_path
definition = load_yaml(resolve_path(sys.argv[1]))
endpoint = definition["coreServices"]["mongodb"]["endpoint"]
print("mongodb://{}:{}".format(endpoint["address"], endpoint["port"]), definition["coreServices"]["mongodb"]["database"])
PY
)

guest_root=/opt/5g-nwdaf-infrastructure/source
echo "SUBSCRIBER DATA action=$action database=$mongo_database hash=$fixture_hash"
printf -v command 'ACTION=%q SUBSCRIBER_FIXTURE=%q GROUP_FIXTURE=%q mongosh --quiet %q --file %q' \
  "$action" \
  "$guest_root/fixtures/full-core/ue-subscribers.json" \
  "$guest_root/fixtures/full-core/group-memberships.json" \
  "$mongo_uri/$mongo_database" \
  "$guest_root/scripts/guest/subscriber-data.js"
vssh core "$command"
