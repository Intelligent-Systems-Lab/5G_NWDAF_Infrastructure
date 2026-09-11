#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:?usage: clock-check.sh testbed}
select_testbed_machines "$testbed"
tolerance_ms=$(PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_path
print(load_yaml(resolve_path(sys.argv[1]))["operations"]["clockSkewToleranceMs"])
PY
)

for machine in "${MACHINES[@]}"; do
  echo "CLOCK SYNC $machine"
  vssh "$machine" "chronyc waitsync 10 0.5 0.0 1 >/dev/null; chronyc tracking | grep -Eq '^Leap status[[:space:]]*:[[:space:]]*Normal$'"
done

temporary=$(mktemp -d -t 5g-nwdaf-clock.XXXXXX)
trap 'rm -rf "$temporary"' EXIT
declare -A pids=()
for machine in "${MACHINES[@]}"; do
  (vssh "$machine" "date +%s%3N" | tr -d '\r' | awk -v machine="$machine" 'NF == 1 {print machine "|" $1}') >"$temporary/$machine" &
  pids["$machine"]=$!
done
for machine in "${MACHINES[@]}"; do
  wait "${pids[$machine]}"
done
cat "$temporary"/* | python3 "$HOST_ROOT/scripts/host/clock-skew.py" \
  --machines "$(IFS=,; echo "${MACHINES[*]}")" --tolerance-ms "$tolerance_ms"
