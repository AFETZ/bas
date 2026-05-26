#!/usr/bin/env bash
# Smoke: воспроизводит mission setup (pause+gazebo+sitl+ns3) и делает probes
# из bas-ctrl-far без запуска orchestrator. Сравнить с busybox smoke (_smoke_radio).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-degraded_lora}"
RUN_ID="smoke_mission_${PROFILE}_$(date -u +%H%M%S)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"

case "$PROFILE" in
    wifi_good)     CTRL_DELAY_MS=5;   CTRL_LOSS=0.0;  CTRL_OUTAGE="" ;;
    degraded_lora) CTRL_DELAY_MS=250; CTRL_LOSS=0.02; CTRL_OUTAGE="" ;;
    *) echo bad profile; exit 1 ;;
esac

[ "$EUID" -eq 0 ] || { echo sudo only; exit 1; }

cleanup() {
    set +e
    [ -n "${TCPDUMP_PID:-}" ] && kill "$TCPDUMP_PID" 2>/dev/null
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null; docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}"

echo "[1] setup_radio_net"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -5
sg docker -c "docker compose -f ${REPO_ROOT}/docker-compose.yml down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[2] pause + inject veth"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3
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

echo "  bas-uav addr: $(ip -n bas-uav -br addr show eth0)"

echo "[3] start gazebo+sitl"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo sitl" 2>&1 | tail -3

echo "[4] wait SITL on :5760"
for i in $(seq 1 60); do
    ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" && break
    sleep 1
done
sleep 5

echo "[5] ns-3 launch"
NS3_ARGS="--runId=${RUN_ID} --duration=60 --ctrlDelayMs=${CTRL_DELAY_MS} --ctrlLoss=${CTRL_LOSS} --ploadDelayMs=200 --ploadLoss=0.0"
[ -n "${CTRL_OUTAGE}" ] && NS3_ARGS="${NS3_ARGS} --ctrlOutage=${CTRL_OUTAGE}"
sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -e NS3_ARGS='${NS3_ARGS}' \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src \
        && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} \$NS3_ARGS'" >/dev/null
for i in $(seq 1 60); do
    [ -s "${LOG_DIR}/ns3_events.jsonl" ] && break
    sleep 2
done
sleep 5

echo "[6] tcpdump on tap-ctrl-far"
tcpdump -i tap-ctrl-far -nn -w "${LOG_DIR}/tap-ctrl-far.pcap" 2>/dev/null &
TCPDUMP_PID=$!
sleep 1

echo "[7] probes из bas-ctrl-far"
ip netns exec bas-ctrl-far ip neigh flush all
echo "--- ping 10.10.0.2 ---"
ip netns exec bas-ctrl-far ping -c 5 -W 5 10.10.0.2 2>&1 | tail -7
echo "--- arp ---"
ip netns exec bas-ctrl-far ip neigh
echo "--- TCP connect 5760 (timeout 8s) ---"
timeout 8 ip netns exec bas-ctrl-far nc -zvw5 10.10.0.2 5760 2>&1 || echo "(timed out or failed)"

echo "--- bas-uav netns interfaces ---"
ip -n bas-uav -br addr
echo "--- bas-uav arp ---"
ip -n bas-uav neigh

sleep 2
kill $TCPDUMP_PID 2>/dev/null
wait $TCPDUMP_PID 2>/dev/null
echo "--- pcap first 25 ---"
tcpdump -r "${LOG_DIR}/tap-ctrl-far.pcap" -nn 2>&1 | head -25
echo "logs: ${LOG_DIR}"
