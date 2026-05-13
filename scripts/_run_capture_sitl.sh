#!/usr/bin/env bash
# Run degraded_lora mission AND capture sitl container logs to file.
set -e
REPO=/home/afetz/bas-prototype

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

sg docker -c "docker rm -f bas-gazebo bas-sitl bas-uav-net bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1
ip link del veth-uav-br 2>/dev/null
rm -f /var/run/netns/bas-uav

SITL_LOG=/tmp/sitl_log_$(date +%s).log
echo "sitl_log=$SITL_LOG"

bash "${REPO}/scripts/run_stage_1_5_1_mission.sh" degraded_lora &
MAIN=$!

for i in $(seq 1 90); do
    sg docker -c "docker ps --format '{{.Names}}'" 2>/dev/null | grep -q bas-sitl && break
    sleep 1
done
sleep 5

sg docker -c "docker logs -f bas-sitl 2>&1" > "$SITL_LOG" 2>&1 &
TAIL=$!

wait $MAIN || true
kill $TAIL 2>/dev/null || true
sleep 1

echo === SITL log last 80 lines ===
tail -80 "$SITL_LOG"
