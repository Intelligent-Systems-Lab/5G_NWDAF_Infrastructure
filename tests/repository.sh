#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/host/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
make_config="$HOST_ROOT/config/local/repository-interface-test"
custom_scenario_root="$HOST_ROOT/.generated/tests/experiments/repository-interface-test"
webconsole_root=$(mktemp -d)
cleanup() {
  rm -rf "$make_config"
  rm -rf "$custom_scenario_root"
  rm -rf "$webconsole_root"
}
trap cleanup EXIT
check_args=(--testbed "$testbed")
if [ -n "$explicit_config" ]; then
  check_args+=(--config-dir "$explicit_config")
fi

while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find "$HOST_ROOT/scripts" "$HOST_ROOT/tests" -type f -name '*.sh' -print0)
echo "PASS shell syntax"

(
  provider_fixture=$(mktemp -d)
  trap 'rm -rf "$provider_fixture"' EXIT
  fixture_log="$provider_fixture/invocation.log"
  printf '%s\n' '#!/usr/bin/env bash' 'printf "%s|%s\\n" "$(basename "$0")" "$*" >>"$PROVIDER_FIXTURE_LOG"' \
    >"$provider_fixture/vagrant"
  cp "$provider_fixture/vagrant" "$provider_fixture/VBoxManage"
  chmod 0755 "$provider_fixture/vagrant" "$provider_fixture/VBoxManage"
  export PATH="$provider_fixture:$PATH"
  export PROVIDER_FIXTURE_LOG="$fixture_log"

  provider_host_context_available /dev/null
  touch "$provider_fixture/not-a-device"
  if provider_host_context_available "$provider_fixture/not-a-device"; then
    echo "provider host-context check accepted a regular file" >&2
    exit 1
  fi

  require_provider_host_context() { return 126; }
  if provider_vagrant status; then
    echo "provider wrapper bypassed a rejected host context" >&2
    exit 1
  fi
  if provider_vboxmanage list vms; then
    echo "VirtualBox wrapper bypassed a rejected host context" >&2
    exit 1
  fi
  if [ -e "$fixture_log" ]; then
    echo "provider fixture started before the host-context guard passed" >&2
    exit 1
  fi

  require_provider_host_context() { :; }
  provider_vagrant validate
  provider_vboxmanage list vms
  grep -Fx 'vagrant|validate' "$fixture_log" >/dev/null
  grep -Fx 'VBoxManage|list vms' "$fixture_log" >/dev/null
)
echo "PASS provider host-context guard and mock wrapper"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  transport_fixture=$(mktemp -d)
  trap 'rm -rf "$transport_fixture"' EXIT
  export XDG_RUNTIME_DIR="$transport_fixture/runtime"
  export GUEST_TRANSPORT_DIR="$transport_fixture/transport"
  mkdir -p "$GUEST_TRANSPORT_DIR"
  printf '%s\n' 'Host core' >"$GUEST_TRANSPORT_DIR/core.conf"
  require_provider_host_context() { :; }
  provider_vagrant() { echo "unexpected provider fallback" >&2; }
  if vssh core true >/dev/null 2>&1 || guest_upload /unused /tmp/unused core >/dev/null 2>&1; then
    echo "Guest transport fell back after its master disappeared" >&2
    exit 1
  fi
)
echo "PASS missing Guest SSH master fails without provider fallback"

if selection_error=$(require_testbed_selection "" 2>&1); then
  echo "explicit testbed selection guard accepted an empty value" >&2
  exit 1
fi
[[ "$selection_error" == *'TESTBED must select an explicit testbed definition'* ]]
require_testbed_selection "$testbed"
echo "PASS explicit testbed selection guard"

assert_selection_rejected() {
  local label=$1 expected=$2
  shift 2
  local output
  if output=$("$@" 2>&1); then
    echo "$label accepted a missing TESTBED selection" >&2
    exit 1
  fi
  if [[ "$output" != *"$expected"* ]]; then
    echo "$label failed for an unrelated reason: $output" >&2
    exit 1
  fi
}

grep -Fx 'TESTBED ?= testbed.protocol-hierarchical.yaml' "$HOST_ROOT/Makefile" >/dev/null
make --no-print-directory -C "$HOST_ROOT" config-validate >/dev/null
assert_selection_rejected \
  "experiment start" \
  "usage: experiment-start.sh testbed" \
  "$HOST_ROOT/scripts/host/experiment-start.sh"
assert_selection_rejected \
  "experiment stop" \
  "usage: experiment-stop.sh testbed" \
  "$HOST_ROOT/scripts/host/experiment-stop.sh"
