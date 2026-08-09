#!/usr/bin/env bash
set -euo pipefail

if [ -n "${PYMTLF_SEED_SOURCE:-}" ]; then
  : "${PYMTLF_SEED_MODEL_ID:?PYMTLF_SEED_MODEL_ID is required}"
  : "${PYMTLF_SEED_INTEROPERABILITY:?PYMTLF_SEED_INTEROPERABILITY is required}"
  : "${PYMTLF_SEED_ARTIFACT_KEY:?PYMTLF_SEED_ARTIFACT_KEY is required}"

  result=$(python /opt/app/tools/import_seed_model.py \
    --config /etc/5g-nwdaf/config.yaml \
    --source "$PYMTLF_SEED_SOURCE" \
    --model-id "$PYMTLF_SEED_MODEL_ID" \
    --model-interoperability "$PYMTLF_SEED_INTEROPERABILITY")
  actual=$(python -c 'import json, sys; print(json.load(sys.stdin)["artifact_key"])' <<<"$result")
  if [ "$actual" != "$PYMTLF_SEED_ARTIFACT_KEY" ]; then
    echo "seed artifact mismatch: expected $PYMTLF_SEED_ARTIFACT_KEY, got $actual" >&2
    exit 1
  fi
  echo "PyMTLF seed artifact ready key=$actual"
fi

exec "$@"
