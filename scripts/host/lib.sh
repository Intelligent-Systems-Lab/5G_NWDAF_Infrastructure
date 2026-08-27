#!/usr/bin/env bash
set -euo pipefail

HOST_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
MACHINES=(core path-a path-b)
CORE_UNITS=(mongodb nrf nssf udr udm ausf pcf amf smf adrf nwdaf-c)
PATH_A_UNITS=(upf-a nwdaf-a gnb-a ue1 ue2 ue3)
PATH_B_UNITS=(upf-b nwdaf-b gnb-b ue4 ue5 ue6)
ML_SERVICES=(pyanlf-a pyanlf-b pymtlf-a pymtlf-b pymtlf-c)

provider_host_context_available() {
  local device=${1:-/dev/vboxdrv}
  # Require the host VirtualBox device namespace before a provider client can touch shared host IPC state.
  [ -c "$device" ]
}

require_provider_host_context() {
  local device=/dev/vboxdrv
  if ! provider_host_context_available "$device"; then
    echo "provider execution refused: $device is not visible as a character device; use an approved host context" >&2
    return 126
  fi
}

provider_vagrant() {
  require_provider_host_context || return
  # Keep long-lived provider helpers from inheriting repository lifecycle lock descriptors.
  command vagrant "$@" 9>&-
}

provider_vboxmanage() {
  require_provider_host_context || return
  command VBoxManage "$@"
}

provider_process_records() {
  local raw status
  command -v pgrep >/dev/null || {
    echo "provider process inventory refused: pgrep is unavailable" >&2
    return 1
  }
  if raw=$(pgrep -a -u "$UID" -x VBoxHeadless 2>&1); then
    :
  else
    status=$?
    if [ "$status" -eq 1 ]; then
      raw=
    else
      echo "provider process inventory failed: $raw" >&2
      return 1
    fi
  fi
  printf '%s\n' "$raw" | python3 "$HOST_ROOT/scripts/host/provider-process-inventory.py"
}

provider_machine_uuid_records() {
  local metadata_root=${1:-$HOST_ROOT/.vagrant/machines}
  local machine path machine_uuid entry provider_dir
  local uuid_pattern='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
  declare -A seen=()
  if [ -e "$metadata_root" ]; then
    [ -d "$metadata_root" ] && [ -r "$metadata_root" ] || {
      echo "invalid Vagrant machine metadata root: $metadata_root" >&2
      return 1
    }
    for entry in "$metadata_root"/*; do
      [ -e "$entry" ] || continue
      machine=${entry##*/}
      [[ " ${MACHINES[*]} " == *" $machine "* ]] || {
        echo "unexpected Vagrant machine metadata: $machine" >&2
        return 1
      }
    done
  fi
  for machine in "${MACHINES[@]}"; do
    if [ -d "$metadata_root/$machine" ]; then
      for provider_dir in "$metadata_root/$machine"/*; do
        [ -e "$provider_dir" ] || continue
        [ "${provider_dir##*/}" = virtualbox ] || {
          echo "unexpected Vagrant provider metadata for $machine: ${provider_dir##*/}" >&2
          return 1
        }
      done
    fi
    path="$metadata_root/$machine/virtualbox/id"
    if [ ! -e "$path" ]; then
      printf '%s|\n' "$machine"
      continue
    fi
    [ -f "$path" ] && [ -r "$path" ] || {
      echo "invalid Vagrant UUID metadata for $machine: $path" >&2
      return 1
    }
    machine_uuid=$(<"$path")
    [[ "$machine_uuid" =~ $uuid_pattern ]] || {
      echo "invalid Vagrant UUID metadata for $machine: $path" >&2
      return 1
    }
    machine_uuid=${machine_uuid,,}
    if [ -n "${seen[$machine_uuid]:-}" ]; then
      echo "duplicate Vagrant UUID metadata: $machine and ${seen[$machine_uuid]} use $machine_uuid" >&2
      return 1
    fi
    seen[$machine_uuid]=$machine
    printf '%s|%s\n' "$machine" "$machine_uuid"
  done
}

