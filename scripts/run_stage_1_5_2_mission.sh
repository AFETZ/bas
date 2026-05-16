#!/usr/bin/env bash
# Этап 1.5.2: mission + RTP-видео через ns-3 payload-канал.
#
# Базируется на run_stage_1_5_1_mission.sh, добавляет видео-pipeline:
#   * второй veth в bas-uav netns (eth1=10.20.0.2/24 → br-pload-near)
#   * pause-контейнер bas-pload-far-net, в его netns eth=10.20.0.3/24 → br-pload-far
#   * video-receiver запускается ПЕРЕД sender'ом, чтобы успеть открыть UDP сокет
#   * video-sender → видео уходит на 10.20.0.3:5000 через ns-3 pload канал
#
# Топология (как 1.5.1) + payload:
#
#   [bas-ctrl-far netns: bas-orchestrator+MissionRunner]
#         │ udpout:10.10.0.2:14550
#         ▼
#   ns-3 control channel
#         │
#         ▼
#   [bas-uav netns: gazebo + sitl + mavbridge]                     [bas-pload-far-net]
#   eth0=10.10.0.2/24, eth1=10.20.0.2/24                            eth=10.20.0.3/24
#         │                                                              ▲
#         │ RTP H.264 UDP:5000 → 10.20.0.3                               │
#         ▼                                                              │
#   tap-pload-near ──► ns-3 payload channel ──► tap-pload-far ────────────┘
#
# Использование:
#   sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
#   sudo bash scripts/run_stage_1_5_2_mission.sh degraded_lora
#   sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
#   sudo env BAS_VIDEO_SOURCE=camera BAS_GAZEBO_GUI=1 bash scripts/run_stage_1_5_2_mission.sh wifi_good
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-wifi_good}"
RUN_ID="stage_1_5_2_mission_${PROFILE}_$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

# Профили: control (как в 1.5.1) + payload (для 1.5.2 — обычно одинаковый профиль,
# но degraded_lora-payload в реальных дронах часто на отдельном радиоканале без outage,
# поэтому payload outage отдельный).
case "$PROFILE" in
    wifi_good)
        SCENARIO=baseline_wifi
        CTRL_DELAY_MS=5;   CTRL_LOSS=0.0;  CTRL_OUTAGE=""
        PLOAD_DELAY_MS=10; PLOAD_LOSS=0.0; PLOAD_OUTAGE=""
        ;;
    degraded_lora)
        SCENARIO=degraded_lora
        CTRL_DELAY_MS=250; CTRL_LOSS=0.02; CTRL_OUTAGE="120-123,160-163"
        # Payload deg-профиль: меньше delay но c loss и кратким outage —
        # чтобы видеть frame_loss spike'и без полного blackout'а.
        PLOAD_DELAY_MS=80; PLOAD_LOSS=0.01; PLOAD_OUTAGE="140-141"
        # Под degraded_lora bitrate понижен с 2000 до 500 kbps:
        # 1) LoRa-подобный канал реалистично не тянет HD-видео,
        # 2) меньше нагрузка на x264 encoder → orchestrator listener
        #    не вытесняется (см. docs/stage_1_5_2_plan.md, INVALID_SEQUENCE
        #    regression).
        DEFAULT_VIDEO_BITRATE_KBPS=500
        ;;
    moderate)
        SCENARIO=baseline_wifi
        CTRL_DELAY_MS=50;  CTRL_LOSS=0.0;  CTRL_OUTAGE=""
        PLOAD_DELAY_MS=50; PLOAD_LOSS=0.0; PLOAD_OUTAGE=""
        ;;
    *) echo "Неизвестный профиль: $PROFILE" >&2; exit 1 ;;
esac

# Длительность ns-3.
if [ -z "${NS3_DURATION:-}" ]; then
    if [ "$PROFILE" = "degraded_lora" ]; then NS3_DURATION=600; else NS3_DURATION=300; fi
