#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

testbed=${1:-testbed.yaml}
explicit_config=${2:-}
config_dir=$(effective_config_dir "$testbed" "$explicit_config")
enabled=$(config_webconsole_enabled "$config_dir")
if [ "$enabled" != true ]; then
  echo "WebConsole is disabled by $config_dir; no toolchain or artifact was changed."
  exit 0
fi

actual_revision=$(git -C "$HOST_ROOT/webconsole" rev-parse HEAD)
expected_revision=$(python3 - "$HOST_ROOT/components.lock.yaml" <<'PY'
import sys, yaml
lock = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
matches = [item for item in lock["components"] if item["path"] == "webconsole"]
if len(matches) != 1:
    raise SystemExit("components.lock.yaml must contain one WebConsole entry")
print(matches[0]["commit"])
PY
)
test "$actual_revision" = "$expected_revision" || {
  echo "WebConsole revision mismatch: expected=$expected_revision actual=$actual_revision" >&2
  exit 1
}
test -z "$(git -C "$HOST_ROOT/webconsole" status --porcelain)" || {
  echo "WebConsole worktree must be clean before building an artifact" >&2
  exit 1
}

helper_hash=$(sha256sum "$HOST_ROOT/scripts/guest/webconsole-build.sh" | awk '{print $1}')
identity=$(printf '%s\n' \
  "source=$actual_revision" \
  "helper=$helper_hash" \
  "node=20" \
  "yarn=4.1.0" | sha256sum | awk '{print $1}')
remote_identity=$(vssh core "cat /var/lib/5g-nwdaf-infrastructure/webconsole/current/identity 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
if [ "$remote_identity" = "$identity" ]; then
  echo "WEBCONSOLE ARTIFACT state=reused identity=$identity revision=$actual_revision"
  exit 0
fi

temporary=$(mktemp -d)
cleanup() {
  rm -rf "$temporary"
}
trap cleanup EXIT
archive=$temporary/webconsole-source.tgz
tar -C "$HOST_ROOT/webconsole" \
  --exclude=.git --exclude=bin --exclude=public \
  --exclude=frontend/node_modules --exclude=frontend/build --exclude=frontend/.yarn/cache \
  -czf "$archive" .
archive_sha=$(sha256sum "$archive" | awk '{print $1}')
remote_archive=/tmp/5g-nwdaf-webconsole-"${archive_sha:0:16}".tgz
(cd "$HOST_ROOT" && provider_vagrant upload "$archive" "$remote_archive" core)
printf -v command \
  'sudo /usr/local/libexec/5g-nwdaf-infrastructure/webconsole-build %q %q %q %q' \
  "$identity" "$remote_archive" "$archive_sha" "$actual_revision"
vssh core "$command"