validate_provider_runtime_inventory() {
  local processes=$1 metadata=$2 states=${3:-}
  local pid machine_uuid extra machine state
  local uuid_pattern='^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  declare -A process_counts=() metadata_uuids=() state_values=()

  while IFS='|' read -r pid machine_uuid extra; do
    [ -n "$pid$machine_uuid$extra" ] || continue
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] && [[ "$machine_uuid" =~ $uuid_pattern ]] && [ -z "$extra" ] || {
      echo "invalid provider process record: $pid|$machine_uuid${extra:+|$extra}" >&2
      return 1
    }
    process_counts[$machine_uuid]=$(( ${process_counts[$machine_uuid]:-0} + 1 ))
    if [ "${process_counts[$machine_uuid]}" -gt 1 ]; then
      echo "duplicate VBoxHeadless runtime for UUID $machine_uuid" >&2
      return 1
    fi
  done <<<"$processes"

  while IFS='|' read -r machine machine_uuid extra; do
    [ -n "$machine$machine_uuid$extra" ] || continue
    [[ " ${MACHINES[*]} " == *" $machine "* ]] && [ -z "$extra" ] || {
      echo "invalid Vagrant metadata record: $machine|$machine_uuid${extra:+|$extra}" >&2
      return 1
    }
    [ -z "${metadata_uuids[$machine]+set}" ] || {
      echo "duplicate Vagrant metadata record for $machine" >&2
      return 1
    }
    metadata_uuids[$machine]=$machine_uuid
  done <<<"$metadata"
  for machine in "${MACHINES[@]}"; do
    [ -n "${metadata_uuids[$machine]+set}" ] || {
      echo "Vagrant metadata omitted machine: $machine" >&2
      return 1
    }
  done

  [ -n "$states" ] || return 0
  while IFS='|' read -r machine state extra; do
    [ -n "$machine$state$extra" ] || continue
    [[ " ${MACHINES[*]} " == *" $machine "* ]] && [ -n "$state" ] && [ -z "$extra" ] || {
      echo "invalid Vagrant state record: $machine|$state${extra:+|$extra}" >&2
      return 1
    }
    [ -z "${state_values[$machine]+set}" ] || {
      echo "duplicate Vagrant state record for $machine" >&2
      return 1
    }
    state_values[$machine]=$state
  done <<<"$states"

  for machine in "${MACHINES[@]}"; do
    [ -n "${state_values[$machine]+set}" ] || {
      echo "Vagrant state omitted machine: $machine" >&2
      return 1
    }
    machine_uuid=${metadata_uuids[$machine]}
    state=${state_values[$machine]}
    if [ -z "$machine_uuid" ]; then
      [ "$state" = not_created ] || {
        echo "provider/Vagrant metadata mismatch for $machine: state=$state UUID=missing" >&2
        return 1
      }
      continue
    fi
    case "$state" in
      running)
        [ "${process_counts[$machine_uuid]:-0}" -eq 1 ] || {
          echo "provider/process mismatch for $machine: state=running process-count=${process_counts[$machine_uuid]:-0}" >&2
          return 1
        }
        ;;
      poweroff|saved|aborted)
        [ "${process_counts[$machine_uuid]:-0}" -eq 0 ] || {
          echo "provider/process mismatch for $machine: state=$state process-count=${process_counts[$machine_uuid]:-0}" >&2
          return 1
        }
        ;;
      not_created)
        echo "provider/Vagrant metadata mismatch for $machine: state=not_created UUID=$machine_uuid" >&2
        return 1
        ;;
      *)
        echo "provider runtime preflight refused unsupported state for $machine: $state" >&2
        return 1
        ;;
    esac
  done
}

