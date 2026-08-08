#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

project=$(ml_project_name)
python3 "$HOST_ROOT/scripts/host/ml-status.py" --project "$project"
