#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

action=${1:-show}
testbed=${2:-testbed.yaml}
explicit_config=${3:-}
case "$action" in validate|plan|apply|show|clear) ;; *) echo "usage: subscriber-data.sh validate|plan|apply|show|clear [testbed] [config-dir]" >&2; exit 2;; esac

config_dir=$(effective_config_dir "$testbed" "$explicit_config")
mapfile -t fixture_paths < <(
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import load_yaml, resolve_path

directory = resolve_path(sys.argv[1]).resolve()
metadata = load_yaml(directory / "manifest.yaml")["subscriberData"]
for name in ("subscribers", "groups"):
    path = (directory / metadata[name]).resolve()
    if directory not in path.parents or not path.is_file():
        raise SystemExit("invalid config subscriber fixture: " + str(path))
    print(path)
PY
)
subscriber_fixture=${fixture_paths[0]}
group_fixture=${fixture_paths[1]}
fixture_hash=$(
  {
    sha256sum "$subscriber_fixture" | awk '{print $1}'
    sha256sum "$group_fixture" | awk '{print $1}'
  } |
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

echo "SUBSCRIBER DATA action=$action database=$mongo_database hash=$fixture_hash"
remote_suffix="${fixture_hash:0:16}-$$"
remote_subscriber="/tmp/5g-nwdaf-subscribers-${remote_suffix}.json"
remote_group="/tmp/5g-nwdaf-groups-${remote_suffix}.json"
uploaded=false
cleanup() {
  if $uploaded; then
    vssh core "rm -f '$remote_subscriber' '$remote_group'" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
(cd "$HOST_ROOT" && provider_vagrant upload "$subscriber_fixture" "$remote_subscriber" core)
uploaded=true
(cd "$HOST_ROOT" && provider_vagrant upload "$group_fixture" "$remote_group" core)
vssh core "chmod 600 '$remote_subscriber' '$remote_group'"
printf -v command 'ACTION=%q SUBSCRIBER_FIXTURE=%q GROUP_FIXTURE=%q mongosh --quiet %q --file %q' \
  "$action" \
  "$remote_subscriber" \
  "$remote_group" \
  "$mongo_uri/$mongo_database" \
  "/usr/local/libexec/5g-nwdaf-infrastructure/subscriber-data.js"
vssh core "$command"
trap - EXIT
cleanup
