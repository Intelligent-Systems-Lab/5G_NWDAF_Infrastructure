#!/usr/bin/env bash
set -euo pipefail

service=${1:?service instance is required}
config=/etc/5g-nwdaf-infrastructure/active
work=/opt/5g-nwdaf-infrastructure/work
bin=/usr/local/libexec/5g-nwdaf-infrastructure/bin
machine=$(cat /etc/5g-nwdaf-infrastructure/machine)
test -L "$config" || { echo "no active config set" >&2; exit 1; }

if [ "$machine:$service" = core:webconsole ]; then
  service_kind=optional
else
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
fi

as_runtime() { exec setpriv --reuid=5g-nwdaf --regid=5g-nwdaf --init-groups "$@"; }
go_nf() { as_runtime "$bin/$1" -c "$config/$2"; }
upf() {
  local name=$1
  dataset=/var/lib/5g-nwdaf-infrastructure/datasets/active
  test -L "$dataset" || { echo "no active PseudoDriver dataset" >&2; exit 1; }
  test -f "$dataset/traffic.parquet" -a -f "$dataset/manifest.json" || {
    echo "active PseudoDriver dataset is incomplete" >&2
    exit 1
  }
  exec "$bin/upf" -c "$config/$name"
}

case "$service_kind:$service" in
  core:mongodb) as_runtime /usr/bin/mongod --dbpath /var/lib/5g-nwdaf-infrastructure/mongodb --bind_ip 192.168.57.18 --port 27017 --wiredTigerCacheSizeGB 0.25 --quiet ;;
  core:nrf|core:nssf|core:udr|core:udm|core:ausf|core:pcf|core:amf|core:adrf)
    go_nf "$service" "${service}cfg.yaml" ;;
  core:smf) as_runtime "$bin/smf" -c "$config/smfcfg.yaml" -u "$config/uerouting.yaml" ;;
  optional:webconsole)
    artifact=/var/lib/5g-nwdaf-infrastructure/webconsole/current
    test -x "$artifact/webconsole" -a -f "$artifact/public/index.html" || {
      echo "WebConsole artifact is missing or incomplete" >&2
      exit 1
    }
    cd "$artifact"
    as_runtime "$artifact/webconsole" -c "$config/webuicfg.yaml"
    ;;
  nwdaf:nwdaf-*) suffix=${service#nwdaf-}; go_nf nwdaf "nwdafcfg-${suffix}.yaml" ;;
  upf:upf-*) suffix=${service#upf-}; upf "upfcfg-${suffix}.yaml" ;;
  gnb:gnb-*) exec "$work/ueransim/build/nr-gnb" -c "$config/ueransim/${service}.yaml" ;;
  ue:ue[0-9]*) exec "$work/ueransim/build/nr-ue" -c "$config/ueransim/${service}.yaml" ;;
  *) echo "unsupported service mapping $service_kind/$service on $machine" >&2; exit 2;;
esac