assert_selection_rejected \
  "experiment reset" \
  "usage: experiment-reset.sh plan|apply|verify testbed" \
  "$HOST_ROOT/scripts/host/experiment-reset.sh" plan
echo "PASS Make uses canonical TESTBED default while direct deployment scripts require selection"

"$HOST_ROOT/tests/provider-runtime-preflight.sh"

(
  test_vssh_runtime=$(mktemp -d)
  trap 'rm -rf "$test_vssh_runtime"' EXIT
  export XDG_RUNTIME_DIR=$test_vssh_runtime
  provider_vagrant() {
    # Model the real SSH client consuming its caller's stdin.
    IFS= read -r _ || true
  }
  observed=()
  while IFS= read -r unit; do
    observed+=("$unit")
    unit_action core start "$unit"
  done <<< $'nrf\nnwdaf-root'
  if [ "${observed[*]}" != 'nrf nwdaf-root' ]; then
    echo "unit action consumed the manifest-driven service loop input" >&2
    exit 1
  fi
)
echo "PASS manifest-driven service loop stdin isolation"

network_unit="$HOST_ROOT/scripts/guest/systemd/5g-nwdaf-network.service"
if grep -Fq 'systemd-networkd-wait-online' "$network_unit"; then
  echo "network service still depends on global interface readiness instead of selected alias reconciliation" >&2
  exit 1
fi
grep -Fx 'ExecStart=/usr/local/libexec/5g-nwdaf-infrastructure/network-setup' \
  "$network_unit" >/dev/null
echo "PASS selected-topology network readiness contract"

grep -F 'observe_collect_subscription_status()' "$HOST_ROOT/scripts/host/observe.sh" >/dev/null
grep -F 'observe_start_subscription_section ' "$HOST_ROOT/scripts/host/observe.sh" >/dev/null
echo "PASS observable subscription-mode collection"

if make --no-print-directory -C "$HOST_ROOT" config-create \
  TESTBED="$testbed" NAME=repository-interface-test FROM= DEVICE=cpu >/dev/null 2>&1; then
  echo "config-create accepted a missing FROM path" >&2
  exit 1
fi
if make --no-print-directory -C "$HOST_ROOT" config-create \
  TESTBED="$testbed" \
  NAME=repository-interface-test \
  FROM="$HOST_ROOT/experiments/examples/full-core-cat-transition/scenario.yaml" \
  DEVICE=cpu >/dev/null 2>&1; then
  echo "config-create accepted an absolute FROM path" >&2
  exit 1
fi
if make --no-print-directory -C "$HOST_ROOT" config-create \
  TESTBED="$testbed" NAME=repository-interface-test FROM=../outside/scenario.yaml DEVICE=cpu \
  >/dev/null 2>&1; then
  echo "config-create accepted a repository-escaping FROM path" >&2
  exit 1
fi
mkdir -p "$custom_scenario_root"
cp -R "$HOST_ROOT/experiments/examples/fl-closure-smoke/." "$custom_scenario_root/"
make --no-print-directory -C "$HOST_ROOT" config-create \
  TESTBED="$testbed" \
  NAME=repository-interface-test \
  FROM=.generated/tests/experiments/repository-interface-test/scenario.yaml \
  DEVICE=cpu WEBCONSOLE=false >/dev/null
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" \
  --config-dir "$make_config" >/dev/null
canonical_config_hash=$(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$make_config" <<'PY'
import sys
from configlib import sha256_tree

print(sha256_tree(sys.argv[1]))
PY
)
runtime_config_hash=$(config_hash "$make_config")
if [ "$runtime_config_hash" != "$canonical_config_hash" ]; then
  echo "runtime config hash differs from the canonical tree hash" >&2
  exit 1
fi
grep -F 'config-hash "$staged"' "$HOST_ROOT/scripts/guest/config-activate.sh" >/dev/null
grep -F 'scripts/shared/config_hash.py' "$HOST_ROOT/scripts/host/guest-tools-sync.sh" >/dev/null
grep -F '"$destination/config-hash"' "$HOST_ROOT/scripts/guest/runtime-tools-install.sh" >/dev/null
if make --no-print-directory -C "$HOST_ROOT" config-create \
  TESTBED="$testbed" \
  NAME=repository-interface-test \
  FROM=.generated/tests/experiments/repository-interface-test/scenario.yaml \
  DEVICE=cpu >/dev/null 2>&1; then
  echo "config-create overwrote an existing output" >&2
  exit 1
