#!/usr/bin/env bash
# Этап 1.7.g: LoRa Serial Bridge — буквальная реализация ТЗ «LoRa через
# Serial Port». Mission AUTO идёт через MAVLink-байтстрим по виртуальной
# PTY-паре, которую разводит ns-3 lorawan PHY+MAC (signetlabdei) с
# реальной LoRa физикой (SF7, BW125 kHz, distance=1000 m).
#
# Архитектура (поверх 1.7.c-fix dual-socat паттерна):
#
#   HOST orchestrator (pymavlink "serial:/tmp/ptyGCS_lora:57600")
#       ↕ host socat: /tmp/ptyGCS_lora ↔ /tmp/bas-bridge/lora-gcs.sock
#       ↕ container-side socat (внутри bas-ns3-stage17): UNIX-CONNECT ↔ PTY
#       ↕ ns-3 GCS PtyApp (читает GCS PTY, шлёт в LoRa)
#       ↕ ns-3 LoRa channel (signetlabdei, ITU-R RP.452, SF7/BW125)
#       ↕ ns-3 UAV PtyApp (получает из LoRa, пишет в UAV PTY)
#       ↕ container-side socat: PTY ↔ UNIX-CONNECT
#       ↕ host UNIX socket /tmp/bas-bridge/lora-uav.sock
#       ↕ bas-lora-uav-bridge (alpine/socat в bas-uav netns):
#         UNIX-CONNECT ↔ TCP:127.0.0.1:5760
#       ↕ SITL primary serial
#       ↕ Gazebo iris_with_gimbal (FDM UDP 9002/9003 в том же netns)
#
# Использование:
#   sudo bash scripts/run_stage_1_7_lora_serial.sh
#
# Цель: mission AUTO с landed=True, где **весь** MAVLink control + telemetry
# идёт через LoRa Serial Port (buchstabe ТЗ). Никаких UDP/TCP fallback.
#
# Дополнительные настройки:
#   BAS_LORA_SF=7|8|9|10|11|12          spreading factor (default 7)
#   BAS_LORA_BW=125000|250000|500000    bandwidth Hz (default 125000)
#   BAS_LORA_DISTANCE_M=1000            UAV-GCS distance (default 1000)
#   NS3_DURATION=600                    длительность ns-3 sim (default 600)
#   NS3_START_TIMEOUT_SECONDS=300       timeout на компиляцию ns-3
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-lora_serial}"
RUN_ID="${BAS_RUN_ID:-stage_1_7_lora_serial_${PROFILE}_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-lora_serial-optimized"

# LoRa параметры (берём дефолты из configs/network_profiles/lora_serial.yaml).
SF="${BAS_LORA_SF:-7}"
BW_HZ="${BAS_LORA_BW:-125000}"
DISTANCE_M="${BAS_LORA_DISTANCE_M:-1000}"
NS3_DURATION="${NS3_DURATION:-600}"
NS3_START_TIMEOUT_SECONDS="${NS3_START_TIMEOUT_SECONDS:-300}"

# Сценарий orchestrator'а. Для LoRa берём baseline_wifi (без artificial
# деградации на control канал) — LoRa сама даёт реалистичную physical
# деградацию. Если нужно сравнение с WiFi: тот же scenario пусть, но
# transport = LoRa serial.
SCENARIO="baseline_wifi"

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
    echo "Docker daemon не поднялся. Запусти: sudo service docker start" >&2
    return 1
}

cleanup() {
    set +e
    echo "[cleanup]"
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage17 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} --profile lora down -v 2>/dev/null" >/dev/null 2>&1
    bash "${REPO_ROOT}/scripts/setup_lora_bridge.sh" down >/dev/null 2>&1 || true
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

ensure_root
ensure_docker
mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, scenario=${SCENARIO}"
echo "==> логи: $LOG_DIR"
echo "==> LoRa params: SF=${SF}, BW=${BW_HZ} Hz, distance=${DISTANCE_M} m, duration=${NS3_DURATION}s"

echo "[1/8] host LoRa PTY bridge (setup_lora_bridge.sh up)"
bash "${REPO_ROOT}/scripts/setup_lora_bridge.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_lora_bridge.sh" up | tail -8

# Sanity: только GCS host PTY (для orchestrator pyserial).
# UAV host PTY больше не создаётся — UAV-side UNIX socket поднимает ns-3
# контейнер сам (UNIX-LISTEN), к нему подключается lora-uav-bridge.
if [ ! -e /tmp/ptyGCS_lora ]; then
    echo "host PTY /tmp/ptyGCS_lora не создан после setup_lora_bridge.sh" >&2
    exit 2
fi

