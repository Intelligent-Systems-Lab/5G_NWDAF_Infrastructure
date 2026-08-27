#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/host/lib.sh"

provider_fixture=$(mktemp -d)
trap 'rm -rf "$provider_fixture"' EXIT
parser="$HOST_ROOT/scripts/host/provider-process-inventory.py"
uuid_core=11111111-1111-4111-8111-111111111111
uuid_path_a=22222222-2222-4222-8222-222222222222
uuid_path_b=33333333-3333-4333-8333-333333333333
metadata_root="$provider_fixture/machines"
export XDG_RUNTIME_DIR="$provider_fixture/runtime"
mkdir -p "$XDG_RUNTIME_DIR"
require_provider_host_context() { :; }
provider_probe_bin="$provider_fixture/bin"
mkdir -p "$provider_probe_bin"
cat >"$provider_probe_bin/vagrant" <<'EOF'
#!/usr/bin/env bash
if [ -e "/proc/$$/fd/9" ]; then
  exit 88
fi
EOF
chmod +x "$provider_probe_bin/vagrant"
original_path=$PATH
PATH="$provider_probe_bin:$PATH"
if ! (
  flock 9
  provider_vagrant probe
) 9>"$provider_fixture/provider-lock"; then
  echo "provider wrapper leaked its lifecycle lock fd into the provider child" >&2
  exit 1
fi
PATH=$original_path
for record in "core:$uuid_core" "path-a:$uuid_path_a" "path-b:$uuid_path_b"; do
  machine=${record%%:*}
  uuid=${record#*:}
  mkdir -p "$metadata_root/$machine/virtualbox"
  printf '%s' "$uuid" >"$metadata_root/$machine/virtualbox/id"
done

parsed=$(printf '%s\n' \
  "101 /usr/lib/virtualbox/VBoxHeadless --comment core --startvm $uuid_core --vrde config" \
  "202 VBoxHeadless --startvm=$uuid_path_a" |
  python3 "$parser")
[ "$parsed" = "$(printf '%s\n' "101|$uuid_core" "202|$uuid_path_a")" ] || {
  echo "provider process parser did not produce canonical PID/UUID records" >&2
  exit 1
}
if printf '%s\n' '303 VBoxHeadless --comment broken' | python3 "$parser" >/dev/null 2>&1; then
  echo "provider process parser accepted a missing --startvm UUID" >&2
  exit 1
fi
if printf '%s\n' '404 VBoxHeadless --startvm not-a-uuid' | python3 "$parser" >/dev/null 2>&1; then
  echo "provider process parser accepted an invalid UUID" >&2
  exit 1
fi
if printf '%s\n' "505 VBoxHeadless --startvm {$uuid_core" | python3 "$parser" >/dev/null 2>&1; then
  echo "provider process parser accepted mismatched UUID braces" >&2
  exit 1
fi

pgrep() { return 1; }
[ -z "$(provider_process_records)" ] || {
  echo "empty provider process inventory produced records" >&2
  exit 1
}
pgrep() { return 2; }
if provider_process_records >/dev/null 2>&1; then
  echo "provider process inventory accepted a pgrep failure" >&2
  exit 1
fi
unset -f pgrep

provider_vagrant() {
  [ "$*" = 'status --machine-readable' ] || return 99
  printf '%s\n' \
    '1,core,state,running' \
    '1,path-a,state,poweroff' \
    '1,path-b,state,not_created'
}
states=$(provider_live_vm_state_records)
[ "$states" = "$(printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|not_created')" ] || {
  echo "provider live state parser did not return the exact machine inventory" >&2
  exit 1
}
provider_vagrant() {
  printf '%s\n' \
    '1,core,state,running' \
    '1,path-a,state,poweroff' \
    '1,path-b,state,poweroff' \
    '1,unexpected,state,running'
}
if provider_live_vm_state_records >/dev/null 2>&1; then
  echo "provider live state parser accepted an unexpected machine" >&2
  exit 1
fi

provider_process_records() { :; }
provider_live_vm_state_records() {
  printf '%s\n' 'core|poweroff' 'path-a|poweroff' 'path-b|poweroff'
}
provider_vm_up_preflight "$metadata_root"

provider_process_records() { printf '%s\n' "101|$uuid_core"; }
provider_live_vm_state_records() {
  printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|poweroff'
}
provider_vm_up_preflight "$metadata_root"

provider_process_records() { printf '%s\n' "101|$uuid_core" "102|$uuid_core"; }
provider_live_vm_state_records() {
  touch "$provider_fixture/provider-query"
  printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|poweroff'
}
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted duplicate VBoxHeadless UUIDs" >&2
  exit 1
fi
if [ -e "$provider_fixture/provider-query" ]; then
  echo "provider preflight queried Vagrant before rejecting duplicate OS processes" >&2
  exit 1
fi

mkdir -p "$metadata_root/unexpected/virtualbox"
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted unexpected Vagrant machine metadata" >&2
  exit 1
fi
rmdir "$metadata_root/unexpected/virtualbox" "$metadata_root/unexpected"

provider_process_records() { printf '%s\n' "101|$uuid_core"; }
provider_live_vm_state_records() {
  printf '%s\n' 'core|poweroff' 'path-a|poweroff' 'path-b|poweroff'
}
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted an orphan process/provider-state mismatch" >&2
  exit 1
fi

provider_process_records() { :; }
provider_live_vm_state_records() {
  printf '%s\n' 'core|running' 'path-a|poweroff' 'path-b|poweroff'
}
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted running provider state without an OS process" >&2
  exit 1
fi

snapshot_count="$provider_fixture/snapshot-count"
printf '0\n' >"$snapshot_count"
provider_process_records() {
  count=$(<"$snapshot_count")
  count=$((count + 1))
  printf '%s\n' "$count" >"$snapshot_count"
  [ "$count" -eq 1 ] || printf '%s\n' "101|$uuid_core"
}
provider_live_vm_state_records() {
  printf '%s\n' 'core|poweroff' 'path-a|poweroff' 'path-b|poweroff'
}
if provider_vm_up_preflight "$metadata_root" >/dev/null 2>&1; then
  echo "provider preflight accepted a process inventory change during the provider query" >&2
  exit 1
fi

invocation_log="$provider_fixture/up-invocation.log"
provider_vm_up_preflight() { printf '%s\n' preflight >>"$invocation_log"; return 1; }
provider_vagrant() { printf 'provider|%s\n' "$*" >>"$invocation_log"; }
if provider_vagrant_up; then
  echo "provider up wrapper ignored a rejected runtime preflight" >&2
  exit 1
fi
[ "$(<"$invocation_log")" = preflight ] || {
  echo "provider up wrapper started Vagrant after preflight rejection" >&2
  exit 1
}
: >"$invocation_log"
provider_vm_up_preflight() { printf '%s\n' preflight >>"$invocation_log"; }
provider_vagrant() {
  printf 'provider|%s\n' "$*" >>"$invocation_log"
}
provider_vagrant_up
[ "$(<"$invocation_log")" = "$(printf '%s\n' preflight 'provider|up')" ] || {
  echo "provider up wrapper did not preserve preflight-before-provider ordering" >&2
  exit 1
}

echo "PROVIDER_RUNTIME_PREFLIGHT_TEST status=passed provider_process=mocked"