fi
echo "PASS explicit scenario path interface"

"$HOST_ROOT/ML/PyMTLF/.venv/bin/python" "$HOST_ROOT/tests/image-dataset.py"
python3 "$HOST_ROOT/tests/clock-skew.py"
node "$HOST_ROOT/tests/experiment-reset.js"
echo "PASS canonical config hash identity"

expected='consumer|5g-nwdaf-consumer.service'
actual=$(vm_log_sources core consumer)
if [ "$actual" != "$expected" ]; then
  echo "unexpected Core Consumer log source: $actual" >&2
  exit 1
fi
expected='network|5g-nwdaf-network.service'
actual=$(vm_log_sources path-a network)
if [ "$actual" != "$expected" ]; then
  echo "unexpected Path A Network log source: $actual" >&2
  exit 1
fi
expected='webconsole|5g-nwdaf@webconsole.service'
actual=$(vm_log_sources core webconsole)
if [ "$actual" != "$expected" ]; then
  echo "unexpected WebConsole log source: $actual" >&2
  exit 1
fi
expected='nwdaf-c|5g-nwdaf@nwdaf-c.service'
actual=$(vm_log_sources core 'nwdaf-*')
if [ "$actual" != "$expected" ]; then
  echo "unexpected Core NWDAF wildcard log source: $actual" >&2
  exit 1
fi
if [ -n "$(vm_log_sources path-b consumer)" ]; then
  echo "Path B unexpectedly owns the Consumer log source" >&2
  exit 1
fi
mapfile -t core_log_sources < <(vm_log_sources core '*')
mapfile -t path_a_log_sources < <(vm_log_sources path-a '*')
mapfile -t path_b_log_sources < <(vm_log_sources path-b '*')
if [ "${#core_log_sources[@]}" -ne 14 ] ||
   [ "${#path_a_log_sources[@]}" -ne 7 ] ||
   [ "${#path_b_log_sources[@]}" -ne 7 ]; then
  echo "unexpected owned VM log source counts: core=${#core_log_sources[@]} path-a=${#path_a_log_sources[@]} path-b=${#path_b_log_sources[@]}" >&2
  exit 1
fi
if [ "$(normalize_log_since '1970-01-01 00:00:00 UTC')" != '1970-01-01T00:00:00Z' ]; then
  echo "log time normalization did not produce canonical UTC" >&2
  exit 1
fi
if [ "$(TZ=Asia/Shanghai normalize_log_since '1970-01-01 08:00:00')" != '1970-01-01T00:00:00Z' ]; then
  echo "log time normalization did not interpret input in the Host timezone" >&2
  exit 1
fi
if [ "$(journal_log_since '1970-01-01T00:00:00Z')" != '1970-01-01 00:00:00 UTC' ]; then
  echo "journald time rendering is not compatible with the Guest parser" >&2
  exit 1
fi
if invalid_since_output=$("$HOST_ROOT/scripts/host/logs.sh" --testbed "$testbed" --source vm \
    --since 'not-a-time' --no-follow 2>&1); then
  echo "logs.sh accepted an invalid --since value" >&2
  exit 1
fi
if [ "$invalid_since_output" != 'invalid --since value: not-a-time' ]; then
  echo "logs.sh returned an unexpected invalid-time diagnostic: $invalid_since_output" >&2
  exit 1
fi
make_log_plan=$(make -C "$HOST_ROOT" --no-print-directory -n logs \
  TESTBED="$testbed" SOURCE=ml VM=all SERVICE=pymtlf-c SINCE='15 minutes ago' TAIL=25 FOLLOW=false)
for expected_arg in \
  '--source "ml"' \
  '--vm "all"' \
  '--service "pymtlf-c"' \
  '--since "15 minutes ago"' \
  '--tail "25"' \
  'follow_args=(--no-follow)'; do
  if [[ "$make_log_plan" != *"$expected_arg"* ]]; then
    echo "make logs did not forward $expected_arg" >&2
    exit 1
  fi
done
empty_service_plan=$(make -C "$HOST_ROOT" --no-print-directory -n logs TESTBED="$testbed" SERVICE=)
if [[ "$empty_service_plan" != *'--service ""'* ]]; then
  echo "make logs did not preserve an empty all-service selector" >&2
  exit 1
fi
if invalid_follow_output=$(make -C "$HOST_ROOT" --no-print-directory logs \
    TESTBED="$testbed" FOLLOW=sometimes 2>&1); then
  echo "make logs accepted an invalid FOLLOW value" >&2
  exit 1