fi
NS3_START_TIMEOUT_SECONDS="${NS3_START_TIMEOUT_SECONDS:-300}"

# Video-параметры (передаются как env в docker-compose).
# BAS_VIDEO_SOURCE:
#   videotestsrc     — synthetic ball pattern (smoke, 1.5.2.a default)
#   camera           — real Gazebo iris_with_gimbal camera через GstCameraPlugin
#                      (этап 1.5.2.b). Маппится в sender.py на 'udpsrc:5600'.
#   udpsrc:<port>    — прямое указание UDP loopback source (для отладки)
export BAS_CAMERA_UDP_PORT="${BAS_CAMERA_UDP_PORT:-5600}"
export BAS_CAMERA_ENABLE_TOPIC="${BAS_CAMERA_ENABLE_TOPIC:-/world/iris_runway/model/iris_with_gimbal/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming}"
export BAS_VIDEO_CAMERA_WARMUP_SECONDS="${BAS_VIDEO_CAMERA_WARMUP_SECONDS:-45}"
export BAS_VIDEO_CAMERA_STRICT="${BAS_VIDEO_CAMERA_STRICT:-1}"
export BAS_VIDEO_SOURCE_RAW="${BAS_VIDEO_SOURCE:-videotestsrc}"
case "$BAS_VIDEO_SOURCE_RAW" in
    camera) export BAS_VIDEO_SOURCE="udpsrc:${BAS_CAMERA_UDP_PORT}" ;;
    *)      export BAS_VIDEO_SOURCE="$BAS_VIDEO_SOURCE_RAW" ;;
esac
export BAS_VIDEO_DEST_HOST="${BAS_VIDEO_DEST_HOST:-10.20.0.3}"
export BAS_VIDEO_DEST_PORT="${BAS_VIDEO_DEST_PORT:-5000}"
export BAS_VIDEO_BITRATE_KBPS="${BAS_VIDEO_BITRATE_KBPS:-${DEFAULT_VIDEO_BITRATE_KBPS:-2000}}"
export BAS_VIDEO_FPS="${BAS_VIDEO_FPS:-30}"
export BAS_VIDEO_WIDTH="${BAS_VIDEO_WIDTH:-640}"
export BAS_VIDEO_HEIGHT="${BAS_VIDEO_HEIGHT:-480}"
export BAS_VIDEO_TX_LOG="/work/logs/${RUN_ID}/video_tx.jsonl"
export BAS_VIDEO_RX_LOG="/work/logs/${RUN_ID}/video_rx.jsonl"
export BAS_VIDEO_RECORD_MP4="${BAS_VIDEO_RECORD_MP4:-/work/logs/${RUN_ID}/video_rx.mp4}"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }

ensure_docker() {
    if docker info >/dev/null 2>&1; then
        return 0
    fi

    echo "[preflight] Docker daemon не отвечает; пробую запустить service docker"
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
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    ip link del veth-uav-br 2>/dev/null
    ip link del veth-upl-br 2>/dev/null
    ip link del veth-pfar-br 2>/dev/null
    rm -f /var/run/netns/bas-uav
    rm -f /var/run/netns/bas-pload-far-pod
    set -e
}
trap cleanup EXIT INT TERM

discover_camera_enable_topic() {
    local discovered
    discovered="$(
        sg docker -c "docker exec bas-gazebo gz topic -l 2>/dev/null" \
            | grep '/enable_streaming$' \
            | head -1 || true
    )"
    if [ -n "$discovered" ]; then
        BAS_CAMERA_ENABLE_TOPIC="$discovered"
        return 0
    fi
    return 1
}

