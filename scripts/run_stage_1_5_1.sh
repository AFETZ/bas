#!/usr/bin/env bash
# Этап 1.5.1: SITL+Gazebo в shared network namespace, MAVLink ходит ЧЕРЕЗ ns-3.
#
# Топология:
#
#   ┌─────────────────────────────────────────┐
#   │ netns bas-uav (pause-контейнер)         │
#   │   ├── lo (FDM 9002/9003)                │  ← gazebo + sitl делят loopback
#   │   ├── gazebo (ardupilot_gazebo plugin)  │
#   │   ├── sitl   (MAVLink на 0.0.0.0:5760)  │
#   │   └── eth0 (10.10.0.2/24, injected veth)│
#   └────────────────┬────────────────────────┘
#                    │ veth-pair (veth-uav ↔ veth-uav-br)
#                    │
#               br-ctrl-near ←→ tap-ctrl-near
#                    │              │
#                    │       ns-3 (delay+loss+outage)
#                    │              │
#               br-ctrl-far  ←→ tap-ctrl-far
#                    │
#                    │ veth-pair
#                    │
#   ┌────────────────▼────────────────────────┐
#   │ netns bas-ctrl-far                      │
#   │   eth0: 10.10.0.1/24                    │
#   │   shadow_gcs.py → tcp:10.10.0.2:5760    │
#   └─────────────────────────────────────────┘
#
# Никакого socat-proxy. MAVLink-трафик целиком в радио-петле.
#
# Использование:
#   sudo bash scripts/run_stage_1_5_1.sh                    # wifi_good
#   sudo bash scripts/run_stage_1_5_1.sh degraded_lora      # с потерями + outage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-wifi_good}"
DURATION="${DURATION:-180}"
RUN_ID="stage_1_5_1_$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

case "$PROFILE" in
    wifi_good)
        CTRL_DELAY_MS=5; CTRL_LOSS=0.0; CTRL_OUTAGE=""
        ;;
    degraded_lora)
        CTRL_DELAY_MS=250; CTRL_LOSS=0.02; CTRL_OUTAGE="30-33,60-62"
        ;;
    *)
        echo "Неизвестный профиль: $PROFILE" >&2; exit 1
        ;;
esac

ensure_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "Запускайте с sudo." >&2; exit 1
    fi
}

cleanup() {
    set +e
    echo "[cleanup]"
    [ -n "${SHADOW_PID:-}" ] && kill "$SHADOW_PID" 2>/dev/null
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1
    sg docker -c "docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    # Удаляем веточный мост на хосте; uav-side veth уйдёт с netns'ом контейнера.
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

ensure_root
mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, duration=${DURATION}s"
echo "==> логи: $LOG_DIR"

echo "[1/8] подготовка радио-сети"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

echo "[2/8] тушим default compose (host-network вариант) если жив"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/8] поднимаем pause-контейнер uav-net"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
echo "  bas-uav-net PID=${UAV_PID}"
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav

echo "[4/8] инжектируем veth → eth0=10.10.0.2 в netns bas-uav"
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns bas-uav
ip -n bas-uav link set veth-uav name eth0
ip -n bas-uav addr add 10.10.0.2/24 dev eth0
ip -n bas-uav link set eth0 up
ip -n bas-uav link set lo up

echo "[5/8] поднимаем gazebo + sitl в shared netns"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo sitl" 2>&1 | tail -5

echo "[6/8] ждём SITL MAVLink на 10.10.0.2:5760 (через uav netns)"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
if ! ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then
    echo "SITL 5760 не открылся за 60с" >&2
    sg docker -c "docker logs --tail 20 bas-sitl 2>&1"
    exit 2
fi
echo "  SITL слушает 5760"
sleep 10  # SITL init settle

echo "[7/8] запуск ns-3 (TapBridge UseLocal на оба канала)"
NS3_ARGS="--runId=${RUN_ID} --duration=$((DURATION + 30)) --ctrlDelayMs=${CTRL_DELAY_MS} --ctrlLoss=${CTRL_LOSS} --ploadDelayMs=200 --ploadLoss=0.0"
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
        && ${NS3_BIN} \$NS3_ARGS'" > /dev/null

# Ждём что ns-3 поднял TAP'ы и начал писать события.
NS3_LOG="${LOG_DIR}/ns3_events.jsonl"
for i in $(seq 1 60); do
    [ -s "$NS3_LOG" ] && break
    sleep 2
done
if ! [ -s "$NS3_LOG" ]; then
    echo "ns-3 не стартовал" >&2
    sg docker -c "docker logs --tail 30 bas-ns3-stage15 2>&1" >&2
    exit 3
fi
echo "  ns-3 готов"

echo "[8/8] shadow_gcs.py в bas-ctrl-far → tcp:10.10.0.2:5760 (через ns-3)"
ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/python3" \
    "${REPO_ROOT}/scripts/shadow_gcs.py" \
    --endpoint tcp:10.10.0.2:5760 \
    --out "${LOG_DIR}/shadow_gcs.jsonl" \
    --duration "${DURATION}" &
SHADOW_PID=$!

echo
echo "Радио-петля активна. Ждём ${DURATION}с (вся MAVLink-телеметрия идёт через ns-3)."
echo
wait "$SHADOW_PID" || true

echo
echo "=== Итог ==="
ls -la "${LOG_DIR}/"
