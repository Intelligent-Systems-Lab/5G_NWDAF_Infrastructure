#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

source_type=all
vm=all
service='*'
since='10 minutes ago'
follow=true
tail_lines=all
while [ "$#" -gt 0 ]; do
  case "$1" in
    --source) source_type=$2; shift 2;;
    --vm) vm=$2; shift 2;;
    --service) service=$2; shift 2;;
    --since) since=$2; shift 2;;
    --tail) tail_lines=$2; shift 2;;
    --no-follow) follow=false; shift;;
    *) echo "usage: logs.sh [--source vm|ml|all] [--vm core|path-a|path-b|all] [--service name|glob|all] [--since value] [--tail lines|all] [--no-follow]" >&2; exit 2;;
  esac
done
case "$service" in ''|all) service='*';; esac
case "$source_type" in vm|ml|all) ;; *) echo "invalid source: $source_type" >&2; exit 2;; esac
case "$vm" in all) selected=(core path-a path-b);; core|path-a|path-b) selected=("$vm");; *) echo "invalid VM: $vm" >&2; exit 2;; esac
[[ "$service" =~ ^[A-Za-z0-9*?-]+$ ]] || { echo "invalid service filter" >&2; exit 2; }
[[ "$tail_lines" = all || "$tail_lines" =~ ^[0-9]+$ ]] || { echo "invalid tail value" >&2; exit 2; }
if ! absolute_since=$(normalize_log_since "$since" 2>/dev/null); then
  echo "invalid --since value: $since" >&2
  exit 2
fi
journal_since=$(journal_log_since "$absolute_since")

pids=()
cleanup() { [ "${#pids[@]}" -eq 0 ] || kill "${pids[@]}" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
if [ "$source_type" = vm ] || [ "$source_type" = all ]; then
  for machine in "${selected[@]}"; do
    mapfile -t vm_sources < <(vm_log_sources "$machine" "$service")
    if [ "${#vm_sources[@]}" -eq 0 ]; then
      continue
    fi
    journal_args=(sudo journalctl --since "$journal_since" -n "$tail_lines" --utc --no-pager -o short-iso-precise)
    $follow && journal_args+=(-f)
    for vm_source in "${vm_sources[@]}"; do
      journal_args+=(-u "${vm_source#*|}")
    done
    printf -v journal_command '%q ' "${journal_args[@]}"
    (
      vssh "$machine" "$journal_command" 2>&1 |
        sed -u "s/^/[$machine] /"
    ) &
    pids+=("$!")
  done
fi

if [ "$source_type" = ml ] || [ "$source_type" = all ]; then
  project=$(ml_project_name)
  mapfile -t ml_container_ids < <(
    docker ps -aq --filter "label=com.docker.compose.project=$project"
  )
  for container_id in "${ml_container_ids[@]}"; do
    ml_service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")
    if [[ "$ml_service" != $service ]]; then
      continue
    fi
    (
      docker_args=(logs --since "$absolute_since" --tail "$tail_lines" --timestamps)
      $follow && docker_args+=(--follow)
      docker "${docker_args[@]}" "$container_id" 2>&1 |
        sed -u "s/^/[ml:$ml_service] /"
    ) &
    pids+=("$!")
  done
fi

if [ "${#pids[@]}" -eq 0 ]; then
  echo "no matching log sources"
  exit 0
fi
wait
