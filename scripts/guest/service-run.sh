#!/usr/bin/env bash
set -euo pipefail

service=${1:?service instance is required}
config=/etc/5g-nwdaf-infrastructure/active
work=/opt/5g-nwdaf-infrastructure/work
bin=/usr/local/libexec/5g-nwdaf-infrastructure/bin
machine=$(cat /etc/5g-nwdaf-infrastructure/machine)
test -L "$config" || { echo "no active config set" >&2; exit 1; }

as_runtime() { exec setpriv --reuid=5g-nwdaf --regid=5g-nwdaf --init-groups "$@"; }
go_nf() { as_runtime "$bin/$1" -c "$config/$2"; }

case "$service:$machine" in
  mongodb:core) as_runtime /usr/bin/mongod --dbpath /var/lib/5g-nwdaf-infrastructure/mongodb --bind_ip 192.168.57.18 --port 27017 --wiredTigerCacheSizeGB 0.25 --quiet ;;
  nrf:core|nssf:core|udr:core|udm:core|ausf:core|pcf:core|amf:core|adrf:core)
    go_nf "$service" "${service}cfg.yaml" ;;
  smf:core) as_runtime "$bin/smf" -c "$config/smfcfg.yaml" -u "$config/uerouting.yaml" ;;
  nwdaf-c:core) go_nf nwdaf nwdafcfg-c.yaml ;;
  upf-a:path-a) exec "$bin/upf" -c "$config/upfcfg-a.yaml" ;;
  upf-b:path-b) exec "$bin/upf" -c "$config/upfcfg-b.yaml" ;;
  nwdaf-a:path-a) go_nf nwdaf nwdafcfg-a.yaml ;;
  nwdaf-b:path-b) go_nf nwdaf nwdafcfg-b.yaml ;;
  gnb-a:path-a) exec "$work/ueransim/build/nr-gnb" -c "$config/ueransim/gnb-a.yaml" ;;
  gnb-b:path-b) exec "$work/ueransim/build/nr-gnb" -c "$config/ueransim/gnb-b.yaml" ;;
  ue[1-3]:path-a|ue[4-6]:path-b) exec "$work/ueransim/build/nr-ue" -c "$config/ueransim/${service}.yaml" ;;
  *) echo "service $service is not assigned to $machine" >&2; exit 2;;
esac