echo "[2/8] тушим default compose"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/8] pause-контейнер uav-net"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3

# bas-uav netns symlink (нужен для ss проверок ниже).
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav

echo "[4/8] gazebo + sitl (БЕЗ mavbridge — его место занимает lora-uav-bridge для LoRa-режима)"
# Gazebo стартуем первым и даём 6с на инициализацию ArduPilotPlugin (FDM 9002/9003).
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -3
echo "  ждём 6с пока Gazebo откроет FDM"
sleep 6
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl" 2>&1 | tail -3

echo "[5/8] ждём SITL MAVLink на :5760"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" || {
    echo "SITL 5760 не открылся" >&2
    sg docker -c "docker logs --tail 20 bas-sitl 2>&1"
    exit 2
}
# SITL settles (EKF, GPS, sensors) ~10с — иначе ранний MAVLink connect ловит "Closed connection".
sleep 8

echo "[6/8] поднимаем lora-uav-bridge (UNIX socket ↔ SITL TCP 5760)"
sg docker -c "docker compose -f ${COMPOSE_FILE} --profile lora up -d lora-uav-bridge" 2>&1 | tail -3
sleep 2

echo "[7/8] запуск ns-3 lora_serial контейнер (compile + RealtimeSimulator)"
sg docker -c "docker rm -f bas-ns3-stage17 2>/dev/null" >/dev/null 2>&1 || true

# Контейнер делает (внутри):
#   1. container-side socat:
#        - GCS PTY: UNIX-CONNECT к host UNIX socket (host socat — listen'ит)
#        - UAV PTY: UNIX-LISTEN на UNIX socket (lora-uav-bridge — connect'ится)
#      Асимметрия из-за того что host orchestrator открывает host PTY GCS
#      (pyserial требует tty-device), а на UAV-стороне host PTY не нужен —
#      lora-uav-bridge сразу проксирует UNIX socket → SITL TCP 5760.
#   2. cp lora_serial.cc → scratch/ + ./ns3 build
#   3. ns3.40-lora_serial-optimized --runId --duration --sf --bandwidth
#      --distance --ptyUavPath --ptyGcsPath
# /tmp/bas-bridge → /bridge: shared volume для UNIX sockets.
# REPO_ROOT/logs → /work/logs: для ns3_events.jsonl в правильную папку.
sg docker -c "docker run -d --name bas-ns3-stage17 --network host --cap-add NET_ADMIN --privileged \
    -v /tmp/bas-bridge:/bridge \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
        echo \"[ns3-container] container-side socat: GCS UNIX-CONNECT, UAV UNIX-LISTEN\"; \
        socat -d -d PTY,link=/tmp/ptyGCS_lora,raw,echo=0,b57600 \
                    UNIX-CONNECT:/bridge/lora-gcs.sock > /tmp/socat-gcs.log 2>&1 & \
        socat -d -d PTY,link=/tmp/ptyUAV_lora,raw,echo=0,b57600 \
                    UNIX-LISTEN:/bridge/lora-uav.sock,fork,mode=666 > /tmp/socat-uav.log 2>&1 & \
        sleep 2; \
        ls -la /tmp/ptyGCS_lora /tmp/ptyUAV_lora /bridge/lora-uav.sock 2>&1; \
        echo \"[ns3-container] compiling lora_serial.cc\"; \
        cp /work/ns3/scenarios/lora_serial.cc /work/ns3-src/scratch/; \
        cd /work/ns3-src && ./ns3 build > /tmp/build.log 2>&1; \
        echo \"[ns3-container] launching lora_serial (SF=${SF}, BW=${BW_HZ}, dist=${DISTANCE_M}m, dur=${NS3_DURATION}s)\"; \
        ${NS3_BIN} \
            --runId=${RUN_ID} --duration=${NS3_DURATION} \
            --sf=${SF} --bandwidth=${BW_HZ} --distance=${DISTANCE_M} \
            --ptyUavPath=/tmp/ptyUAV_lora --ptyGcsPath=/tmp/ptyGCS_lora'" > /dev/null

NS3_LOG="${LOG_DIR}/ns3_events.jsonl"
for i in $(seq 1 $((NS3_START_TIMEOUT_SECONDS / 2))); do
    [ -s "$NS3_LOG" ] && break
    if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-ns3-stage17 2>/dev/null" | grep -q true; then
        echo "ns-3 контейнер завершился до старта" >&2
        sg docker -c "docker exec bas-ns3-stage17 cat /tmp/build.log 2>&1" >&2 || true
        sg docker -c "docker logs --tail 80 bas-ns3-stage17 2>&1" >&2 || true
        exit 3
    fi
    sleep 2