provider_live_vm_state_records() {
  local raw machine state count total
  if ! raw=$(cd "$HOST_ROOT" && TESTBED="${TESTBED:-testbed.yaml}" provider_vagrant status --machine-readable); then
    echo "failed to query Vagrant machine states" >&2
    return 1
  fi
  total=$(awk -F, '$3=="state" {count++} END {print count+0}' <<<"$raw")
  [ "$total" -eq "${#MACHINES[@]}" ] || {
    echo "Vagrant status returned $total total machine state records; expected ${#MACHINES[@]}" >&2
    return 1
  }
  for machine in "${MACHINES[@]}"; do
    count=$(awk -F, -v wanted="$machine" '$2==wanted && $3=="state" {count++} END {print count+0}' <<<"$raw")
    [ "$count" -eq 1 ] || {
      echo "Vagrant status returned $count state records for machine: $machine" >&2
      return 1
    }
    state=$(awk -F, -v wanted="$machine" '$2==wanted && $3=="state" {print $4}' <<<"$raw")
    printf '%s|%s\n' "$machine" "$state"
  done
}

provider_vm_up_preflight() {
  local metadata_root=${1:-$HOST_ROOT/.vagrant/machines}
  local processes_before processes_after metadata states
  require_provider_host_context || return
  processes_before=$(provider_process_records) || return
  metadata=$(provider_machine_uuid_records "$metadata_root") || return
  validate_provider_runtime_inventory "$processes_before" "$metadata" || return
  states=$(provider_live_vm_state_records) || return
  processes_after=$(provider_process_records) || return
  [ "$processes_before" = "$processes_after" ] || {
    echo "provider process inventory changed during Vagrant state query; refusing vm-up" >&2
    return 1
  }
  validate_provider_runtime_inventory "$processes_after" "$metadata" "$states"
}

provider_vagrant_up() {
  local lock_root=${XDG_RUNTIME_DIR:-/tmp}/5g-nwdaf-infrastructure-$UID
  require_provider_host_context || return
  mkdir -p "$lock_root" && chmod 700 "$lock_root" || {
    echo "provider runtime preflight cannot secure lifecycle lock directory: $lock_root" >&2
    return 1
  }
  (
    flock 9 || return
    provider_vm_up_preflight || return
    provider_vagrant up "$@"
  ) 9>"$lock_root/vagrant-up.lock"
}

vm_log_sources() {
  local machine=$1 filter=$2 logical unit
  local -a template_units=()
  local -a special_sources=()
  case "$machine" in
    core)
      template_units=("${CORE_UNITS[@]}" webconsole)
      special_sources=(
        'consumer|5g-nwdaf-consumer.service'
        'network|5g-nwdaf-network.service'
      )
      ;;
    path-a)
      template_units=("${PATH_A_UNITS[@]}")
      special_sources=('network|5g-nwdaf-network.service')
      ;;
    path-b)
      template_units=("${PATH_B_UNITS[@]}")
      special_sources=('network|5g-nwdaf-network.service')
      ;;
    *)
      echo "unknown VM for log source resolution: $machine" >&2
      return 2
      ;;
  esac

  for logical in "${template_units[@]}"; do
    if [[ "$logical" == $filter ]]; then
      printf '%s|5g-nwdaf@%s.service\n' "$logical" "$logical"
    fi
  done
  for unit in "${special_sources[@]}"; do
    logical=${unit%%|*}
    if [[ "$logical" == $filter ]]; then
      printf '%s\n' "$unit"
    fi
  done
}

normalize_log_since() {
  local value=$1 epoch
  epoch=$(date --date "$value" '+%s') || return
  date --utc --date "@$epoch" '+%Y-%m-%dT%H:%M:%SZ'
}

journal_log_since() {
  local canonical_utc=$1
  canonical_utc=${canonical_utc/T/ }
  printf '%s UTC\n' "${canonical_utc%Z}"
}

