#!/usr/bin/env bash
set -euo pipefail

action=${1:?usage: experiment-reset.sh plan|apply|verify mongo-uri nrf-db adrf-db storage-dir instance-id js-file}
mongo_uri=${2:?missing MongoDB URI}
nrf_database=${3:?missing NRF database}
adrf_database=${4:?missing ADRF database}
storage_dir=${5:?missing ADRF model storage directory}
adrf_instance_id=${6:?missing ADRF NF instance ID}
js_file=${7:?missing reset JavaScript}

case "$action" in plan|apply|verify) ;; *) echo "invalid reset action: $action" >&2; exit 2;; esac
[[ "$nrf_database" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid NRF database name" >&2; exit 2; }
[[ "$adrf_database" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid ADRF database name" >&2; exit 2; }
case "$storage_dir" in
  /var/lib/5g-nwdaf-infrastructure/adrf/*) ;;
  *) echo "refusing unsafe ADRF storage path: $storage_dir" >&2; exit 2;;
esac
test "$(id -u)" -eq 0 || { echo "experiment reset requires root" >&2; exit 1; }
test -f "$js_file" || { echo "missing reset JavaScript: $js_file" >&2; exit 1; }
mongo_host=${mongo_uri#mongodb://}
mongo_host=${mongo_host%%:*}
if ! ip -o -4 address show | awk '{sub(/\/.*/, "", $4); print $4}' | grep -Fxq "$mongo_host"; then
  echo "MongoDB bind address $mongo_host is not active in Core; reconcile the selected config network before reset" >&2
  echo "repair: sudo /usr/local/libexec/5g-nwdaf-infrastructure/network-setup --verify || sudo systemctl restart 5g-nwdaf-network.service" >&2
  exit 1
fi

if [ "$action" != plan ]; then
  active=()
  for unit in nrf nssf udr udm ausf pcf amf smf adrf nwdaf-c; do
    systemctl is-active --quiet "5g-nwdaf@$unit.service" && active+=("$unit")
  done
  systemctl is-active --quiet 5g-nwdaf-consumer.service && active+=("consumer")
  if [ "${#active[@]}" -ne 0 ]; then
    echo "refusing reset while Core services are active: ${active[*]}" >&2
    exit 1
  fi
fi

mongodb_was_active=false
if systemctl is-active --quiet 5g-nwdaf@mongodb.service; then
  mongodb_was_active=true
  if [ "$action" != plan ]; then
    echo "refusing reset while MongoDB is already active" >&2
    exit 1
  fi
else
  systemctl start --no-block 5g-nwdaf@mongodb.service
fi

cleanup() {
  if [ "$mongodb_was_active" = false ]; then
    systemctl stop 5g-nwdaf@mongodb.service || true
  fi
}
trap cleanup EXIT

for _attempt in $(seq 1 30); do
  mongosh --quiet "$mongo_uri/$nrf_database" --eval 'quit(db.runCommand({ping: 1}).ok ? 0 : 1)' >/dev/null 2>&1 && break
  sleep 1
done
if ! mongosh --quiet "$mongo_uri/$nrf_database" --eval 'quit(db.runCommand({ping: 1}).ok ? 0 : 1)' >/dev/null; then
  echo "MongoDB did not become ready within 30 seconds" >&2
  journalctl -u 5g-nwdaf@mongodb.service -n 30 --no-pager >&2 || true
  exit 1
fi

ACTION="$action" ADRF_DATABASE="$adrf_database" ADRF_INSTANCE_ID="$adrf_instance_id" \
  mongosh --quiet "$mongo_uri/$nrf_database" --file "$js_file"

model_entries=0
if [ -d "$storage_dir" ]; then
  model_entries=$(find "$storage_dir" -mindepth 1 -print | wc -l)
fi
echo "ADRF_MODELS action=$action directory=$storage_dir entries=$model_entries"

if [ "$action" = apply ] && [ -d "$storage_dir" ]; then
  find "$storage_dir" -mindepth 1 -delete
  echo "ADRF_MODELS removed=$model_entries"
elif [ "$action" = verify ] && [ "$model_entries" -ne 0 ]; then
  echo "ADRF model storage is not empty" >&2
  exit 1
fi
