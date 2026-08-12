#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

action=${1:-plan}
testbed=${2:-testbed.yaml}
explicit_config=${3:-}
case "$action" in plan|apply|verify) ;; *) echo "usage: experiment-reset.sh plan|apply|verify [testbed] [config-dir]" >&2; exit 2;; esac

config_dir=$(effective_config_dir "$testbed" "$explicit_config")
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$config_dir"
read -r scenario mongo_uri nrf_database adrf_database storage_dir adrf_instance_id < <(
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" "$config_dir" <<'PY'
import sys
from configlib import load_yaml, resolve_path

testbed = load_yaml(resolve_path(sys.argv[1]))
manifest = load_yaml(resolve_path(sys.argv[2]) / "manifest.yaml")
endpoint = testbed["coreServices"]["mongodb"]["endpoint"]
adrf = testbed["coreServices"]["adrf"]
print(
    manifest["scenario"]["name"],
    "mongodb://{}:{}".format(endpoint["address"], endpoint["port"]),
    testbed["coreServices"]["mongodb"]["database"],
    adrf["mongodb"]["database"],
    adrf["modelStorage"]["localDirectory"],
    adrf["nfInstanceId"],
)
PY
)
project=$(ml_project_name)
services=(pyanlf-a pyanlf-b pymtlf-a pymtlf-b pymtlf-c)
volume_specs=(
  "pyanlf-a-artifacts:5g-nwdaf-infrastructure/pyanlf:local"
  "pyanlf-b-artifacts:5g-nwdaf-infrastructure/pyanlf:local"
  "pymtlf-a-data:5g-nwdaf-infrastructure/pymtlf:local"
  "pymtlf-b-data:5g-nwdaf-infrastructure/pymtlf:local"
  "pymtlf-c-data:5g-nwdaf-infrastructure/pymtlf:local"
)

vm_state() {
  local machine=$1
  (cd "$HOST_ROOT" && vagrant status "$machine" --machine-readable 2>/dev/null) |
    awk -F, '$3 == "state" {state=$4} END {print state}'
}

assert_volume_identity() {
  local logical=$1 physical=$2 actual_project actual_logical
  actual_project=$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$physical")
  actual_logical=$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$physical")
  if [ "$actual_project" != "$project" ] || [ "$actual_logical" != "$logical" ]; then
    echo "refusing volume with unexpected identity: $physical project=$actual_project logical=$actual_logical" >&2
    return 1
  fi
}

volume_state() {
  local logical=$1 image=$2 physical="${project}_${logical}"
  if ! docker volume inspect "$physical" >/dev/null 2>&1; then
    echo "VOLUME logical=$logical physical=$physical state=absent"
    return 0
  fi
  assert_volume_identity "$logical" "$physical"
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "VOLUME logical=$logical physical=$physical state=present entries=unavailable image=$image"
    return 0
  fi
  state=$(docker run --rm --user 0 --network none --read-only \
    --mount "type=volume,source=$physical,target=/state,readonly" \
    --entrypoint /bin/sh "$image" -c \
    'entries=$(find /state -mindepth 1 -print | wc -l); kib=$(du -sk /state | awk "{print \$1}"); printf "entries=%s kib=%s" "$entries" "$kib"')
  echo "VOLUME logical=$logical physical=$physical state=present $state"
}

clear_volume() {
  local logical=$1 image=$2 physical="${project}_${logical}"
  if ! docker volume inspect "$physical" >/dev/null 2>&1; then
    echo "VOLUME logical=$logical physical=$physical state=absent retained=yes"
    return 0
  fi
  assert_volume_identity "$logical" "$physical"
  docker image inspect "$image" >/dev/null
  docker run --rm --user 0 --network none --read-only \
    --mount "type=volume,source=$physical,target=/state" \
    --entrypoint /bin/sh "$image" -c 'find /state -mindepth 1 -delete'
  echo "VOLUME logical=$logical physical=$physical cleared=yes retained=yes"
}

