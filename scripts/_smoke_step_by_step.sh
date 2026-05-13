#!/usr/bin/env bash
# Пошаговая smoke: проверяет состояние netns/ARP/ping после КАЖДОГО шага
# mission setup. Цель: найти момент, когда ARP/ping ломается.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
RUN_ID="smoke_step_$(date -u +%H%M%S)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

cleanup() {
    set +e
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null; docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

mkdir -p "$LOG_DIR"
echo "==> $RUN_ID"

probe() {
    local label="$1"
    echo "==========${label}=========="
    echo "--- bas-uav interfaces ---"
    ip -n bas-uav -br addr 2>&1
    echo "--- bridge fdb br-ctrl-near (uav-related) ---"
    bridge fdb show br br-ctrl-near 2>&1 | grep -E "veth-uav-br|tap-ctrl-near" | head -10
    echo "--- ping from bas-ctrl-far ---"
    ip netns exec bas-ctrl-far ip neigh flush all
    ip netns exec bas-ctrl-far ping -c 2 -W 5 10.10.0.2 2>&1 | tail -3
    echo "--- arp after ping ---"
    ip netns exec bas-ctrl-far ip neigh
    echo
}

echo "[A] setup_radio_net"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up >/dev/null
sg docker -c "docker compose -f ${REPO_ROOT}/docker-compose.yml down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[B] pause-only via compose"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" >/dev/null
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns bas-uav
ip -n bas-uav link set veth-uav name eth0
ip -n bas-uav addr add 10.10.0.2/24 dev eth0
ip -n bas-uav link set eth0 up
ip -n bas-uav link set lo up

echo "[C] ns-3 launch (нужен для маршрутизации ICMP)"
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} --runId=${RUN_ID} --duration=120 --ctrlDelayMs=250 --ctrlLoss=0.02 --ploadDelayMs=200 --ploadLoss=0.0'" >/dev/null
for i in $(seq 1 60); do
    [ -s "${LOG_DIR}/ns3_events.jsonl" ] && break
    sleep 2
done
sleep 5

probe "step1: после pause+veth+ns-3 (gazebo/sitl ЕЩЁ НЕ запущены)"

echo "[D] start gazebo (только gazebo)"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -2
sleep 5

probe "step2: после старта gazebo"

echo "[E] start sitl"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl" 2>&1 | tail -2
sleep 8

probe "step3: после старта sitl"

echo "logs: ${LOG_DIR}"
