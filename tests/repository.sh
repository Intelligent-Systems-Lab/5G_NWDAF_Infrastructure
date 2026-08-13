#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/host/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
cpu_config="$HOST_ROOT/config/generated/ml-repository-test"
webconsole_root=$(mktemp -d)
legacy_testbed_root=$(mktemp -d)
cleanup_wait_log=""
cleanup_wait_writer=""
cleanup() {
  if [ -n "$cleanup_wait_writer" ]; then
    kill "$cleanup_wait_writer" >/dev/null 2>&1 || true
    wait "$cleanup_wait_writer" 2>/dev/null || true
  fi
  if [ -n "$cleanup_wait_log" ]; then
    rm -f "$cleanup_wait_log"
  fi
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

monitor_log_fixture=$'ML Model Monitor subscription active subscription_id=old-a registration_id=reg-a\nML Model Monitor subscription active subscription_id=current-b registration_id=reg-b\nML Model Monitor subscription removed subscription_id=old-a registration_id=reg-a\nML Model Monitor subscription active subscription_id=current-a registration_id=reg-c'
mapfile -t active_monitor_ids < <(
  ml_monitor_active_subscription_ids_from_log <<<"$monitor_log_fixture"
)
if [ "${active_monitor_ids[*]}" != "current-a current-b" ]; then
  echo "unexpected active Model Monitor subscriptions: ${active_monitor_ids[*]}" >&2
  exit 1
fi
echo "PASS Model Monitor cleanup log reconstruction"

removed_monitor_log_fixture=$'ML Model Monitor subscription removed subscription_id=done-b registration_id=reg-b\nunrelated log\nML Model Monitor subscription removed subscription_id=done-a registration_id=reg-a'
mapfile -t removed_monitor_ids < <(
  ml_monitor_removed_subscription_ids_from_log <<<"$removed_monitor_log_fixture"
)
if [ "${removed_monitor_ids[*]}" != "done-a done-b" ]; then
  echo "unexpected removed Model Monitor subscriptions: ${removed_monitor_ids[*]}" >&2
  exit 1
fi
echo "PASS Model Monitor cleanup completion parsing"

cleanup_wait_log=$(mktemp)
(
  sleep 0.2
  printf '%s\n' 'ML Model Monitor subscription removed subscription_id=done-a registration_id=reg-a'
  sleep 0.2
  printf '%s\n' 'ML Model Monitor subscription removed subscription_id=done-b registration_id=reg-b'
  sleep 2
) >"$cleanup_wait_log" &
cleanup_wait_writer=$!
wait_for_ml_monitor_cleanup "$cleanup_wait_log" "$cleanup_wait_writer" 3 1 done-a done-b
kill "$cleanup_wait_writer" >/dev/null 2>&1 || true
wait "$cleanup_wait_writer" 2>/dev/null || true
rm -f "$cleanup_wait_log"
cleanup_wait_writer=""
cleanup_wait_log=""
echo "PASS Model Monitor cleanup convergence wait"

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
