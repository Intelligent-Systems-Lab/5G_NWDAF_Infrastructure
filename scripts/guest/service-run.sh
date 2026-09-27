#!/usr/bin/env bash
set -euo pipefail

service=${1:?service instance is required}
config=/etc/5g-nwdaf-infrastructure/active
bin=/usr/local/libexec/5g-nwdaf-infrastructure/bin
machine=$(cat /etc/5g-nwdaf-infrastructure/machine)
test -L "$config" || { echo "no active config set" >&2; exit 1; }

service_kind=$(python3 - "$config/manifest.yaml" "$machine" "$service" <<'PY'
import sys
import yaml
with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = yaml.safe_load(stream) or {}
matches = [
    item for item in manifest.get("runtime", {}).get("guestServices", [])
    if item.get("machine") == sys.argv[2] and item.get("unit") == sys.argv[3]
]
if len(matches) != 1:
    raise SystemExit("service {} is not assigned to {}".format(sys.argv[3], sys.argv[2]))
print(matches[0].get("kind", ""))
PY
)

as_runtime() { exec setpriv --reuid=5g-nwdaf --regid=5g-nwdaf --init-groups "$@"; }
go_nf() { as_runtime "$bin/$1" -c "$config/$2"; }
case "$service_kind:$service" in
  core:mongodb) as_runtime /usr/bin/mongod --dbpath /var/lib/5g-nwdaf-infrastructure/mongodb --bind_ip 192.168.57.18 --port 27017 --wiredTigerCacheSizeGB 0.25 --quiet ;;
  core:nrf|core:adrf)
    go_nf "$service" "${service}cfg.yaml" ;;
  nwdaf:nwdaf-*) suffix=${service#nwdaf-}; go_nf nwdaf "nwdafcfg-${suffix}.yaml" ;;
  *) echo "unsupported service mapping $service_kind/$service on $machine" >&2; exit 2;;
esac
