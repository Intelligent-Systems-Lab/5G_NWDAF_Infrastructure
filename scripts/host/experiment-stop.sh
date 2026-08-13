#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

cleanup_timeout=${ML_CLEANUP_TIMEOUT_SECONDS:-${ML_CLEANUP_GRACE_SECONDS:-210}}
cleanup_poll=2
if ! [[ "$cleanup_timeout" =~ ^[0-9]+$ ]]; then
  echo "ML_CLEANUP_TIMEOUT_SECONDS must be a non-negative integer (got: $cleanup_timeout)." >&2
  exit 2
fi
if [ -n "${ML_CLEANUP_GRACE_SECONDS:-}" ] && [ -z "${ML_CLEANUP_TIMEOUT_SECONDS:-}" ]; then
  echo "WARN ML_CLEANUP_GRACE_SECONDS is deprecated; use ML_CLEANUP_TIMEOUT_SECONDS." >&2
fi

cleanup_log_file=""
cleanup_log_follower_pid=""
stop_cleanup_log_follower() {
  if [ -n "$cleanup_log_follower_pid" ]; then
    kill "$cleanup_log_follower_pid" >/dev/null 2>&1 || true
    wait "$cleanup_log_follower_pid" 2>/dev/null || true
    cleanup_log_follower_pid=""
  fi
  if [ -n "$cleanup_log_file" ]; then
    rm -f "$cleanup_log_file"
    cleanup_log_file=""
  fi
}
trap stop_cleanup_log_follower EXIT

consumer=$(vssh core "systemctl is-active 5g-nwdaf-consumer.service 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -n 1)
if [ "$consumer" = active ]; then
  pymtlf_c_container=""
  monitor_snapshot=""
  cleanup_verification_available=false
  expected_monitor_subscriptions=()
  if pymtlf_c_container=$(ml_container_id pymtlf-c); then
    if monitor_snapshot=$(ml_monitor_active_subscription_ids "$pymtlf_c_container"); then
      cleanup_verification_available=true
      if [ -n "$monitor_snapshot" ]; then
        mapfile -t expected_monitor_subscriptions <<<"$monitor_snapshot"
      fi
      echo "Tracking ${#expected_monitor_subscriptions[@]} active PyMTLF-C Model Monitor subscription(s) during cleanup."
    else
      pymtlf_c_container=""
      echo "WARN PyMTLF-C cleanup logs are unavailable; aggregate stop will continue without log-based verification." >&2
    fi
  else
    echo "WARN PyMTLF-C cleanup container is unavailable; aggregate stop will continue without log-based verification." >&2
  fi
  if [ "$cleanup_verification_available" = true ] && \
     [ "${#expected_monitor_subscriptions[@]}" -gt 0 ] && \
     [ "$cleanup_timeout" -gt 0 ]; then
    cleanup_log_file=$(mktemp)
    docker logs --tail 1 --follow "$pymtlf_c_container" >"$cleanup_log_file" 2>&1 &
    cleanup_log_follower_pid=$!
    for _ in $(seq 1 50); do
      if [ -s "$cleanup_log_file" ]; then
        break
      fi
      if ! kill -0 "$cleanup_log_follower_pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    if [ ! -s "$cleanup_log_file" ] || ! kill -0 "$cleanup_log_follower_pid" 2>/dev/null; then
      echo "WARN PyMTLF-C log follower did not attach; aggregate stop will continue without log-based verification." >&2
      cleanup_verification_available=false
      stop_cleanup_log_follower
    fi
  fi
  "$HOST_ROOT/scripts/host/subscriptions-stop.sh"
  if [ "$cleanup_verification_available" = true ]; then
    if [ "${#expected_monitor_subscriptions[@]}" -eq 0 ]; then
      echo "No active PyMTLF-C Model Monitor subscriptions required cleanup verification."
    elif [ "$cleanup_timeout" -eq 0 ]; then
      echo "WARN ML Model Monitor cleanup verification was disabled with ML_CLEANUP_TIMEOUT_SECONDS=0." >&2
    else
      echo "Waiting up to ${cleanup_timeout}s for asynchronous ML Model Monitor cleanup."
      wait_for_ml_monitor_cleanup \
        "$cleanup_log_file" "$cleanup_log_follower_pid" "$cleanup_timeout" "$cleanup_poll" \
        "${expected_monitor_subscriptions[@]}" || true
      stop_cleanup_log_follower
    fi
  fi
else
  echo "Consumer is not active; no subscriptions were changed."
fi
"$HOST_ROOT/scripts/host/webconsole-stop.sh"
"$HOST_ROOT/scripts/host/ml-stop.sh"
"$HOST_ROOT/scripts/host/services-stop.sh"

echo "Experiment processes stopped; VMs, datasets, databases, artifacts, containers, images, and volumes were retained."
