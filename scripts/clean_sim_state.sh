#!/usr/bin/env bash
# Сбросить «грязное» состояние симуляции, оставшееся после прерванного демо
# (Ctrl+C / kill / timeout не всегда чистят netns + Docker-контейнеры).
# Запускать перед демо, если предыдущее упало/прервалось и новое не стартует.
#
#   sudo bash scripts/clean_sim_state.sh
#
# Идемпотентно и безопасно: гасит только наши процессы/неймспейсы/контейнеры.
set +e

if [ "$EUID" -ne 0 ]; then
  echo "needs sudo (netns + docker): sudo bash scripts/clean_sim_state.sh" >&2
  exit 1
fi

echo "[clean] stray sim processes"
pkill -9 -f 'arducopter' 2>/dev/null
pkill -9 -f 'mavproxy' 2>/dev/null
pkill -9 -f 'gcs_web_ui_server' 2>/dev/null
pkill -9 -f 'mavproxy_stage_2_4_driver' 2>/dev/null
pkill -9 -f 'sionna_channel_publisher' 2>/dev/null
pkill -9 -f 'two_channel|lora_serial' 2>/dev/null
pkill -9 -f 'airsim_bridge|airsim_stub_server' 2>/dev/null
pkill -9 -f 'run_stage_|run_master_demo' 2>/dev/null

echo "[clean] BAS network namespaces"
for n in $(ip netns list 2>/dev/null | awk '{print $1}' | grep -E '^bas-'); do
  ip netns del "$n" 2>/dev/null && echo "  deleted netns $n"
done

echo "[clean] Docker sim containers (gazebo + sitl)"
if command -v docker >/dev/null; then
  for c in bas-gazebo bas-sitl bas-mavros; do
    docker rm -f "$c" >/dev/null 2>&1 && echo "  removed container $c"
  done
fi

echo "[clean] stale PTYs / sockets"
rm -f /tmp/ptyGCS_lora* /tmp/bas_stage24_rf.json /tmp/bas_*.sock 2>/dev/null

echo "[clean] busy demo ports (8765 GCS / 8770 ISSGR / 8766 FPV)"
for p in 8765 8770 8766 8810 8811; do
  fuser -k "${p}/tcp" 2>/dev/null && echo "  freed :$p"
done

echo "[clean] done — состояние сброшено, можно запускать демо заново."
