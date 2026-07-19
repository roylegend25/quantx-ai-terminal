#!/usr/bin/env bash
set -euo pipefail

image_ref=${1:?usage: clock_preflight.sh IMAGE_REF HEALTH_URL}
health_url=${2:?usage: clock_preflight.sh IMAGE_REF HEALTH_URL}

ntp_synced=$(timedatectl show -p NTPSynchronized --value)
if [[ "$ntp_synced" != "yes" ]]; then
  echo "clock_preflight=failed reason=host_ntp_unsynchronized"
  exit 1
fi

if ! chronyc tracking | rg -q '^Leap status[[:space:]]*:[[:space:]]*Normal$'; then
  echo "clock_preflight=failed reason=chrony_unhealthy"
  exit 1
fi

host_ms=$(( $(date +%s%N) / 1000000 ))
container_ms=$(( $(docker run --rm --entrypoint date "$image_ref" +%s%N) / 1000000 ))
delta_ms=$(( container_ms - host_ms ))
abs_delta_ms=${delta_ms#-}
if (( abs_delta_ms > 1000 )); then
  echo "clock_preflight=failed reason=host_container_delta delta_ms=$delta_ms"
  exit 1
fi

health=$(curl -fsS "$health_url")
binance_status=$(jq -r '.binance_time.status // "missing"' <<<"$health")
if [[ "$binance_status" != "synced" ]]; then
  echo "clock_preflight=failed reason=binance_time_status status=$binance_status"
  exit 1
fi

offset_ms=$(jq -r '.binance_time.offset_ms' <<<"$health")
rtt_ms=$(jq -r '.binance_time.round_trip_ms' <<<"$health")
echo "clock_preflight=passed ntp=yes host_container_delta_ms=$delta_ms binance_status=$binance_status offset_ms=$offset_ms rtt_ms=$rtt_ms"
