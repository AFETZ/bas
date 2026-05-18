#!/usr/bin/env bash
# Этап 1.8: MAVROS backend — mission AUTO через ROS2 humble + MAVROS 2.14
# вместо pymavlink. Buchstabe ТЗ "MAVROS для работы на основе ROS".
#
# Архитектура:
#
#   bas-orchestrator (host) ──> docker run bas/mavros:dev
#                                     │ (network_mode: container:bas-uav-net)
#                                     │
#   bas-mavros (ros:humble + MAVROS) ─┤ внутри:
#                                     │  • mavros_node (subprocess)
#                                     │       fcu_url=udp://@:14550
#                                     │  • bas_mavros_bridge (rclpy Node)
#                                     │       subscribes /mavros/state,
#                                     │           /mavros/global_position/*,
#                                     │           /mavros/extended_state,
#                                     │           /mavros/mission/reached
#                                     │       services /mavros/cmd/arming,
#                                     │           /set_mode, /mission/push
#                                     │       пишет events.jsonl
#                                     ▼
#               bas-mavbridge (alpine/socat в bas-uav netns)
#                  UDP4-LISTEN:14550 ↔ TCP4:127.0.0.1:5760
#                                     ▼
#                          bas-sitl (ArduCopter SITL)
#                                     ▼
#                          bas-gazebo (Gazebo Harmonic + iris_with_gimbal)
#
# Никакого pymavlink в радио-петле; orchestrator на host лишь запускает
# контейнер bas-mavros и ждёт его exit code. events.jsonl пишется внутри
# контейнера в /work/logs/<run_id>/, bind-mount делает его доступным host'у.
#
# Использование:
#   sudo bash scripts/run_stage_1_8_mavros.sh [scenario_id]   # default baseline_wifi
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="${1:-baseline_wifi}"
RUN_ID="${BAS_RUN_ID:-stage_1_8_mavros_${SCENARIO}_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }

ensure_docker() {
    if docker info >/dev/null 2>&1; then return 0; fi
    echo "[preflight] Docker daemon не отвечает; пробую service docker start"
    service docker start >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
            echo "  Docker daemon готов"
            return 0
        fi
        sleep 1
    done
    echo "Docker daemon не поднялся" >&2
    return 1
}

cleanup() {
    set +e
    echo "[cleanup]"
    timeout 30 sg docker -c "docker rm -f bas-mavros 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

ensure_root
ensure_docker
mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, scenario=${SCENARIO}"
echo "==> логи: ${LOG_DIR}"
echo "==> backend: mavros (ROS2 humble + MAVROS 2.14)"

echo "[1/6] тушим default compose"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[2/6] uav-net (pause-контейнер) + control veth (для health checks)"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav

echo "[3/6] gazebo + sitl (БЕЗ mavbridge — MAVROS подключается к SITL TCP 5760 напрямую)"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -3
echo "  ждём 6с пока Gazebo откроет FDM 9002/9003"
sleep 6
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl" 2>&1 | tail -3

echo "[4/6] ждём SITL MAVLink на :5760"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" || {
    echo "SITL 5760 не открылся" >&2; sg docker -c "docker logs --tail 20 bas-sitl 2>&1"; exit 2
}
# settle EKF/GPS как в 1.5.2.
sleep 10

echo "[5/6] sanity: SITL TCP:5760 готов"
ip netns exec bas-uav ss -tln 2>/dev/null | grep ":5760" | head -1

echo "[6/6] запуск bas-mavros (MAVROS node TCP→SITL + bridge → mission AUTO)"
# Сначала прибиваем любой stale контейнер с тем же именем (если предыдущий
# прогон не завершился чисто).
sg docker -c "docker rm -f bas-mavros 2>/dev/null" >/dev/null 2>&1 || true
# bas-mavros подключается в bas-uav netns (тот же, где mavbridge UDP:14550).
# mission YAML и configs/ читаются через bind-mount; events.jsonl пишется
# в /work/logs/<run_id>/ — bind-mount наружу на host.
sg docker -c "docker run --rm \
    --name bas-mavros \
    --network=container:bas-uav-net \
    -e BAS_RUN_ID=${RUN_ID} \
    -e BAS_RUN_DIR=/work/logs/${RUN_ID} \
    -e BAS_SCENARIO_ID=${SCENARIO} \
    -e BAS_PROJECT_ROOT=/work \
    -e BAS_MAVLINK_FCU_URL=tcp://127.0.0.1:5760 \
    -e BAS_MAX_DURATION_S=600 \
    -v ${REPO_ROOT}/configs:/work/configs:ro \
    -v ${REPO_ROOT}/orchestrator:/work/orchestrator:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    bas/mavros:dev" 2>&1 | tee "${LOG_DIR}/bas_mavros_stdout.log"
RC=${PIPESTATUS[0]}

# Сохранить контейнерные логи остальных компонентов для post-mortem.
sg docker -c "docker logs bas-sitl 2>&1"     > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1"   > "${LOG_DIR}/gazebo.log" 2>&1 || true
sg docker -c "docker logs bas-mavbridge 2>&1" > "${LOG_DIR}/mavbridge.log" 2>&1 || true

echo
echo "[анализ]"
"${REPO_ROOT}/.venv/bin/bas-analyzer" "${LOG_DIR}" 2>&1 | tail -40

# MAVROS sanity: число MAVROS-emitted events.
MAVROS_EVENTS=$(grep -c '"mavlink_backend": "mavros"' "${LOG_DIR}/events.jsonl" 2>/dev/null || echo 0)
FLIGHT_EVENTS=$(grep -c '"event_type": "flight"'   "${LOG_DIR}/events.jsonl" 2>/dev/null || echo 0)
HB_EVENTS=$(grep -c '"message_type": "HEARTBEAT"'  "${LOG_DIR}/events.jsonl" 2>/dev/null || echo 0)
WPR_EVENTS=$(grep -c '"phase": "wp_push_ok"'       "${LOG_DIR}/events.jsonl" 2>/dev/null || echo 0)

echo
echo "Stage 1.8 MAVROS sanity:"
echo "  events.jsonl flight=${FLIGHT_EVENTS} HEARTBEAT=${HB_EVENTS} wp_push_ok=${WPR_EVENTS}"
echo "  MAVROS bridge events tagged: ${MAVROS_EVENTS}"

echo
echo "Прогон завершён (RC=${RC}). Логи: ${LOG_DIR}"
exit ${RC}
