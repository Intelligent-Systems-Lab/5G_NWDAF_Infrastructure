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
    *) echo "usage: logs.sh [--source vm|ml|all] [--vm core|path-a|path-b|all] [--service name|glob] [--since value] [--tail lines|all] [--no-follow]" >&2; exit 2;;
  esac
done
case "$source_type" in vm|ml|all) ;; *) echo "invalid source: $source_type" >&2; exit 2;; esac
case "$vm" in all) selected=(core path-a path-b);; core|path-a|path-b) selected=("$vm");; *) echo "invalid VM: $vm" >&2; exit 2;; esac
[[ "$service" =~ ^[A-Za-z0-9*?-]+$ ]] || { echo "invalid service filter" >&2; exit 2; }
[[ "$tail_lines" = all || "$tail_lines" =~ ^[0-9]+$ ]] || { echo "invalid tail value" >&2; exit 2; }

pids=()
cleanup() { [ "${#pids[@]}" -eq 0 ] || kill "${pids[@]}" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
printf -v remote_since '%q' "$since"
if [ "$source_type" = vm ] || [ "$source_type" = all ]; then
  journal_follow=(-f)
  $follow || journal_follow=()
  for machine in "${selected[@]}"; do
    (
      vssh "$machine" "sudo journalctl ${journal_follow[*]} --since $remote_since -n '$tail_lines' -u '5g-nwdaf@$service.service' -o cat" 2>&1 |
        sed -u "s/^/[$machine] /"
    ) &
    pids+=("$!")
  done
fi

if [ "$source_type" = ml ] || [ "$source_type" = all ]; then
  project=$(ml_project_name)
  docker_since=$(date --date "$since" --iso-8601=seconds)
  mapfile -t ml_container_ids < <(
    docker ps -aq --filter "label=com.docker.compose.project=$project"
  )
  for container_id in "${ml_container_ids[@]}"; do
    ml_service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")
    if [[ "$ml_service" != $service ]]; then
      continue
    fi
    (
      docker_args=(logs --since "$docker_since" --tail "$tail_lines")
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