done
[ -s "$NS3_LOG" ] || {
    echo "ns-3 не стартовал за ${NS3_START_TIMEOUT_SECONDS}s" >&2
    sg docker -c "docker exec bas-ns3-stage17 tail -80 /tmp/build.log 2>&1" >&2 || true
    sg docker -c "docker logs --tail 80 bas-ns3-stage17 2>&1" >&2 || true
    exit 3
}
echo "  ns-3 lora_serial готов"
# Ещё пара секунд чтобы PtyApp запустилcя (sim_time >= 1.5s) и host socat
# увидел client connect для UAV bridge.
sleep 3

# Sanity-check: lora-uav-bridge container всё ещё работает (не упал из-за
# отсутствия client'а на UNIX-CONNECT'е).
if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-lora-uav-bridge 2>/dev/null" | grep -q true; then
    echo "[warn] lora-uav-bridge не работает — перезапускаю"
    sg docker -c "docker compose -f ${COMPOSE_FILE} --profile lora up -d lora-uav-bridge" 2>&1 | tail -3
    sleep 2
fi

echo "[8/8] Mission AUTO через LoRa Serial (orchestrator на host, serial:/tmp/ptyGCS_lora)"
echo "  endpoint=serial:/tmp/ptyGCS_lora:57600 — байты идут через ns-3 LoRa channel"
echo "  (PHY калиброван под SX1276 SF=${SF}, BW=${BW_HZ}, distance=${DISTANCE_M}m)"

# Orchestrator на host (не в bas-ctrl-far netns) — открывает host PTY через
# pymavlink/pyserial и шлёт MISSION_COUNT/MISSION_ITEM/ARM/MISSION_START через
# LoRa. Поскольку 1.7.h канал full-duplex (PointToPoint калиброванный под
# SX1276), mission AUTO upload работает: orchestrator → SITL для команд,
# SITL → orchestrator для telemetry.
set +e
"${REPO_ROOT}/.venv/bin/bas-orchestrator" "${SCENARIO}" \
    --real --external-compose \
    --mavlink-endpoint "serial:/tmp/ptyGCS_lora:57600" \
    --run-dir "${LOG_DIR}" \
    --project-root "${REPO_ROOT}" 2>&1 | tee "${LOG_DIR}/orchestrator_stdout.log"
RC=${PIPESTATUS[0]}
set -e

# Дать ns-3 пару секунд догрести events.jsonl до закрытия.
sleep 3

# Сохранить логи всех контейнеров для post-mortem.
sg docker -c "docker logs bas-sitl 2>&1"             > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1"           > "${LOG_DIR}/gazebo.log" 2>&1 || true
sg docker -c "docker logs bas-lora-uav-bridge 2>&1"  > "${LOG_DIR}/lora_uav_bridge.log" 2>&1 || true
sg docker -c "docker logs bas-ns3-stage17 2>&1"      > "${LOG_DIR}/ns3_stdout.log" 2>&1 || true

# Дополнительно: дамп host socat логов — диагностика дедлоков.
cp /tmp/bas-bridge/socat-gcs.log "${LOG_DIR}/socat_host_gcs.log" 2>/dev/null || true

echo
echo "[анализ]"
"${REPO_ROOT}/.venv/bin/bas-analyzer" "${LOG_DIR}" 2>&1 | tail -40

# LoRa sanity: PTY reads / writes + LoRa frames.
PTY_READ_UAV=$(grep -c '"phase":"pty_read","side":"uav"'   "${NS3_LOG}" 2>/dev/null || echo 0)
PTY_READ_GCS=$(grep -c '"phase":"pty_read","side":"gcs"'   "${NS3_LOG}" 2>/dev/null || echo 0)
PTY_WRITE_UAV=$(grep -c '"phase":"pty_write","side":"uav"' "${NS3_LOG}" 2>/dev/null || echo 0)
PTY_WRITE_GCS=$(grep -c '"phase":"pty_write","side":"gcs"' "${NS3_LOG}" 2>/dev/null || echo 0)

echo
echo "Stage 1.7.h LoRa Serial sanity (full-duplex):"
echo "  PTY reads: UAV=${PTY_READ_UAV} (SITL→LoRa→orchestrator)"
echo "             GCS=${PTY_READ_GCS} (orchestrator→LoRa→SITL)"
echo "  PTY writes: UAV=${PTY_WRITE_UAV} (orchestrator commands → SITL)"
echo "              GCS=${PTY_WRITE_GCS} (SITL telemetry → orchestrator)"

echo
echo "Прогон завершён (RC=${RC}). Логи: ${LOG_DIR}"
exit ${RC}