enable_gazebo_camera_stream() {
    [ "$BAS_VIDEO_SOURCE_RAW" = "camera" ] || return 0

    echo "[camera] включаем GstCameraPlugin stream"
    local discovered=0
    for _ in $(seq 1 20); do
        if discover_camera_enable_topic; then
            discovered=1
            break
        fi
        sleep 1
    done
    if [ "$discovered" -ne 1 ]; then
        echo "  camera enable topic не появился в gz topic -l; пробую default"
    fi
    echo "  camera enable topic: ${BAS_CAMERA_ENABLE_TOPIC}"

    local ok=0
    for _ in $(seq 1 3); do
        if sg docker -c "docker exec bas-gazebo gz topic -t '${BAS_CAMERA_ENABLE_TOPIC}' -m gz.msgs.Boolean -p 'data: true' >/tmp/bas_camera_enable.log 2>&1"; then
            ok=1
        fi
        sleep 1
    done
    if [ "$ok" -ne 1 ]; then
        echo "Не удалось отправить enable_streaming в Gazebo camera plugin" >&2
        sg docker -c "docker exec bas-gazebo cat /tmp/bas_camera_enable.log 2>/dev/null" >&2 || true
        return 1
    fi
    sg docker -c "docker logs --tail 80 bas-gazebo 2>&1" \
        | grep -E 'GstCameraPlugin|CameraZoomPlugin|Ogre2|Unable to open display' \
        | tail -20 \
        | sed 's/^/  gz: /' || true
}

check_camera_video_flow() {
    [ "$BAS_VIDEO_SOURCE_RAW" = "camera" ] || return 0

    local tx_log="${LOG_DIR}/video_tx.jsonl"
    local tx_lines
    local waited=0
    while [ "$waited" -le "$BAS_VIDEO_CAMERA_WARMUP_SECONDS" ]; do
        if [ -f "$tx_log" ]; then
            tx_lines=$(wc -l < "$tx_log" 2>/dev/null || echo 0)
        else
            tx_lines=0
        fi
        [ "${tx_lines:-0}" -gt 1 ] && break
        if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-video-sender 2>/dev/null" | grep -q true; then
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done
    echo "  camera tx sanity: video_tx.jsonl=${tx_lines:-0} записей (${waited}s)"

    if [ "${tx_lines:-0}" -le 1 ] && [ "$BAS_VIDEO_CAMERA_STRICT" = "1" ]; then
        echo "Gazebo camera не дала RTP на 127.0.0.1:${BAS_CAMERA_UDP_PORT}; останавливаю camera smoke." >&2
        sg docker -c "docker logs bas-gazebo 2>&1"         > "${LOG_DIR}/gazebo.log" 2>&1 || true
        sg docker -c "docker logs bas-video-sender 2>&1"   > "${LOG_DIR}/video_sender.log" 2>&1 || true
        sg docker -c "docker logs bas-video-receiver 2>&1" > "${LOG_DIR}/video_receiver.log" 2>&1 || true
        echo "Подсказка: см. ${LOG_DIR}/gazebo.log и ${LOG_DIR}/video_sender.log" >&2
        sg docker -c "docker logs --tail 120 bas-gazebo 2>&1" | sed 's/^/  gz: /' >&2 || true
        sg docker -c "docker logs --tail 80 bas-video-sender 2>&1" | sed 's/^/  tx: /' >&2 || true
        exit 4
    fi
}

ensure_root
ensure_docker
mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, scenario=${SCENARIO}"
echo "==> логи: $LOG_DIR"
echo "==> video: src=${BAS_VIDEO_SOURCE_RAW} (pipeline=${BAS_VIDEO_SOURCE}) ${BAS_VIDEO_WIDTH}x${BAS_VIDEO_HEIGHT}@${BAS_VIDEO_FPS} ${BAS_VIDEO_BITRATE_KBPS}kbps → ${BAS_VIDEO_DEST_HOST}:${BAS_VIDEO_DEST_PORT}"
echo "==> video record: ${BAS_VIDEO_RECORD_MP4}"
[ "${BAS_GAZEBO_GUI:-0}" = "1" ] && echo "==> gazebo GUI: enabled (WSLg/X11)"

echo "[1/9] подготовка радио-сети (control + payload bridges/TAPs)"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