fi
if [[ "$invalid_follow_output" != *'FOLLOW must be true or false'* ]]; then
  echo "make logs returned an unexpected FOLLOW diagnostic" >&2
  exit 1
fi
echo "PASS owned log sources and UTC time normalization"

assert_ue_readiness() {
  local expected=$1 service_state=$2 journal=$3 label=$4 actual
  actual=$(ue_readiness_states "$service_state" "$journal")
  if [ "$actual" != "$expected" ]; then
    echo "unexpected UE readiness for $label: expected=$expected actual=$actual" >&2
    exit 1
  fi
}
assert_ue_readiness 'inactive|inactive' inactive '' inactive
assert_ue_readiness 'not-running|not-running' not-running '' vm-poweroff
assert_ue_readiness 'failed|failed' failed \
  'Initial Registration is successful' failed-service
assert_ue_readiness 'pending|pending' activating '' activating
assert_ue_readiness 'pending|pending' active '' fresh-invocation
assert_ue_readiness 'successful|pending' active \
  'Initial Registration is successful' registration-only
assert_ue_readiness 'successful|successful' active \
  $'Initial Registration is successful\nPDU Session establishment is successful PSI[1]' complete
assert_ue_readiness 'failed|failed' active \
  $'Initial Registration failed [PLMN_NOT_ALLOWED]\nPDU Session Establishment Reject received [INSUFFICIENT_RESOURCES]' rejected
assert_ue_readiness 'successful|successful' active \
  $'Initial Registration failed [TEMPORARY]\nPDU Session Establishment procedure failure\nInitial Registration is successful\nPDU Session establishment is successful PSI[1]' recovered
(
  source "$HOST_ROOT/scripts/host/services-status.sh"
  assert_guest_runtime_identity() { :; }
  vssh() {
    local machine=$1 remote_script=$2
    [ "$machine" = path-a ]
    [[ "$remote_script" == *'_SYSTEMD_INVOCATION_ID=$invocation'* ]]
    bash -n <<<"$remote_script"
  }
  machine_snapshot path-a ue1 ue2 ue3
  machine_snapshot() {
    local machine=$1 encoded
    case "$machine" in
      core)
        printf '%s\n' 'SERVICE|core|nrf|active'
        ;;
      path-a)
        encoded=$(printf '%s' $'Initial Registration is successful\nPDU Session establishment is successful PSI[1]' | base64 -w0)
        printf '%s\n' 'SERVICE|path-a|ue1|active'
        printf 'UE|path-a|ue1|active|current-a|%s\n' "$encoded"
        ;;
      path-b)
        printf '%s\n' 'SERVICE|path-b|ue4|inactive'
        printf '%s\n' 'UE|path-b|ue4|inactive||'
        ;;
    esac
  }
  vm_state_records() {
    printf '%s\n' 'core|running' 'path-a|running' 'path-b|running'
  }
  status_output=$(services_status_main "$testbed" "$explicit_config")
  compact_status=$(sed -E 's/[[:space:]]+/ /g' <<<"$status_output")
  [[ "$compact_status" == *'path-a ue1 active successful successful'* ]]
  [[ "$compact_status" == *'path-b ue4 inactive inactive inactive'* ]]
  vm_state_records() {
    printf '%s\n' 'core|poweroff' 'path-a|poweroff' 'path-b|poweroff'
  }
  machine_snapshot() { return 99; }
  stopped_output=$(services_status_main "$testbed" "$explicit_config")
  stopped_compact=$(sed -E 's/[[:space:]]+/ /g' <<<"$stopped_output")
  [[ "$stopped_compact" == *'path-a ue1 not-running not-running not-running'* ]]
)
echo "PASS current-invocation UE readiness parsing"

