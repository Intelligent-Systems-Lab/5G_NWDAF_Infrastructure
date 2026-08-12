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
