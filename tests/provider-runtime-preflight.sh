#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/host/lib.sh"

export TESTBED=testbed.protocol-hierarchical.yaml
select_testbed_machines "$TESTBED"
provider_fixture=$(mktemp -d)
trap 'rm -rf "$provider_fixture"' EXIT
parser="$HOST_ROOT/scripts/host/provider-process-inventory.py"
metadata_root="$provider_fixture/machines"
export XDG_RUNTIME_DIR="$provider_fixture/runtime"
mkdir -p "$XDG_RUNTIME_DIR"
require_provider_host_context() { :; }

declare -A uuids=(
  [core]=11111111-1111-4111-8111-111111111111
  [path-a]=22222222-2222-4222-8222-222222222222
  [path-b]=33333333-3333-4333-8333-333333333333
  [path-c]=44444444-4444-4444-8444-444444444444
)
for machine in "${MACHINES[@]}"; do
  mkdir -p "$metadata_root/$machine/virtualbox"
  printf '%s' "${uuids[$machine]}" >"$metadata_root/$machine/virtualbox/id"
done

parsed=$(printf '%s\n' \
  "101 /usr/lib/virtualbox/VBoxHeadless --startvm ${uuids[core]}" \
  "202 VBoxHeadless --startvm=${uuids[path-a]}" | python3 "$parser")
[ "$parsed" = "$(printf '%s\n' "101|${uuids[core]}" "202|${uuids[path-a]}")" ]
if printf '%s\n' '303 VBoxHeadless --comment broken' | python3 "$parser" >/dev/null 2>&1; then
  echo "provider process parser accepted a missing VM UUID" >&2
  exit 1
fi

all_states() {
  local core_state=$1 machine state
  for machine in "${MACHINES[@]}"; do
    state=poweroff
    [ "$machine" = core ] && state=$core_state
    printf '%s|%s\n' "$machine" "$state"
  done
}

provider_process_records() { :; }
provider_live_vm_state_records() { all_states poweroff; }
provider_vm_up_preflight "$metadata_root"

provider_process_records() { printf '%s\n' "101|${uuids[core]}"; }
provider_live_vm_state_records() { all_states running; }
provider_vm_up_preflight "$metadata_root"

provider_process_records() {
  printf '%s\n' "101|${uuids[core]}" "102|${uuids[core]}"
}
provider_live_vm_state_records() {
  touch "$provider_fixture/provider-query"
  all_states running
}
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted duplicate VBoxHeadless UUIDs" >&2
  exit 1
fi
if [ -e "$provider_fixture/provider-query" ]; then
  echo "provider preflight queried Vagrant before rejecting duplicate OS processes" >&2
  exit 1
fi

provider_process_records() { printf '%s\n' "101|${uuids[core]}"; }
provider_live_vm_state_records() { all_states poweroff; }
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted an orphan process/provider-state mismatch" >&2
  exit 1
fi

provider_process_records() { :; }
provider_live_vm_state_records() { all_states running; }
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted running provider state without an OS process" >&2
  exit 1
fi

invocation_log="$provider_fixture/invocation.log"
provider_vm_up_preflight() { printf '%s\n' preflight >>"$invocation_log"; }
provider_vagrant() { printf 'provider|%s\n' "$*" >>"$invocation_log"; }
provider_vagrant_up
[ "$(<"$invocation_log")" = "$(printf '%s\n' preflight 'provider|up')" ]

: >"$invocation_log"
provider_runtime_state_records() { printf '%s\n' preflight >>"$invocation_log"; }
provider_vagrant_halt
[ "$(<"$invocation_log")" = "$(printf '%s\n' preflight 'provider|halt')" ]

echo "PROVIDER_RUNTIME_PREFLIGHT_TEST machines=${#MACHINES[@]} provider_process=mocked status=passed"