(
  source "$HOST_ROOT/scripts/host/observe.sh"
  good_section() { printf '%s\n' 'section-ready'; }
  bad_section() { printf '%s\n' 'backend refused query' >&2; return 7; }
  [ "$(observe_section TEST good_section)" = section-ready ]
  if failed_output=$(observe_section TEST bad_section 2>&1); then
    echo "observe section accepted a failed status backend" >&2
    exit 1
  fi
  [[ "$failed_output" == *'backend refused query'* ]]
  [[ "$failed_output" == *'TEST status unavailable'* ]]
  observe_snapshot() { printf '%s\n' complete-snapshot; }
  rendered=$(OBSERVE_INTERVAL=0 observe_main --once "$testbed" "$explicit_config")
  [[ "$rendered" == *'SNAPSHOT started='* ]]
  [[ "$rendered" == *'collection='* ]]
  [[ "$rendered" == *'complete-snapshot'* ]]
  observe_snapshot() { return 1; }
  if observe_main --once "$testbed" "$explicit_config" >/dev/null 2>&1; then
    echo "observe --once accepted an incomplete snapshot" >&2
    exit 1
  fi
  source "$HOST_ROOT/scripts/host/webconsole-status.sh"
  vm_state_for() { printf '%s\n' poweroff; }
  [[ "$(webconsole_status_main)" == *'state=not-running'* ]]
  source "$HOST_ROOT/scripts/host/subscriptions-status.sh"
  vm_state_for() { printf '%s\n' poweroff; }
  powered_off_subscriptions=$(subscriptions_status_main)
  [[ "$powered_off_subscriptions" == *'consumer_service=not-running reason=core-not-running'* ]]
  [[ "$powered_off_subscriptions" == *'local_resource_state=not-readable reason=core-not-running'* ]]
  source "$HOST_ROOT/scripts/host/lib.sh"
  vm_state_records() { return 1; }
  if vm_state_for core >/dev/null 2>&1; then
    echo "vm_state_for hid a Vagrant status failure" >&2
    exit 1
  fi
)
echo "PASS aggregate status failure and poweroff semantics"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  vm_cache=$(mktemp)
  printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|not_created' >"$vm_cache"
  VM_STATE_RECORDS_FILE=$vm_cache
  cached_states=$(vm_state_records)
  rm -f -- "$vm_cache"
  unset VM_STATE_RECORDS_FILE
  [[ "$cached_states" == *'core|running'* ]]
  [[ "$cached_states" == *'path-b|not_created'* ]]
)
echo "PASS cached VM state snapshot"

