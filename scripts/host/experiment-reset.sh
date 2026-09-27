#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

action=${1:-plan}
testbed=${2:?usage: experiment-reset.sh plan|apply|verify testbed [config-dir]}
explicit_config=${3:-}
select_testbed_machines "$testbed"
case "$action" in plan|apply|verify) ;; *) echo "usage: experiment-reset.sh plan|apply|verify testbed [config-dir]" >&2; exit 2;; esac

config_dir=$(effective_config_dir "$testbed" "$explicit_config")
database_machine=$(config_guest_service_machine "$config_dir" mongodb)
vm_records=$(provider_runtime_state_records)
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$config_dir"
reset_identity=$(
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" "$config_dir" <<'PY'
import sys
from configlib import load_runtime_manifest, load_yaml, resolve_path

testbed = load_yaml(resolve_path(sys.argv[1]))
manifest = load_runtime_manifest(resolve_path(sys.argv[2]))
scope = manifest["runtime"]["resetScope"]
endpoint = testbed["coreServices"]["mongodb"]["endpoint"]
print(
    manifest["scenario"]["name"],
    "mongodb://{}:{}".format(endpoint["address"], endpoint["port"]),
    scope["nrf"]["database"],
    ",".join(scope["nrf"]["collections"]),
    scope["nrf"].get("nfType", "-"),
    ",".join(scope["nrf"].get("nfInstanceIds", [])) or "-",
    scope["adrf"]["database"],
    ",".join(scope["adrf"]["collections"]),
    scope["adrf"]["modelStorage"],
    scope["adrf"]["nfInstanceId"],
    manifest["seedRestoration"]["coordinatorContainer"],
    manifest["seedRestoration"]["artifactKey"],
)
PY
)
read -r scenario mongo_uri nrf_database nrf_collections nrf_nf_type nrf_instance_ids adrf_database adrf_collections storage_dir adrf_instance_id seed_coordinator seed_artifact_key <<<"$reset_identity"
project=$(ml_project_name)
service_lines=$(config_reset_host_containers "$config_dir")
volume_lines=$(config_reset_ml_volume_records "$config_dir")
[ -n "$service_lines" ] && [ -n "$volume_lines" ] || {
  echo "selected reset inventory is empty" >&2
  exit 1
}
mapfile -t services <<<"$service_lines"
mapfile -t volume_specs <<<"$volume_lines"

vm_state() {
  local wanted=$1 machine state
  while IFS='|' read -r machine state; do
    if [ "$machine" = "$wanted" ]; then
      printf '%s\n' "$state"
      return 0
    fi
  done <<<"$vm_records"
  echo "provider state omitted machine: $wanted" >&2
  return 1
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
  assert_volume_identity "$logical" "$physical" || return
  docker image inspect "$image" >/dev/null || return
  docker run --rm --user 0 --network none --read-only \
    --mount "type=volume,source=$physical,target=/state" \
    --entrypoint /bin/sh "$image" -c 'find /state -mindepth 1 -delete' || return
  echo "VOLUME logical=$logical physical=$physical cleared=yes retained=yes"
}

verify_volume() {
  local logical=$1 image=$2 physical="${project}_${logical}" remaining
  if docker volume inspect "$physical" >/dev/null 2>&1; then
    assert_volume_identity "$logical" "$physical" || return
    remaining=$(docker run --rm --user 0 --network none --read-only \
      --mount "type=volume,source=$physical,target=/state,readonly" \
      --entrypoint /bin/sh "$image" -c 'find /state -mindepth 1 -print -quit') || return
    [ -z "$remaining" ] || { echo "volume is not empty: $physical" >&2; return 1; }
  fi
}

wait_volume_batch() {
  local pid failed=false
  for pid in "$@"; do
    if ! wait "$pid"; then
      failed=true
    fi
  done
  ! $failed
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
    if [ "$state" != running ]; then
      echo "refusing reset because $machine is not running (state=${state:-unknown})" >&2
      return 1
    fi
    active=$(vssh "$machine" "systemctl list-units --state=active --no-legend '5g-nwdaf@*.service' | awk '{print \$1}'; true" 2>/dev/null | tr -d '\r')
    if [ -n "$active" ]; then
      echo "refusing reset while $machine experiment services are active:" >&2
      printf '%s\n' "$active" >&2
      return 1
    fi
  done
}

