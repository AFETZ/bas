#!/usr/bin/env bash
# Минимальный smoke-test радио-петли БЕЗ orchestrator/SITL.
#
# 1. setup_radio_net.sh down+up (свежее состояние)
# 2. Поднимает busybox-pause-контейнер bas-uav-net, инжектирует veth с заданным IP
# 3. Поднимает busybox-listener на TCP 5760 внутри bas-uav (имитирует SITL)
# 4. Запускает ns-3 контейнер с заданным профилем
# 5. Из bas-ctrl-far делает: ip addr, ip route, ip neigh, ip route get, ping, TCP-connect
# 6. Параллельно tcpdump на tap-ctrl-far пишет ARP/SYN активность
#
# Использование:
#   sudo bash scripts/debug/_smoke_radio.sh wifi_good [UAV_IP]
#   sudo bash scripts/debug/_smoke_radio.sh degraded_lora [UAV_IP]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-wifi_good}"
UAV_IP="${2:-10.10.0.2}"
RUN_ID="smoke_${PROFILE}_${UAV_IP//./_}_$(date -u +%H%M%S)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"

case "$PROFILE" in
    wifi_good)     CTRL_DELAY_MS=5;   CTRL_LOSS=0.0;  CTRL_OUTAGE="" ;;
    degraded_lora) CTRL_DELAY_MS=250; CTRL_LOSS=0.02; CTRL_OUTAGE="" ;;
    *) echo "bad profile" >&2; exit 1 ;;
esac

[ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }

cleanup() {
    set +e
    [ -n "${TCPDUMP_PID:-}" ] && kill "$TCPDUMP_PID" 2>/dev/null
    sg docker -c "docker rm -f bas-ns3-stage15 bas-uav-net 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, UAV_IP=${UAV_IP}"

echo "[1] setup_radio_net.sh down+up"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -8

echo "[2] pause + инжект veth + busybox listener"
sg docker -c "docker run -d --name bas-uav-net --network none busybox sh -c 'trap : TERM INT; sleep infinity & wait'" >/dev/null
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns bas-uav
ip -n bas-uav link set veth-uav name eth0
ip -n bas-uav addr add "${UAV_IP}/24" dev eth0
ip -n bas-uav link set eth0 up
ip -n bas-uav link set lo up

echo "  bas-uav eth0: $(ip -n bas-uav -br addr show eth0)"
echo "  bas-ctrl-near veth: $(ip netns exec bas-ctrl-near ip -br addr show | grep veth || echo none)"

echo "[3] busybox listener в bas-uav (на 5760)"
sg docker -c "docker exec -d bas-uav-net sh -c 'while true; do echo SMOKE-OK | nc -lp 5760 -s 0.0.0.0; done'" 2>/dev/null
sleep 1

echo "[4] ns-3 docker run"
NS3_ARGS="--runId=${RUN_ID} --duration=60 --ctrlDelayMs=${CTRL_DELAY_MS} --ctrlLoss=${CTRL_LOSS} --ploadDelayMs=200 --ploadLoss=0.0"
[ -n "${CTRL_OUTAGE}" ] && NS3_ARGS="${NS3_ARGS} --ctrlOutage=${CTRL_OUTAGE}"
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
[ -s "${LOG_DIR}/ns3_events.jsonl" ] || { echo "ns-3 не стартовал"; sg docker -c "docker logs bas-ns3-stage15 2>&1 | tail -10"; exit 3; }
sleep 5

echo "[5] tcpdump на tap-ctrl-far"
tcpdump -i tap-ctrl-far -nn -w "${LOG_DIR}/tap-ctrl-far.pcap" 2>/dev/null &
TCPDUMP_PID=$!
sleep 1

echo "[6] probes из bas-ctrl-far к ${UAV_IP}"
echo "--- ip addr ---"
ip netns exec bas-ctrl-far ip -br addr | head -5
echo "--- ip route ---"
ip netns exec bas-ctrl-far ip route
echo "--- ip neigh flush + initial ---"
ip netns exec bas-ctrl-far ip neigh flush all
ip netns exec bas-ctrl-far ip neigh
echo "--- ip route get ${UAV_IP} ---"
ip netns exec bas-ctrl-far ip route get "${UAV_IP}" 2>&1
echo "--- ping -c 5 -W 2 ${UAV_IP} ---"
ip netns exec bas-ctrl-far ping -c 5 -W 2 "${UAV_IP}" 2>&1 || true
echo "--- ip neigh после ping ---"
ip netns exec bas-ctrl-far ip neigh
echo "--- TCP connect (nc -zvw5 ${UAV_IP} 5760) ---"
timeout 10 ip netns exec bas-ctrl-far nc -zvw5 "${UAV_IP}" 5760 2>&1 || true

sleep 2
kill $TCPDUMP_PID 2>/dev/null
wait $TCPDUMP_PID 2>/dev/null

echo "--- pcap on tap-ctrl-far (first 30 entries) ---"
tcpdump -r "${LOG_DIR}/tap-ctrl-far.pcap" -nn 2>&1 | head -30 || true

echo
echo "Логи: ${LOG_DIR}"