vm_state_records() {
  local raw machine state
  if [ -n "${VM_STATE_RECORDS_FILE:-}" ]; then
    [ -r "$VM_STATE_RECORDS_FILE" ] || {
      echo "cached VM state is not readable: $VM_STATE_RECORDS_FILE" >&2
      return 1
    }
    cat "$VM_STATE_RECORDS_FILE"
    return
  fi
  provider_live_vm_state_records
}

vm_state_for() {
  local wanted=$1 machine state records
  records=$(vm_state_records) || return
  while IFS='|' read -r machine state; do
    if [ "$machine" = "$wanted" ]; then
      printf '%s\n' "$state"
      return 0
    fi
  done <<<"$records"
  echo "Vagrant status omitted machine: $wanted" >&2
  return 1
}

ue_readiness_states() {
  local service_state=$1 journal=${2:-}
  local registration pdu_session
  case "$service_state" in
    not-running)
      printf 'not-running|not-running\n'
      return
      ;;
    inactive|unknown|'')
      printf 'inactive|inactive\n'
      return
      ;;
    failed)
      printf 'failed|failed\n'
      return
      ;;
    active)
      registration=pending
      pdu_session=pending
      ;;
    *)
      printf 'pending|pending\n'
      return
      ;;
  esac

  if [[ "$journal" == *'Initial Registration is successful'* ]]; then
    registration=successful
  elif [[ "$journal" == *'Initial Registration failed ['* ]]; then
    registration=failed
  fi

  if [[ "$journal" == *'PDU Session establishment is successful PSI['* ]]; then
    pdu_session=successful
  elif [[ "$journal" == *'PDU Session Establishment Reject received ['* ||
          "$journal" == *'PDU Session Establishment procedure failure'* ||
          "$journal" == *'PDU session allocation failed'* ]]; then
    pdu_session=failed
  fi

  printf '%s|%s\n' "$registration" "$pdu_session"
}

vssh() {
  local machine=$1 command=$2
  local lock_root=${XDG_RUNTIME_DIR:-/tmp}/5g-nwdaf-infrastructure-$UID
  mkdir -p "$lock_root"
  chmod 700 "$lock_root"
  (
    flock 9
    cd "$HOST_ROOT"
    provider_vagrant ssh "$machine" -c "$command" </dev/null
  ) 9>"$lock_root/vagrant-$machine.lock"
}

consumer_cli() {
  local action=$1
  case "$action" in status|delete) ;; *) echo "invalid consumer action: $action" >&2; return 2;; esac
  vssh core "sudo -u 5g-nwdaf /usr/local/libexec/5g-nwdaf-infrastructure/nwdaf-consumer --config /etc/5g-nwdaf-infrastructure/active/consumer.yaml '$action'"
}

consumer_unit_active() {
  local core_state
  core_state=$(vm_state_for core) || return
  [ "$core_state" = running ] || return 1
  vssh core "systemctl is-active --quiet 5g-nwdaf-consumer.service" >/dev/null 2>&1
}

unit_action() {
  local machine=$1 action=$2 unit=$3
  vssh "$machine" "sudo systemctl $action 5g-nwdaf@$unit.service"
}

wait_active() {
  local machine=$1 unit=$2 attempt
  for attempt in $(seq 1 30); do
    if vssh "$machine" "systemctl is-active --quiet 5g-nwdaf@$unit.service" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "$machine/$unit did not become active" >&2
  vssh "$machine" "sudo journalctl -u 5g-nwdaf@$unit.service -n 40 --no-pager" >&2 || true
  return 1
}

start_unit() {
  local machine=$1 unit=$2
  echo "START $machine/$unit"
  unit_action "$machine" start "$unit"
  wait_active "$machine" "$unit"
}

stop_unit() {
  local machine=$1 unit=$2
  echo "STOP  $machine/$unit"
  unit_action "$machine" stop "$unit" || true
}

config_hash() {
  python3 "$HOST_ROOT/scripts/shared/config_hash.py" "$1"
}