(
  vm_state_for() { printf '%s\n' poweroff; }
  vssh() {
    echo "consumer unit query unexpectedly reached a powered-off VM" >&2
    return 99
  }
  if consumer_unit_active; then
    echo "powered-off Core VM reported an active Consumer" >&2
    exit 1
  fi

  vm_state_for() { printf '%s\n' running; }
  vssh() { return 0; }
  consumer_unit_active

  vssh() { return 3; }
  if consumer_unit_active; then
    echo "inactive Consumer unit reported active" >&2
    exit 1
  fi
)
echo "PASS Consumer lifecycle state detection"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  config_hash() { printf '%s\n' selected-hash; }
  vm_state_records() { printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|poweroff'; }
  config_guest_units() { printf '%s\n' nrf; }
  vssh() { printf '%s\n' 'IDENTITY|stale-hash|stale-hash'; }
  if assert_guest_runtime_identity ignored; then
    echo "Guest identity guard accepted a wrong active config" >&2
    exit 1
  fi
  vssh() { printf '%s\n' 'IDENTITY|selected-hash|selected-hash' 'UNIT|nwdaf-unexpected'; }
  if assert_guest_runtime_identity ignored; then
    echo "Guest identity guard accepted an unexpected active unit" >&2
    exit 1
  fi
  vssh() { printf '%s\n' 'IDENTITY|selected-hash|selected-hash' 'UNIT|nrf'; }
  assert_guest_runtime_identity ignored
)
echo "PASS selected/active Guest identity fail-closed guards"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  vm_state_records() { printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|poweroff'; }
  vssh() { printf '%s\n' nwdaf-still-active; }
  if assert_no_active_guest_units; then
    echo "Guest stop verification accepted a partially stopped stack" >&2
    exit 1
  fi
  vssh() { return 17; }
  if assert_no_active_guest_units; then
    echo "Guest stop verification hid an inventory read failure" >&2
    exit 1
  fi
)
echo "PASS partial Guest stop and inventory failure detection"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  selected_services=$(printf '%s\n' pymtlf-root pymtlf-leaf-a)
  selected_volumes=$(printf '%s\n' 'root-state|image-a' 'leaf-a-state|image-b')
  selected_containers=$(printf '%s\n' 'pymtlf-root|Exited (0)' 'pymtlf-leaf-a|Exited (0)')
  selected_volume_inventory=$(printf '%s\n' 'project_root-state|root-state' 'project_leaf-a-state|leaf-a-state')
  selected_output=$(check_reset_runtime_inventory \
    "$selected_services" "$selected_volumes" "$selected_containers" "$selected_volume_inventory" project)
  [ -z "$selected_output" ]

  unexpected_containers=$(printf '%s\n' 'pymtlf-root|Exited (0)' 'pymtlf-root|Created' 'unselected-service|Up 1 minute' '|Exited (0)')
  unexpected_volumes=$(printf '%s\n' 'project_root-state|root-state' 'project_duplicate|root-state' 'project_unknown|unknown-state')
  if unexpected_output=$(check_reset_runtime_inventory \
      "$selected_services" "$selected_volumes" "$unexpected_containers" "$unexpected_volumes" project); then
    echo "reset inventory accepted unexpected project resources" >&2
    exit 1
  fi
  grep -Fx 'CONTAINER_DUPLICATE service=pymtlf-root status=Created retained=yes selected=yes' \
    <<<"$unexpected_output" >/dev/null
  grep -Fx 'CONTAINER_UNEXPECTED service=unselected-service status=Up 1 minute retained=yes selected=no' \
    <<<"$unexpected_output" >/dev/null
  grep -Fx 'CONTAINER_INVALID service=unknown status=Exited (0) retained=yes selected=no' \
    <<<"$unexpected_output" >/dev/null
  grep -Fx 'VOLUME_UNEXPECTED logical=root-state physical=project_duplicate expected=project_root-state retained=yes selected=no' \
    <<<"$unexpected_output" >/dev/null
  grep -Fx 'VOLUME_UNEXPECTED logical=unknown-state physical=project_unknown retained=yes selected=no' \
    <<<"$unexpected_output" >/dev/null
)
echo "PASS reset exact-scope runtime inventory"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  config_host_containers() { printf '%s\n' pymtlf-root; }
  config_reset_host_containers() { printf '%s\n' pymtlf-root; }
  config_reset_ml_volume_records() { printf '%s\n' 'root-state|image-a'; }
  docker() {
    if [ "$1" = ps ]; then
      printf '%s\n' 'pymtlf-root|Exited (0)'
    elif [ "$1" = volume ] && [ "$2" = ls ]; then
      printf '%s\n' 'project_old-state|old-state'
    else
      echo "unexpected Docker command in ML inventory test: $*" >&2
      return 2
    fi
  }
  if inventory_error=$(assert_ml_runtime_identity ignored /unused start 2>&1); then
    echo "ML startup identity guard accepted an unexpected project volume" >&2
    exit 1
  fi
  grep -F 'VOLUME_UNEXPECTED' <<<"$inventory_error" >/dev/null
)
echo "PASS ML startup exact project inventory"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  activation_fixture=$(mktemp -d)
  activation_log="$activation_fixture/activation.log"
  trap 'rm -rf "$activation_fixture"' EXIT
  export XDG_RUNTIME_DIR="$activation_fixture/runtime"
  printf '%s\n' fixture >"$activation_fixture/payload"
  provider_vagrant() { printf 'UPLOAD|%s\n' "$*" >>"$activation_log"; }
  vssh() {
    local machine=$1 command=$2
    case "$command" in
      printf*) printf '/old/%s|old-%s\n' "$machine" "$machine" ;;
      *"config-activate '$machine' '/etc/5g-nwdaf-infrastructure/config-sets/"*)
        printf 'ACTIVATE|%s\n' "$machine" >>"$activation_log"
        [ "$machine" != path-a ]
        ;;
      *"config-activate '$machine' '/old/$machine' 'old-$machine'"*)
        printf 'ROLLBACK|%s\n' "$machine" >>"$activation_log"
        ;;
      *) printf 'REMOTE|%s\n' "$machine" >>"$activation_log" ;;
    esac
  }
  if stage_config_all "$activation_fixture" selected-hash; then
    echo "partial config activation unexpectedly succeeded" >&2
    exit 1
  fi
  grep -Fx 'ACTIVATE|core' "$activation_log" >/dev/null
  grep -Fx 'ACTIVATE|path-a' "$activation_log" >/dev/null
  grep -Fx 'ROLLBACK|core' "$activation_log" >/dev/null
  if grep -Fq 'ACTIVATE|path-b' "$activation_log"; then
    echo "config activation continued after a partial failure" >&2
    exit 1
  fi
)
echo "PASS partial Guest config activation rollback"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  MACHINES=(core path-a)
  activation_fixture=$(mktemp -d)
  trap 'rm -rf "$activation_fixture"' EXIT
  export XDG_RUNTIME_DIR="$activation_fixture/runtime"
  printf '%s\n' fixture >"$activation_fixture/payload"
  provider_vagrant() {
    [ "$4" != path-a ]
  }
  vssh() {
    case "$2" in
      printf*) printf '/old/%s|old-%s\n' "$1" "$1" ;;
      *config-activate*) printf 'ACTIVATE|%s\n' "$1" >>"$activation_fixture/activation.log" ;;
    esac
  }
  if stage_config_all "$activation_fixture" selected-hash; then
    echo "config staging accepted a failed Guest upload" >&2
    exit 1
  fi
  if [ -e "$activation_fixture/activation.log" ]; then
    echo "config activation began before all Guest uploads succeeded" >&2
    exit 1
  fi
)
echo "PASS failed Guest config staging blocks activation"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  MACHINES=(core path-a)
  activation_fixture=$(mktemp -d)
  trap 'rm -rf "$activation_fixture"' EXIT
  export XDG_RUNTIME_DIR="$activation_fixture/runtime"
  printf '%s\n' fixture >"$activation_fixture/payload"
  provider_vagrant() { :; }
  vssh() {
    local machine=$1 command=$2
    case "$command" in
      printf*) printf '/old/%s|old-%s\n' "$machine" "$machine" ;;
      *"config-activate 'core' '/etc/5g-nwdaf-infrastructure/config-sets/"*) return 0 ;;
      *"config-activate 'path-a' '/etc/5g-nwdaf-infrastructure/config-sets/"*) return 23 ;;
      *"config-activate 'core' '/old/core' 'old-core'"*) return 29 ;;
      *) return 0 ;;
    esac
  }
  if activation_error=$(stage_config_all "$activation_fixture" selected-hash 2>&1); then
    echo "partial config activation with rollback failure unexpectedly succeeded" >&2
    exit 1
  fi
  if ! grep -Fq 'config activation rollback incomplete: core' <<<"$activation_error"; then
    echo "rollback failure did not identify the exact machine" >&2
    printf '%s\n' "$activation_error" >&2
    exit 1
  fi
)
echo "PASS config activation rollback failure attribution"

