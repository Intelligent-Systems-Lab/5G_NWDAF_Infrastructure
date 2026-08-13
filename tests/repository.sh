#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/host/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
cpu_config="$HOST_ROOT/config/generated/ml-repository-test"
webconsole_root=$(mktemp -d)
legacy_testbed_root=$(mktemp -d)
cleanup() {
  rm -rf "$cpu_config"
  rm -rf "$webconsole_root"
  rm -rf "$legacy_testbed_root"
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
if invalid_since_output=$("$HOST_ROOT/scripts/host/logs.sh" --source vm \
    --since 'not-a-time' --no-follow 2>&1); then
  echo "logs.sh accepted an invalid --since value" >&2
  exit 1
fi
if [ "$invalid_since_output" != 'invalid --since value: not-a-time' ]; then
  echo "logs.sh returned an unexpected invalid-time diagnostic: $invalid_since_output" >&2
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
  status_output=$(services_status_main)
  compact_status=$(sed -E 's/[[:space:]]+/ /g' <<<"$status_output")
  [[ "$compact_status" == *'path-a ue1 active successful successful'* ]]
  [[ "$compact_status" == *'path-b ue4 inactive inactive inactive'* ]]
  vm_state_records() {
    printf '%s\n' 'core|poweroff' 'path-a|poweroff' 'path-b|poweroff'
  }
  machine_snapshot() { return 99; }
  stopped_output=$(services_status_main)
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
  observe_snapshot() { return 1; }
  if observe_main --once >/dev/null 2>&1; then
    echo "observe --once accepted an incomplete snapshot" >&2
    exit 1
  fi
  source "$HOST_ROOT/scripts/host/webconsole-status.sh"
  vm_state_for() { printf '%s\n' poweroff; }
  [[ "$(webconsole_status_main)" == *'state=not-running'* ]]
  source "$HOST_ROOT/scripts/host/subscriptions-status.sh"
  [[ "$(subscriptions_status_main)" == *'local_resource_state=unavailable reason=core-not-running'* ]]
  vm_state_records() { return 1; }
  if vm_state_for core >/dev/null 2>&1; then
    echo "vm_state_for hid a Vagrant status failure" >&2
    exit 1
  fi
)
echo "PASS aggregate status failure and poweroff semantics"

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
python3 "$HOST_ROOT/tests/mobile-identity.py"
python3 "$HOST_ROOT/tests/consumer-state.py"
python3 "$HOST_ROOT/tests/ml-status.py"
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
  vssh() { printf '%s\n' active; }
  consumer_cli() { printf '%s\n' '{not-json'; }
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
  --name enabled --scenario fixtures/full-core/scenarios/fl-closure-smoke.yaml \
  --output-root "$webconsole_root" --ml-device cpu --webconsole true
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" \
  --config-dir "$webconsole_root/enabled"
python3 "$HOST_ROOT/tests/network-config.py"
"$HOST_ROOT/tests/dataset-determinism.sh" "$testbed" "$explicit_config"
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" "${check_args[@]}"
python3 "$HOST_ROOT/tests/support/ml-cpu-config.py" --force --output "$cpu_config"
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$cpu_config"
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" --testbed "$testbed" \
  --config-dir "$cpu_config" --mode cpu-smoke

cp "$HOST_ROOT/Vagrantfile" "$legacy_testbed_root/Vagrantfile"
cp "$HOST_ROOT/testbed.yaml" "$legacy_testbed_root/testbed.yaml"
touch "$legacy_testbed_root/testbed.local.yaml"
if legacy_output=$(cd "$legacy_testbed_root" && TESTBED=testbed.yaml vagrant validate 2>&1); then
  echo "Vagrant accepted removed testbed.local.yaml compatibility layer" >&2
  exit 1
fi
grep -F "testbed.local.yaml is no longer supported" <<<"$legacy_output" >/dev/null
echo "OK stale testbed.local.yaml is rejected"
rm "$legacy_testbed_root/testbed.local.yaml"
if provider_output=$(cd "$legacy_testbed_root" && VAGRANT_DEFAULT_PROVIDER=libvirt TESTBED=testbed.yaml vagrant validate 2>&1); then
  echo "Vagrant accepted a non-VirtualBox provider" >&2
  exit 1
fi
grep -F "unsupported provider libvirt; expected virtualbox" <<<"$provider_output" >/dev/null
echo "OK non-VirtualBox provider is rejected"

(cd "$HOST_ROOT" && TESTBED="$testbed" vagrant validate)

echo "Repository tests passed; no VM or service lifecycle state was changed."