effective_config_dir() {
  local testbed=$1 explicit=${2:-}
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" "$explicit" <<'PY'
import sys
from configlib import load_yaml, resolve_config_dir, resolve_path
definition = load_yaml(resolve_path(sys.argv[1]))
print(resolve_config_dir(definition, sys.argv[2] or None))
PY
}

effective_ml_bind_address() {
  local testbed=$1
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_ml_bind_address, resolve_path
definition = load_yaml(resolve_path(sys.argv[1]))
print(resolve_ml_bind_address(definition))
PY
}

ml_project_name() {
  local project=${ML_PROJECT_NAME:-5g-nwdaf-infrastructure}
  [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || {
    echo "invalid ML project name: $project" >&2
    return 2
  }
  printf '%s\n' "$project"
}

ml_runtime_mode() {
  local mode=${ML_RUNTIME_MODE:-baseline}
  case "$mode" in
    baseline|cpu-smoke) printf '%s\n' "$mode" ;;
    *) echo "invalid ML runtime mode: $mode" >&2; return 2 ;;
  esac
}

config_ml_device_policy() {
  local config_dir=$1
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import resolve_ml_device_policy, resolve_path
print(resolve_ml_device_policy(resolve_path(sys.argv[1])))
PY
}

config_webconsole_enabled() {
  local config_dir=$1
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import load_yaml, resolve_path
manifest = load_yaml(resolve_path(sys.argv[1]) / "manifest.yaml")
enabled = manifest.get("optionalServices", {}).get("webconsole", {}).get("enabled")
if not isinstance(enabled, bool):
    raise SystemExit("optionalServices.webconsole.enabled must be boolean")
print(str(enabled).lower())
PY
}

config_webconsole_endpoint() {
  local config_dir=$1
  PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$config_dir" <<'PY'
import sys
from configlib import load_yaml, resolve_path
config = load_yaml(resolve_path(sys.argv[1]) / "webuicfg.yaml")["configuration"]["webServer"]
print(config["ipv4Address"], config["port"])
PY
}

ml_device_policy() {
  local policy=${ML_DEVICE_POLICY:-}
  case "$policy" in
    cpu|gpu) printf '%s\n' "$policy" ;;
    "") echo "ML_DEVICE_POLICY is unset; resolve it from the selected config first" >&2; return 2 ;;
    *) echo "invalid ML device policy: $policy" >&2; return 2 ;;
  esac
}

ml_compose() {
  local project policy
  local -a command
  project=$(ml_project_name)
  policy=$(ml_device_policy)
  command=(docker compose -p "$project" -f "$HOST_ROOT/compose.yaml")
  if [ "$policy" = cpu ]; then
    command+=(-f "$HOST_ROOT/compose.cpu.yaml")
  fi
  if [ "$(ml_runtime_mode)" = cpu-smoke ]; then
    command+=(-f "$HOST_ROOT/compose.cpu-smoke.yaml")
  fi
  "${command[@]}" "$@"
}

host_has_address() {
  local address=$1
  ip -j address show | python3 -c '
import json, sys
target = sys.argv[1]
addresses = {
    item.get("local")
    for interface in json.load(sys.stdin)
    for item in interface.get("addr_info", [])
}
raise SystemExit(0 if target in addresses else 1)
' "$address"
}

