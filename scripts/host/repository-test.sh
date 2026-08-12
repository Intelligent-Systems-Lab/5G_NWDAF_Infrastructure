#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
cpu_config="$HOST_ROOT/config/generated/ml-repository-test"
cleanup() {
  rm -rf "$cpu_config"
}
trap cleanup EXIT
check_args=(--testbed "$testbed")
if [ -n "$explicit_config" ]; then
  check_args+=(--config-dir "$explicit_config")
fi

while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find "$HOST_ROOT/scripts" -type f -name '*.sh' -print0)
echo "PASS shell syntax"

python3 - "$HOST_ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
for path in sorted((root / "scripts").rglob("*.py")):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("PASS Python syntax")
PY

python3 "$HOST_ROOT/scripts/host/config-contract-smoke.py" "${check_args[@]}"
python3 "$HOST_ROOT/scripts/host/network-config-smoke.py"
"$HOST_ROOT/scripts/host/dataset-smoke.sh" "$testbed" "$explicit_config"
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" "${check_args[@]}"
python3 "$HOST_ROOT/scripts/host/ml-smoke-config.py" --force --output "$cpu_config"
python3 "$HOST_ROOT/scripts/host/config-check.py" --testbed "$testbed" --config-dir "$cpu_config"
python3 "$HOST_ROOT/scripts/host/ml-compose-check.py" --testbed "$testbed" \
  --config-dir "$cpu_config" --mode cpu-smoke
(cd "$HOST_ROOT" && TESTBED="$testbed" vagrant validate)

echo "Repository tests passed; no VM or service lifecycle state was changed."
