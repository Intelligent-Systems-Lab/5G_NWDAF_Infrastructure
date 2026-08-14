#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=$(ml_project_name)
args=(--project "$project")
[ -z "${ML_STATUS_CACHE_DIR:-}" ] || args+=(--cache-dir "$ML_STATUS_CACHE_DIR")
python3 "$HOST_ROOT/scripts/host/ml-status.py" "${args[@]}"