(
  source "$HOST_ROOT/scripts/host/lib.sh"
  wait_fixture=$(mktemp)
  trap 'rm -f "$wait_fixture"' EXIT
  printf '0\n' >"$wait_fixture"
  docker() {
    checks=$(<"$wait_fixture")
    checks=$((checks + 1))
    printf '%s\n' "$checks" >"$wait_fixture"
    if [ "$checks" -lt 3 ]; then
      printf '%s\n' container-stopping
    fi
    return 0
  }
  sleep() { :; }
  wait_no_running_ml_containers test-project 5
  checks=$(<"$wait_fixture")
  [ "$checks" -eq 3 ] || {
    echo "ML stop wait did not observe Docker state convergence" >&2
    exit 1
  }

  docker() {
    if [ "$1" = ps ] && [[ " $* " == *" --format "* ]]; then
      printf '%s\n' 'container-stuck Up 1 minute' >&2
    else
      printf '%s\n' container-stuck
    fi
  }
  if wait_no_running_ml_containers test-project 2 >/dev/null 2>&1; then
    echo "ML stop wait accepted a permanently running container" >&2
    exit 1
  fi
)
echo "PASS bounded ML stop convergence and timeout detection"

python3 - "$HOST_ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
for directory in (root / "scripts", root / "tests"):
    for path in sorted(directory.rglob("*.py")):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS Python syntax")
PY