echo "[2/9] тушим default compose"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/9] pause-контейнеры (uav-net + pload-far-net)"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net pload-far-net" 2>&1 | tail -3

# uav-net netns symlink + control veth + payload veth.
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav

# control: eth0 (как в 1.5.1)
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns bas-uav
ip -n bas-uav link set veth-uav name eth0
ip -n bas-uav addr add 10.10.0.2/24 dev eth0
ip -n bas-uav link set eth0 up
ip -n bas-uav link set lo up

# payload: eth1 (новое для 1.5.2)
ip link add veth-upl type veth peer name veth-upl-br
ip link set veth-upl-br master br-pload-near
ip link set veth-upl-br up
ip link set veth-upl netns bas-uav
ip -n bas-uav link set veth-upl name eth1
ip -n bas-uav addr add 10.20.0.2/24 dev eth1
ip -n bas-uav link set eth1 up

# pload-far-pod netns symlink + receiver veth.
PFAR_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-pload-far-net")
ln -sf "/proc/${PFAR_PID}/ns/net" /var/run/netns/bas-pload-far-pod
ip link add veth-pfar type veth peer name veth-pfar-br
ip link set veth-pfar-br master br-pload-far
ip link set veth-pfar-br up
ip link set veth-pfar netns bas-pload-far-pod
ip -n bas-pload-far-pod link set veth-pfar name eth0
ip -n bas-pload-far-pod addr add 10.20.0.3/24 dev eth0
ip -n bas-pload-far-pod link set eth0 up
ip -n bas-pload-far-pod link set lo up

echo "  bas-uav: $(ip -n bas-uav -br addr 2>&1 | grep -E 'eth' | head -2 | tr '\n' '|')"
echo "  bas-pload-far-pod: $(ip -n bas-pload-far-pod -br addr 2>&1 | grep eth | head -1)"

echo "[4/9] gazebo + sitl + mavbridge"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo sitl mavbridge" 2>&1 | tail -3

echo "[5/9] ждём SITL MAVLink на :5760"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" || {
    echo "SITL 5760 не открылся" >&2; sg docker -c "docker logs --tail 20 bas-sitl 2>&1"; exit 2
}
sleep 8

echo "[6/9] запуск ns-3 (ctrl: delay=${CTRL_DELAY_MS}ms loss=${CTRL_LOSS} outage='${CTRL_OUTAGE}'; pload: delay=${PLOAD_DELAY_MS}ms loss=${PLOAD_LOSS} outage='${PLOAD_OUTAGE}')"
NS3_ARGS="--runId=${RUN_ID} --duration=${NS3_DURATION}"
NS3_ARGS="${NS3_ARGS} --ctrlDelayMs=${CTRL_DELAY_MS} --ctrlLoss=${CTRL_LOSS}"
NS3_ARGS="${NS3_ARGS} --ploadDelayMs=${PLOAD_DELAY_MS} --ploadLoss=${PLOAD_LOSS}"
[ -n "${CTRL_OUTAGE}" ]  && NS3_ARGS="${NS3_ARGS} --ctrlOutage=${CTRL_OUTAGE}"
[ -n "${PLOAD_OUTAGE}" ] && NS3_ARGS="${NS3_ARGS} --ploadOutage=${PLOAD_OUTAGE}"

sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1 || true
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
for i in $(seq 1 $((NS3_START_TIMEOUT_SECONDS / 2))); do
    [ -s "$NS3_LOG" ] && break
    if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-ns3-stage15 2>/dev/null" | grep -q true; then
        echo "ns-3 контейнер завершился до старта" >&2
        sg docker -c "docker logs --tail 80 bas-ns3-stage15 2>&1" >&2 || true
        exit 3
    fi
    sleep 2