guest_reset() {
  local guest_action=$1 remote_shell=/tmp/5g-nwdaf-experiment-reset.sh remote_js=/tmp/5g-nwdaf-experiment-reset.js
  guest_upload "$HOST_ROOT/scripts/guest/experiment-reset.sh" "$remote_shell" "$database_machine"
  guest_upload "$HOST_ROOT/scripts/guest/experiment-reset.js" "$remote_js" "$database_machine"
  vssh "$database_machine" "sudo bash '$remote_shell' '$guest_action' '$mongo_uri' '$nrf_database' '$nrf_collections' '$nrf_nf_type' '$nrf_instance_ids' '$adrf_database' '$adrf_collections' '$storage_dir' '$adrf_instance_id' '$remote_js'; status=\$?; rm -f '$remote_shell' '$remote_js'; exit \$status"
}

echo "EXPERIMENT RESET action=$action scenario=$scenario config=$config_dir project=$project"
echo "SCOPE containers=retained images=retained network=retained volumes=retained"
echo "SEED coordinator=$seed_coordinator artifact_key=$seed_artifact_key canonical_source=retained"
for service in "${services[@]}"; do
  status=$(docker ps -a --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$service" --format '{{.Status}}')
  echo "CONTAINER service=$service status=${status:-absent} retained=yes"
done
container_inventory=$(docker ps -a --filter "label=com.docker.compose.project=$project" \
  --format '{{.Label "com.docker.compose.service"}}|{{.Status}}')
if [ "$action" = plan ]; then
  for spec in "${volume_specs[@]}"; do
    IFS='|' read -r logical image <<<"$spec"
    volume_state "$logical" "$image"
  done
fi
volume_inventory=$(docker volume ls --filter "label=com.docker.compose.project=$project" \
  --format '{{.Name}}|{{.Label "com.docker.compose.volume"}}')
unexpected_runtime=false
if ! inventory_findings=$(check_reset_runtime_inventory \
    "$service_lines" "$volume_lines" "$container_inventory" "$volume_inventory" "$project"); then
  unexpected_runtime=true
fi
[ -z "$inventory_findings" ] || printf '%s\n' "$inventory_findings"

core_state=$(vm_state "$database_machine")
echo "GUEST machine=$database_machine state=${core_state:-unknown}"
if [ "$action" = plan ]; then
  echo "GUEST_SCOPE adrf_database=$adrf_database collections=$adrf_collections"
  echo "GUEST_SCOPE nrf_database=$nrf_database collections=$nrf_collections nf_type=$nrf_nf_type instance_ids=$nrf_instance_ids"
  echo "GUEST_SCOPE model_storage=$storage_dir"
  if [ "$core_state" = running ]; then
    guest_reset plan
  else
    echo "GUEST_STATE unavailable=$database_machine-not-running"
  fi
  printf 'RESET_COMMAND make reset CONFIG_DIR=%q RESET_CONFIRM=%q\n' "$config_dir" "$scenario"
  echo "PLAN_ONLY no experiment state was deleted"
  exit 0
fi

if $unexpected_runtime; then
  echo "refusing reset while unexpected project containers or volumes exist" >&2
  exit 1
fi

assert_runtime_stopped
assert_guest_runtime_identity "$config_dir"
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
  volume_pids=()
  for spec in "${volume_specs[@]}"; do
    IFS='|' read -r logical image <<<"$spec"
    clear_volume "$logical" "$image" &
    volume_pids+=("$!")
    if [ "${#volume_pids[@]}" -ge 4 ]; then
      wait_volume_batch "${volume_pids[@]}"
      volume_pids=()
    fi
  done
  if [ "${#volume_pids[@]}" -gt 0 ]; then
    wait_volume_batch "${volume_pids[@]}"
  fi
  echo "RESET_APPLIED scenario=$scenario; run experiment-reset-verify before startup"
else
  guest_reset verify
  volume_pids=()
  for spec in "${volume_specs[@]}"; do
    IFS='|' read -r logical image <<<"$spec"
    verify_volume "$logical" "$image" &
    volume_pids+=("$!")
    if [ "${#volume_pids[@]}" -ge 4 ]; then
      wait_volume_batch "${volume_pids[@]}"
      volume_pids=()
    fi
  done
  if [ "${#volume_pids[@]}" -gt 0 ]; then
    wait_volume_batch "${volume_pids[@]}"
  fi
  echo "RESET_VERIFIED scenario=$scenario state=empty containers=retained volumes=retained"
fi