ml_host_resource_gate() {
  local testbed=$1
  local reserve_mib swap_policy minimum_swap_mib minimum_storage_gib
  local available_mib swap_free_mib docker_root docker_free_gib
  read -r reserve_mib swap_policy minimum_swap_mib minimum_storage_gib < <(
    PYTHONPATH="$HOST_ROOT/scripts/host" python3 - "$testbed" <<'PY'
import sys
from configlib import load_yaml, resolve_path
safety = load_yaml(resolve_path(sys.argv[1]))["hostSafety"]
print(
    safety["reserveMemoryMiB"],
    safety["swapPolicy"],
    safety["minimumFreeSwapMiB"],
    safety["minimumFreeStorageGiB"],
)
PY
  )
  available_mib=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
  swap_free_mib=$(awk '/SwapFree:/ {print int($2/1024)}' /proc/meminfo)
  docker_root=$(docker info --format '{{.DockerRootDir}}')
  docker_free_gib=$(df -Pk "$docker_root" | awk 'NR==2 {print int($4/1024/1024)}')

  if [ "$available_mib" -lt "$reserve_mib" ]; then
    echo "available RAM ${available_mib}MiB is below ML/Host reserve ${reserve_mib}MiB" >&2
    return 1
  fi
  if [ "$docker_free_gib" -lt "$minimum_storage_gib" ]; then
    echo "Docker data-root free ${docker_free_gib}GiB is below ${minimum_storage_gib}GiB" >&2
    return 1
  fi
  if [ "$swap_free_mib" -lt "$minimum_swap_mib" ]; then
    if [ "$swap_policy" = require ]; then
      echo "free swap ${swap_free_mib}MiB is below required ${minimum_swap_mib}MiB" >&2
      return 1
    fi
    echo "WARN free swap ${swap_free_mib}MiB is below ${minimum_swap_mib}MiB" >&2
  fi
  echo "ML HOST available_ram=${available_mib}MiB reserve=${reserve_mib}MiB docker_free=${docker_free_gib}GiB"
}

ml_runtime_gate() {
  local policy cdi_device cdi_inventory
  policy=$(ml_device_policy)
  if [ "$policy" = cpu ]; then
    echo "ML RUNTIME device_policy=cpu nvidia=not-required"
    return 0
  fi

  cdi_device=nvidia.com/gpu=all
  if ! command -v nvidia-ctk >/dev/null 2>&1; then
    echo "NVIDIA CDI prerequisite is missing: nvidia-ctk was not found" >&2
    return 1
  fi
  cdi_inventory=$(nvidia-ctk cdi list)
  if ! grep -Fxq "$cdi_device" <<<"$cdi_inventory"; then
    echo "NVIDIA CDI device is unavailable: $cdi_device" >&2
    printf '%s\n' "$cdi_inventory" >&2
    return 1
  fi
  if ! docker info --format '{{json .Runtimes}}' | python3 -c \
    'import json, sys; raise SystemExit(0 if "nvidia" in json.load(sys.stdin) else 1)'; then
    echo "NVIDIA Docker runtime is unavailable; register it and reload Docker" >&2
    return 1
  fi
  echo "ML RUNTIME device_policy=gpu cdi=$cdi_device docker_runtime=nvidia"
}

stage_config_all() {
  local config_dir=$1 hash=$2 name archive temporary machine destination
  name=$(basename "$config_dir")
  destination="/etc/5g-nwdaf-infrastructure/config-sets/${name}-${hash:0:16}"
  temporary=$(mktemp -d)
  archive="$temporary/config.tgz"
  trap 'rm -rf "$temporary"' RETURN
  tar -C "$config_dir" -czf "$archive" .
  for machine in "${MACHINES[@]}"; do
    echo "STAGE $machine $destination"
    (cd "$HOST_ROOT" && provider_vagrant upload "$archive" "/tmp/5g-nwdaf-config-${hash:0:16}.tgz" "$machine")
    vssh "$machine" "sudo rm -rf '$destination' && sudo install -d '$destination' && sudo tar -C '$destination' -xzf '/tmp/5g-nwdaf-config-${hash:0:16}.tgz' && sudo rm -f '/tmp/5g-nwdaf-config-${hash:0:16}.tgz' && sudo /usr/local/libexec/5g-nwdaf-infrastructure/config-activate '$machine' '$destination' '$hash'"
  done
  trap - RETURN
  rm -rf "$temporary"
}

print_unit_status() {
  local machine=$1 unit=$2 state
  state=$(vssh "$machine" "systemctl is-active 5g-nwdaf@$unit.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
  printf '%-8s %-14s %s\n' "$machine" "$unit" "${state:-unknown}"
}