done
[ -s "$NS3_LOG" ] || {
    echo "ns-3 не стартовал за ${NS3_START_TIMEOUT_SECONDS}s" >&2
    sg docker -c "docker exec bas-ns3-stage15 tail -80 /tmp/build.log 2>&1" >&2 || true
    sg docker -c "docker logs --tail 80 bas-ns3-stage15 2>&1" >&2 || true
    exit 3
}
echo "  ns-3 готов"
sleep 5

# ARP-hygiene + увеличенные мульти/уникастные solicit'ы (наследие 1.5.1).
ip netns exec bas-ctrl-far    ip neigh flush all 2>/dev/null || true
ip netns exec bas-uav         ip neigh flush all 2>/dev/null || true
ip netns exec bas-pload-far-pod ip neigh flush all 2>/dev/null || true
for ns in bas-ctrl-far bas-uav bas-pload-far-pod; do
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.mcast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.ucast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.retrans_time_ms=2000 >/dev/null 2>&1 || true
done

echo "[7/9] запуск video-receiver (слушает 0.0.0.0:${BAS_VIDEO_DEST_PORT})"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d video-receiver" 2>&1 | tail -3
# Дать receiver'у пару секунд встать.
sleep 3
sg docker -c "docker logs --tail 5 bas-video-receiver 2>&1" | sed 's/^/  rx: /'

echo "[8/9] запуск video-sender (источник: ${BAS_VIDEO_SOURCE})"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d video-sender" 2>&1 | tail -3
sleep 3
sg docker -c "docker logs --tail 5 bas-video-sender 2>&1" | sed 's/^/  tx: /'
enable_gazebo_camera_stream
check_camera_video_flow

echo "[9/9] прогон миссии через ns-3 control канал (orchestrator в bas-ctrl-far netns)"
echo "  endpoint=udpout:10.10.0.2:14550 (через ns-3 → mavbridge → SITL TCP)"

set +e
ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/bas-orchestrator" "${SCENARIO}" \
    --real --external-compose \
    --mavlink-endpoint udpout:10.10.0.2:14550 \
    --run-dir "${LOG_DIR}" \
    --project-root "${REPO_ROOT}" 2>&1 | tee "${LOG_DIR}/orchestrator_stdout.log"
RC=${PIPESTATUS[0]}
set -e

# Дать receiver'у догрести буфер до закрытия pipeline.
sleep 3
sg docker -c "docker stop -t 5 bas-video-sender bas-video-receiver 2>/dev/null" >/dev/null 2>&1 || true

# Сохранить логи всех контейнеров для post-mortem.
sg docker -c "docker logs bas-sitl 2>&1"           > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1"         > "${LOG_DIR}/gazebo.log" 2>&1 || true
sg docker -c "docker logs bas-video-sender 2>&1"   > "${LOG_DIR}/video_sender.log" 2>&1 || true
sg docker -c "docker logs bas-video-receiver 2>&1" > "${LOG_DIR}/video_receiver.log" 2>&1 || true

echo
echo "[анализ]"
"${REPO_ROOT}/.venv/bin/bas-analyzer" "${LOG_DIR}" 2>&1 | tail -40

# Резюме видео-логов (для быстрой sanity check'и до того как analyzer выучит format).
TX_LINES=$(wc -l < "${LOG_DIR}/video_tx.jsonl" 2>/dev/null || echo 0)
RX_LINES=$(wc -l < "${LOG_DIR}/video_rx.jsonl" 2>/dev/null || echo 0)
VIDEO_MP4="${LOG_DIR}/video_rx.mp4"
echo
echo "Видео sanity:"
echo "  video_tx.jsonl: ${TX_LINES} записей"
echo "  video_rx.jsonl: ${RX_LINES} записей"
if [ -s "$VIDEO_MP4" ]; then
    echo "  video_rx.mp4: $(du -h "$VIDEO_MP4" | awk '{print $1}')"
else
    echo "  video_rx.mp4: не создан или пуст"
fi

echo
echo "Прогон завершён (RC=${RC}). Логи: ${LOG_DIR}"
exit ${RC}