python3 "$HOST_ROOT/tests/config-contract.py" "${check_args[@]}"
python3 "$HOST_ROOT/tests/execution-policy.py"
python3 "$HOST_ROOT/tests/dataset-summary.py"
python3 "$HOST_ROOT/tests/mobile-identity.py"
echo "SKIP legacy static topology regression (retained unverified asset)"
python3 "$HOST_ROOT/tests/runtime-inventory.py"
python3 "$HOST_ROOT/tests/consumer-state.py"
python3 "$HOST_ROOT/tests/ml-status.py"
python3 "$HOST_ROOT/tests/fl-control.py"
python3 "$HOST_ROOT/tests/fl-experiment.py"
(
  source "$HOST_ROOT/scripts/host/subscriptions-status.sh"
  state_fixture='{"status":"active","notificationCount":3,"subscriptions":[{"path":"a","status":"active","nfInstanceId":"provider-a","tac":"000001","correlationId":"corr-a","location":"http://a/subscriptions/1"},{"path":"b","status":"active","nfInstanceId":"provider-b","tac":"000002","correlationId":"corr-b","location":"http://b/subscriptions/2"}],"callbacksByPath":{"a":{"requestCount":2,"lastCallbackAt":"2026-08-13T00:00:00Z","correlationId":"corr-a"},"b":{"requestCount":1,"lastCallbackAt":"2026-08-13T00:00:01Z","correlationId":"corr-b"}}}'
  rendered=$(render_subscription_status <<<"$state_fixture")
  compact=$(sed -E 's/[[:space:]]+/ /g' <<<"$rendered")
  [[ "$compact" == *'a active provider-a 000001 corr-a 2 2026-08-13T00:00:00Z http://a/subscriptions/1'* ]]
  [[ "$compact" == *'b active provider-b 000002 corr-b 1 2026-08-13T00:00:01Z http://b/subscriptions/2'* ]]
  legacy_fixture='{"status":"active","notificationCount":4,"subscriptions":[{"path":"a","status":"active","nfInstanceId":"provider-a","tac":"000001","correlationId":"legacy-a","location":"http://a/subscriptions/legacy"}]}'
  legacy_rendered=$(render_subscription_status <<<"$legacy_fixture")
  [[ "$legacy_rendered" == *'unknown'* ]]
  vm_state_for() { printf '%s\n' running; }
  consumer_status_snapshot() { printf '%s\n' 'CONSUMER_SERVICE|active' '{not-json'; }
  if subscriptions_status_main >/dev/null 2>&1; then
    echo "subscriptions-status accepted invalid Consumer state" >&2
    exit 1
  fi
)
echo "PASS subscription status rendering and error propagation"
python3 "$HOST_ROOT/tests/provisioning-lock.py"
python3 "$HOST_ROOT/tests/testbed-definition.py"
"$HOST_ROOT/scripts/host/webconsole-prepare.sh" "$testbed" "config/default" |
  grep -F "no toolchain or artifact was changed"
"$HOST_ROOT/scripts/host/webconsole-start.sh" "$testbed" "config/default" |
  grep -F "no toolchain, artifact, or process was changed"
python3 "$HOST_ROOT/scripts/host/config-render.py" --testbed "$testbed" \
  --name enabled --scenario experiments/examples/fl-closure-smoke/scenario.yaml \
  --output-root "$webconsole_root" --ml-device cpu --webconsole true
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" \
  --config-dir "$webconsole_root/enabled"
python3 "$HOST_ROOT/tests/network-config.py"
"$HOST_ROOT/tests/dataset-determinism.sh" "$testbed" "$explicit_config"
echo "SKIP legacy ML Compose and CPU smoke regression (retained unverified assets)"

embedded_ruby=/opt/vagrant/embedded/bin/ruby
[ -x "$embedded_ruby" ] || {
  echo "Vagrant embedded Ruby is unavailable for isolated Vagrantfile syntax validation" >&2
  exit 1
}
"$embedded_ruby" -c "$HOST_ROOT/Vagrantfile" >/dev/null
echo "PASS isolated Vagrantfile Ruby syntax"
(
  vagrantfile_fixture=$(mktemp -d)
  trap 'rm -rf "$vagrantfile_fixture"' EXIT
  cp "$HOST_ROOT/Vagrantfile" "$vagrantfile_fixture/Vagrantfile"
  cp "$HOST_ROOT/testbed.yaml" "$vagrantfile_fixture/testbed.yaml"
  cp "$HOST_ROOT/components.lock.yaml" "$vagrantfile_fixture/components.lock.yaml"
  if selection_output=$(cd "$vagrantfile_fixture" && env -u TESTBED \
      "$embedded_ruby" Vagrantfile 2>&1); then
    echo "Vagrantfile accepted a missing TESTBED selection" >&2
    exit 1
  fi
  grep -F "TESTBED must select an explicit testbed definition" <<<"$selection_output" >/dev/null
  touch "$vagrantfile_fixture/testbed.local.yaml"
  if legacy_output=$(cd "$vagrantfile_fixture" && TESTBED=testbed.yaml "$embedded_ruby" Vagrantfile 2>&1); then
    echo "Vagrantfile accepted removed testbed.local.yaml compatibility layer" >&2
    exit 1
  fi
  grep -F "testbed.local.yaml is no longer supported" <<<"$legacy_output" >/dev/null
  rm "$vagrantfile_fixture/testbed.local.yaml"
  if provider_output=$(cd "$vagrantfile_fixture" && VAGRANT_DEFAULT_PROVIDER=libvirt \
    TESTBED=testbed.yaml "$embedded_ruby" Vagrantfile 2>&1); then
    echo "Vagrantfile accepted a non-VirtualBox provider" >&2
    exit 1
  fi
  grep -F "unsupported provider libvirt; expected virtualbox" <<<"$provider_output" >/dev/null
)
echo "PASS isolated Vagrantfile selection guards"

echo "Repository tests passed with synthetic provider checks; no provider or service process was started."
