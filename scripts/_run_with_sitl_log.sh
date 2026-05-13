#!/usr/bin/env bash
# Запускает mission и параллельно тейлит логи sitl. Цель: увидеть что SITL
# говорит про ARM (rejected/accepted/etc).
set -e
REPO=/home/afetz/bas-prototype

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

# Чистка
sg docker -c "docker rm -f bas-gazebo bas-sitl bas-uav-net bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1
ip link del veth-uav-br 2>/dev/null
rm -f /var/run/netns/bas-uav

bash "${REPO}/scripts/run_stage_1_5_1_mission.sh" degraded_lora &
MAIN_PID=$!

# Wait for sitl to be up
for i in $(seq 1 90); do
    sg docker -c "docker ps --format '{{.Names}}'" 2>/dev/null | grep -q bas-sitl && break
    sleep 1
done
sleep 5

# Tail SITL logs in foreground until mission script exits
sg docker -c "docker logs -f bas-sitl 2>&1" &
TAIL=$!

wait $MAIN_PID
kill $TAIL 2>/dev/null
