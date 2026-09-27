#!/usr/bin/env bash
set -euo pipefail

HOST_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$HOST_ROOT"

project_python="$HOST_ROOT/.venv/bin/python"
pymtlf_python="$HOST_ROOT/ML/PyMTLF/.venv/bin/python"
for interpreter in "$project_python" "$pymtlf_python"; do
  if [ ! -x "$interpreter" ]; then
    echo "required project interpreter is missing: $interpreter" >&2
    exit 1
  fi
done

while IFS= read -r script; do
  bash -n "$script"
done < <(find scripts tests -type f -name '*.sh' -print | sort)
echo "PASS shell syntax"

"$project_python" -m py_compile \
  scripts/guest/network-config.py \
  scripts/guest/provisioning-lock.py \
  scripts/host/*.py \
  tests/*.py
echo "PASS Python compile"

python_tests=(
  tests/testbed-definition.py
  tests/execution-policy.py
  tests/network-config.py
  tests/provisioning-lock.py
  tests/clock-skew.py
  tests/fl-control.py
  tests/fl-experiment.py
  tests/ml-status.py
  tests/fl-analysis.py
  tests/runtime-inventory.py
)
for test_file in "${python_tests[@]}"; do
  "$project_python" "$test_file"
done

"$pymtlf_python" tests/image-dataset.py

bash tests/provider-runtime-preflight.sh
node tests/experiment-reset.js

echo "REPOSITORY_TEST status=passed"
