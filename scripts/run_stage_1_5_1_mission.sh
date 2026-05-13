#!/usr/bin/env bash
# Этап 1.5.1 + миссия: оркестратор+MissionRunner запускаются ВНУТРИ bas-ctrl-far netns
# и общаются с SITL ТОЛЬКО через ns-3 радио-канал. Команды ARM/TAKEOFF/SET_POSITION_TARGET
# проходят через профиль (задержка, потери, outage).
#
# Топология (как 1.5.1) + mission в bas-ctrl-far netns:
#
#   [bas-ctrl-far netns: bas-orchestrator+MissionRunner]
#         │ tcp:10.10.0.2:5760
#         ▼
#   ns-3 control channel (delay+loss+outage по профилю)
#         │
#         ▼
#   [bas-uav netns: gazebo + sitl на shared loopback, MAVLink на eth0=10.10.0.2:5760]
#
# Использование:
#   sudo bash scripts/run_stage_1_5_1_mission.sh wifi_good       # без деградации
#   sudo bash scripts/run_stage_1_5_1_mission.sh degraded_lora   # с потерями+outage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-wifi_good}"
RUN_ID="stage_1_5_1_mission_${PROFILE}_$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

case "$PROFILE" in
    wifi_good)       SCENARIO=baseline_wifi; CTRL_DELAY_MS=5;   CTRL_LOSS=0.0;  CTRL_OUTAGE="" ;;
    # Outage timing для degraded_lora подобран чтобы пересечься с полётной фазой:
    # orch стартует на ns-3 sim_time ~60-70 (после build + TAP setup), миссия идёт
    # +45..+130 wall от orch start. Окна 120-123 и 160-163 ns-3 → +50..53 и +90..93 wall orch,
    # т.е. сразу после takeoff и в середине waypoint'ов.
    degraded_lora)   SCENARIO=degraded_lora; CTRL_DELAY_MS=250; CTRL_LOSS=0.02; CTRL_OUTAGE="120-123,160-163" ;;
    # Промежуточная конфигурация для отладки.
    moderate)        SCENARIO=baseline_wifi; CTRL_DELAY_MS=50;  CTRL_LOSS=0.0;  CTRL_OUTAGE="" ;;
    *) echo "Неизвестный профиль: $PROFILE" >&2; exit 1 ;;
esac

# Длительность ns-3: с запасом на миссию (mission max_duration_s в YAML + buffer).
# При degraded_lora даём больше времени на IMU settle + повторы команд.
NS3_DURATION="${NS3_DURATION:-300}"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }

cleanup() {
    set +e
    echo "[cleanup]"
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1
    sg docker -c "docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

ensure_root
mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, scenario=${SCENARIO}"
echo "==> логи: $LOG_DIR"

echo "[1/7] подготовка радио-сети"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

echo "[2/7] тушим default compose"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/7] pause + veth + gazebo + sitl"
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

sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo sitl" 2>&1 | tail -3

echo "[4/7] ждём SITL MAVLink на eth0:5760"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" || {
    echo "SITL 5760 не открылся" >&2; sg docker -c "docker logs --tail 20 bas-sitl 2>&1"; exit 2
}
sleep 8

echo "[5/7] запуск ns-3 (delay=${CTRL_DELAY_MS}ms, loss=${CTRL_LOSS}, outage='${CTRL_OUTAGE}')"
NS3_ARGS="--runId=${RUN_ID} --duration=${NS3_DURATION} --ctrlDelayMs=${CTRL_DELAY_MS} --ctrlLoss=${CTRL_LOSS} --ploadDelayMs=200 --ploadLoss=0.0"
[ -n "${CTRL_OUTAGE}" ] && NS3_ARGS="${NS3_ARGS} --ctrlOutage=${CTRL_OUTAGE}"
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -e NS3_ARGS='${NS3_ARGS}' \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src \
        && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} \$NS3_ARGS'" > /dev/null
NS3_LOG="${LOG_DIR}/ns3_events.jsonl"
for i in $(seq 1 60); do
    [ -s "$NS3_LOG" ] && break
    sleep 2
done
[ -s "$NS3_LOG" ] || { echo "ns-3 не стартовал" >&2; sg docker -c "docker logs --tail 30 bas-ns3-stage15"; exit 3; }
echo "  ns-3 готов"

# Дать ns-3 пару секунд на завершение настройки TapBridge внутри обоих TAP'ов.
sleep 5

# Сбросить ARP-кэш в обоих netns: до запуска ns-3 packets могли копиться как
# FAILED entries (60s+ recovery time). После flush kernel сделает fresh ARP.
# Не делаем ping-warmup: failed ping сам создаёт FAILED entry и провоцирует
# именно ту проблему, которую пытаемся обойти. Лучше дать orchestrator сделать
# собственную ARP-discovery естественным путём.
ip netns exec bas-ctrl-far ip neigh flush all 2>/dev/null || true
ip netns exec bas-uav         ip neigh flush all 2>/dev/null || true

# Поднять ARP-параметры в netns'ах: дать больше попыток для high-delay сценариев.
for ns in bas-ctrl-far bas-uav; do
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.mcast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.ucast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.retrans_time_ms=2000 >/dev/null 2>&1 || true
done

echo "[6/7] прогон миссии через ns-3 канал (orchestrator в bas-ctrl-far netns)"
echo "  endpoint=tcp:10.10.0.2:5760 (через ns-3)"

set +e
ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/bas-orchestrator" "${SCENARIO}" \
    --real --external-compose \
    --mavlink-endpoint tcp:10.10.0.2:5760 \
    --run-dir "${LOG_DIR}" \
    --project-root "${REPO_ROOT}" 2>&1 | tee "${LOG_DIR}/orchestrator_stdout.log"
RC=${PIPESTATUS[0]}
set -e

# Save SITL logs for post-mortem (особенно про ARM).
sg docker -c "docker logs bas-sitl 2>&1" > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1" > "${LOG_DIR}/gazebo.log" 2>&1 || true

echo
echo "[7/7] анализ"
"${REPO_ROOT}/.venv/bin/bas-analyzer" "${LOG_DIR}" 2>&1 | tail -30

echo
echo "Прогон завершён (RC=${RC}). Логи: ${LOG_DIR}"
exit ${RC}