assert_runtime_stopped() {
  local running machine state active
  running=$(docker ps -q --filter "label=com.docker.compose.project=$project")
  if [ -n "$running" ]; then
    echo "refusing reset while ML project $project has running containers" >&2
    docker ps --filter "label=com.docker.compose.project=$project" --format '  {{.Names}} {{.Status}}' >&2
    return 1
  fi
  for machine in "${MACHINES[@]}"; do
    state=$(vm_state "$machine")
    [ "$state" = running ] || continue
    if [ "$machine" = core ]; then
      active=$(vssh core "for unit in mongodb nrf nssf udr udm ausf pcf amf smf adrf nwdaf-c; do systemctl is-active --quiet 5g-nwdaf@\$unit.service && echo \$unit; done; systemctl is-active --quiet 5g-nwdaf-consumer.service && echo consumer; true" 2>/dev/null | tr -d '\r')
    else
      active=$(vssh "$machine" "systemctl list-units --state=active --no-legend '5g-nwdaf@*.service' | awk '{print \$1}'" 2>/dev/null | tr -d '\r')
    fi
    if [ -n "$active" ]; then
      echo "refusing reset while $machine experiment services are active:" >&2
      printf '%s\n' "$active" >&2
      return 1
    fi
  done
}

guest_reset() {
  local guest_action=$1 remote_shell=/tmp/5g-nwdaf-experiment-reset.sh remote_js=/tmp/5g-nwdaf-experiment-reset.js
  (cd "$HOST_ROOT" && vagrant upload "$HOST_ROOT/scripts/guest/experiment-reset.sh" "$remote_shell" core)
  (cd "$HOST_ROOT" && vagrant upload "$HOST_ROOT/scripts/guest/experiment-reset.js" "$remote_js" core)
  vssh core "sudo bash '$remote_shell' '$guest_action' '$mongo_uri' '$nrf_database' '$adrf_database' '$storage_dir' '$adrf_instance_id' '$remote_js'; status=\$?; rm -f '$remote_shell' '$remote_js'; exit \$status"
}

echo "EXPERIMENT RESET action=$action scenario=$scenario config=$config_dir project=$project"
echo "SCOPE containers=retained images=retained network=retained volumes=retained"
for service in "${services[@]}"; do
  status=$(docker ps -a --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$service" --format '{{.Status}}')
  echo "CONTAINER service=$service status=${status:-absent} retained=yes"
done
for spec in "${volume_specs[@]}"; do
  IFS=: read -r logical image <<<"$spec"
  volume_state "$logical" "$image"
done

core_state=$(vm_state core)
echo "GUEST machine=core state=${core_state:-unknown}"
if [ "$action" = plan ]; then
  echo "GUEST_SCOPE adrf_database=$adrf_database collections=data_store_records,mlmodel_store_records"
  echo "GUEST_SCOPE nrf_database=$nrf_database collections=NfProfile,urilist filter=nfType:ADRF"
  echo "GUEST_SCOPE model_storage=$storage_dir"
  if [ "$core_state" = running ]; then
    guest_reset plan
  else
    echo "GUEST_STATE unavailable=core-not-running"
  fi
  printf 'RESET_COMMAND make reset CONFIG_DIR=%q RESET_CONFIRM=%q\n' "$config_dir" "$scenario"
  echo "PLAN_ONLY no experiment state was deleted"
  exit 0
fi

assert_runtime_stopped
if [ "$core_state" != running ]; then
  echo "Core VM must be running for $action" >&2
  exit 1
fi

if [ "$action" = apply ]; then
  if [ "${RESET_CONFIRM:-}" != "$scenario" ]; then
    echo "refusing reset: set RESET_CONFIRM=$scenario after reviewing experiment-reset-plan" >&2
    exit 1
  fi
  guest_reset apply
  for spec in "${volume_specs[@]}"; do
    IFS=: read -r logical image <<<"$spec"
    clear_volume "$logical" "$image"
  done
  echo "RESET_APPLIED scenario=$scenario; run experiment-reset-verify before startup"
else
  guest_reset verify
  for spec in "${volume_specs[@]}"; do
    IFS=: read -r logical image <<<"$spec"
    physical="${project}_${logical}"
    if docker volume inspect "$physical" >/dev/null 2>&1; then
      assert_volume_identity "$logical" "$physical"
      remaining=$(docker run --rm --user 0 --network none --read-only \
        --mount "type=volume,source=$physical,target=/state,readonly" \
        --entrypoint /bin/sh "$image" -c 'find /state -mindepth 1 -print -quit')
      [ -z "$remaining" ] || { echo "volume is not empty: $physical" >&2; exit 1; }
    fi
  done
  echo "RESET_VERIFIED scenario=$scenario state=empty containers=retained volumes=retained"
fi
