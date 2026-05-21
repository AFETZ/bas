#!/usr/bin/env bash
# Stage 2.4: Manual control of one BAS through MAVProxy command-line GCS.
#
# Acceptance path:
#   MAVProxy CLI in bas-ctrl-far netns -> ns-3 control channel ->
#   mavbridge UDP14550/TCP5760 -> SITL
#
# No direct pymavlink command sender and no mission upload are used here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${BAS_RUN_ID:-stage_2_4_mavproxy_gcs_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
NS3_DURATION="${NS3_DURATION:-600}"
NS3_START_TIMEOUT_SECONDS="${NS3_START_TIMEOUT_SECONDS:-300}"
MAVPROXY_MASTER="${BAS_STAGE24_MAVPROXY_MASTER:-udpout:10.10.0.2:14550}"
TAKEOFF_ALT="${BAS_STAGE24_TAKEOFF_ALT:-10}"
MODE="${1:-${BAS_STAGE24_MODE:-smoke}}"
SIONNA_CHANNEL_PATH="${BAS_SIONNA_CHANNEL_PATH:-${BAS_RF_CHANNEL_PATH:-}}"
SIONNA_CONTAINER_PATH=""

# Stage 2.4 FPV livestream: при BAS_GCS_FPV=1 поднимаем bas-fpv-mjpeg в
# bas-uav netns, который принимает RTP H.264 от Gazebo iris_with_gimbal
# (GstCameraPlugin → UDP loopback 5600) и раздаёт как multipart MJPEG TCP
# 0.0.0.0:8766. Web GCS проксирует это в /camera.mjpg → <img> в браузере.
BAS_GCS_FPV="${BAS_GCS_FPV:-0}"
export BAS_CAMERA_UDP_PORT="${BAS_CAMERA_UDP_PORT:-5600}"
export BAS_FPV_MJPEG_PORT="${BAS_FPV_MJPEG_PORT:-8766}"
# Эти env-переменные интерполируются docker compose из ХОСТ-окружения в
# command-блоке fpv-mjpeg, поэтому экспортировать их обязательно (иначе
# gst-launch получит пустые caps/framerate и сразу упадёт без открытия порта).
export BAS_FPV_WIDTH="${BAS_FPV_WIDTH:-640}"
export BAS_FPV_HEIGHT="${BAS_FPV_HEIGHT:-480}"
export BAS_FPV_FPS="${BAS_FPV_FPS:-15}"
export BAS_FPV_QUALITY="${BAS_FPV_QUALITY:-70}"
export BAS_FPV_CPUS="${BAS_FPV_CPUS:-0.6}"
export BAS_FPV_GST_DEBUG="${BAS_FPV_GST_DEBUG:-2}"
export BAS_CAMERA_ENABLE_TOPIC="${BAS_CAMERA_ENABLE_TOPIC:-/world/iris_runway/model/iris_with_gimbal/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming}"
# Для FPV-режима фиксируем мир с onboard камерой (тот же что в 1.5.2.b).
# Если оператор хочет RF-демо + FPV одновременно — это пока несовместимо
# (iris_runway_rf_demo.sdf не имеет камеры). Можно объединить миры позже.
if [ "$BAS_GCS_FPV" = "1" ]; then
    export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway.sdf}"
fi

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }

ensure_docker() {
    if docker info >/dev/null 2>&1; then return 0; fi
    service docker start >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    echo "Docker daemon did not become ready" >&2
    return 1
}

# Убрать stale gcs_web_ui_server.py с UI порта. После аварийного выхода
# (например crash python -> trap не сработал) Python процесс остаётся жив и
# держит :8765, ломая повторный запуск с "Address already in use". Ищем по
# именно нашему имени модуля чтобы не задеть случайные python-серверы.
kill_stale_ui() {
    local port="${1:-8765}"
    local pids
    pids="$(ss -tlnp 2>/dev/null \
            | awk -v p=":${port}" '$4 ~ p {print}' \
            | grep -oE 'pid=[0-9]+' \
            | cut -d= -f2 \
            | sort -u || true)"
    [ -z "$pids" ] && return 0
    for pid in $pids; do
        # Confirm это наш UI сервер (cmdline содержит gcs_web_ui_server).
        if grep -q gcs_web_ui_server "/proc/${pid}/cmdline" 2>/dev/null; then
            echo "  kill stale gcs_web_ui_server PID=${pid} on :${port}"
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        else
            echo "  WARN: port :${port} held by PID=${pid} (not our UI server)" >&2
        fi
    done
}

cleanup() {
    set +e
    echo "[cleanup]"
    # Сначала остановим UI сервер если он наш — иначе порт 8765 повиснет
    # для следующего запуска.
    kill_stale_ui "${BAS_GCS_UI_PORT:-8765}"
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage24 2>/dev/null" >/dev/null 2>&1
    timeout 30 sg docker -c "docker rm -f bas-fpv-mjpeg 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} --profile fpv down -v 2>/dev/null" >/dev/null 2>&1
    # FPV host-IP cleanup (idemptotent: del fails silently если не было).
    ip addr del 10.10.0.254/24 dev br-ctrl-near 2>/dev/null || true
    ip link del veth-uav-br >/dev/null 2>&1 || true
    ip link del veth-uav >/dev/null 2>&1 || true
    umount /var/run/netns/bas-uav >/dev/null 2>&1 || true
    rm -f /var/run/netns/bas-uav
    set -e
}

# --- FPV helpers ----------------------------------------------------------
# Включает GstCameraPlugin в Gazebo через enable_streaming gz topic и
# поднимает bas-fpv-mjpeg контейнер. Идемпотентно: если что-то уже
# запущено — просто проверит и пойдёт дальше.
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

start_fpv_pipeline() {
    [ "$BAS_GCS_FPV" = "1" ] || return 0

    # Хост по умолчанию не имеет IP на br-ctrl-near, поэтому gcs_web_ui_server
    # (запущен в host netns) не может достучаться до 10.10.0.2:8766 в bas-uav
    # netns. Даём bridge временный адрес 10.10.0.254/24, чтобы открыть L3
    # route к bas-uav. Удаляется в cleanup.
    if ! ip -4 addr show br-ctrl-near 2>/dev/null | grep -q "10.10.0.254/24"; then
        ip addr add 10.10.0.254/24 dev br-ctrl-near 2>/dev/null || true
        echo "[fpv] br-ctrl-near host IP: 10.10.0.254/24 (route to bas-uav)"
    fi

    echo "[fpv] enable iris_with_gimbal GstCameraPlugin"
    local discovered=0
    for _ in $(seq 1 20); do
        if discover_camera_enable_topic; then discovered=1; break; fi
        sleep 1
    done
    if [ "$discovered" -ne 1 ]; then
        echo "  camera enable topic not in gz topic -l; trying default"
    fi
    echo "  enable topic: ${BAS_CAMERA_ENABLE_TOPIC}"

    local ok=0
    for _ in $(seq 1 3); do
        if sg docker -c "docker exec bas-gazebo gz topic -t '${BAS_CAMERA_ENABLE_TOPIC}' -m gz.msgs.Boolean -p 'data: true' >/tmp/bas_fpv_enable.log 2>&1"; then
            ok=1
        fi
        sleep 1
    done
    if [ "$ok" -ne 1 ]; then
        echo "  WARN: enable_streaming publish failed — FPV stream may not appear" >&2
        sg docker -c "docker exec bas-gazebo cat /tmp/bas_fpv_enable.log 2>/dev/null" >&2 || true
    fi

    echo "[fpv] start bas-fpv-mjpeg (MJPEG TCP 0.0.0.0:${BAS_FPV_MJPEG_PORT})"
    sg docker -c "docker compose -f ${COMPOSE_FILE} --profile fpv up -d fpv-mjpeg" 2>&1 | tail -3

    # Дождёмся пока tcpserversink реально откроет 8766 (gst pipeline сам по
    # себе бьёт ERROR если нет RTP source, но порт открывается всё равно).
    local waited=0
    while [ "$waited" -lt 12 ]; do
        if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":${BAS_FPV_MJPEG_PORT}"; then
            echo "  fpv-mjpeg TCP listener up on :${BAS_FPV_MJPEG_PORT}"
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
    if ! ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":${BAS_FPV_MJPEG_PORT}"; then
        echo "  WARN: fpv-mjpeg did not open :${BAS_FPV_MJPEG_PORT} within ${waited}s" >&2
        sg docker -c "docker logs --tail 40 bas-fpv-mjpeg 2>&1" | sed 's/^/  fpv: /' >&2 || true
    fi
}

wait_for_container_netns() {
    local container="$1"
    local netns_name="$2"
    local pid=""

    mkdir -p /var/run/netns
    umount "/var/run/netns/${netns_name}" >/dev/null 2>&1 || true
    rm -f "/var/run/netns/${netns_name}"

    for _ in $(seq 1 60); do
        pid="$(
            sg docker -c "docker inspect --format '{{.State.Pid}} {{.State.Running}}' ${container}" 2>/dev/null \
                | awk '$2 == "true" && $1 + 0 > 1 {print $1; exit}'
        )"
        if [ -n "$pid" ] && [ -e "/proc/${pid}/ns/net" ]; then
            ln -sfnT "/proc/${pid}/ns/net" "/var/run/netns/${netns_name}"
            if ip netns exec "$netns_name" true >/dev/null 2>&1; then
                printf '%s\n' "$pid"
                return 0
            fi
            umount "/var/run/netns/${netns_name}" >/dev/null 2>&1 || true
            rm -f "/var/run/netns/${netns_name}"
        fi
        sleep 0.5
    done

    echo "Container ${container} did not expose a usable network namespace" >&2
    sg docker -c "docker inspect --format 'pid={{.State.Pid}} running={{.State.Running}} status={{.State.Status}}' ${container} 2>&1" >&2 || true
    return 1
}

case "$MODE" in
    smoke|interactive|ui|dry-run) ;;
    *)
        echo "Unknown mode: ${MODE}. Use smoke, interactive, or dry-run." >&2
        exit 2
        ;;
esac

mkdir -p "$LOG_DIR"
if [ -n "$SIONNA_CHANNEL_PATH" ]; then
    mkdir -p "$(dirname "$SIONNA_CHANNEL_PATH")"
    printf '{"loss_ratio":0.0,"extra_delay_ms":0.0,"rssi_db":-55.0,"rss_db":-55.0,"status":"LOS","los":true}\n' > "$SIONNA_CHANNEL_PATH"
    SIONNA_CONTAINER_PATH="$SIONNA_CHANNEL_PATH"
    if [[ "$SIONNA_CHANNEL_PATH" == /tmp/* ]]; then
        SIONNA_CONTAINER_PATH="/tmp/$(basename "$SIONNA_CHANNEL_PATH")"
    fi
fi

if [ "$MODE" = "dry-run" ]; then
    "${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/mavproxy_stage_2_4_driver.py" \
        --dry-run \
        --run-id "$RUN_ID" \
        --log-dir "$LOG_DIR" \
        --master "$MAVPROXY_MASTER" \
        --takeoff-alt "$TAKEOFF_ALT"
    exit $?
fi

ensure_root
ensure_docker

# Preflight: убрать с UI-порта остатки от аварийных запусков. Только если
# режим действительно поднимает Web UI (mode=ui).
if [ "$MODE" = "ui" ]; then
    kill_stale_ui "${BAS_GCS_UI_PORT:-8765}"
fi

trap cleanup EXIT INT TERM

[ -x "${REPO_ROOT}/.venv/bin/python" ] || {
    echo ".venv/bin/python not found" >&2
    exit 1
}
[ -x "${REPO_ROOT}/.venv/bin/mavproxy.py" ] || {
    echo ".venv/bin/mavproxy.py not found" >&2
    exit 1
}

echo "==> run_id=${RUN_ID}"
echo "==> logs: ${LOG_DIR}"
echo "==> mode: ${MODE}"
echo "==> MAVProxy master: ${MAVPROXY_MASTER}"
[ -n "$SIONNA_CHANNEL_PATH" ] && echo "==> RF/Sionna channel: ${SIONNA_CHANNEL_PATH}"
echo "==> chain: MAVProxy -> bas-ctrl-far netns -> ns-3 control -> mavbridge -> SITL"

"${REPO_ROOT}/.venv/bin/mavproxy.py" --help > "${LOG_DIR}/mavproxy_help.txt" 2>&1 || true
"${REPO_ROOT}/.venv/bin/mavproxy.py" --version > "${LOG_DIR}/mavproxy_version.txt" 2>&1 || true
if grep -q -- "--script" "${LOG_DIR}/mavproxy_help.txt"; then
    echo "Unexpected MAVProxy --script option found; review runner assumptions" >&2
    exit 2
fi

echo "[1/7] prepare control bridges/TAPs"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

echo "[2/7] stop default compose stack"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/7] start uav-net pause container and inject control veth"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3
UAV_PID="$(wait_for_container_netns bas-uav-net bas-uav)"
echo "  bas-uav netns: PID=${UAV_PID}"

ip link del veth-uav-br >/dev/null 2>&1 || true
ip link del veth-uav >/dev/null 2>&1 || true
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns "$UAV_PID"
ip -n bas-uav link set veth-uav name eth0
ip -n bas-uav addr add 10.10.0.2/24 dev eth0
ip -n bas-uav link set eth0 up
ip -n bas-uav link set lo up

echo "[4/7] start Gazebo, SITL, mavbridge"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -3
echo "  waiting 6s for Gazebo FDM"
sleep 6
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl mavbridge" 2>&1 | tail -3

echo "[5/7] wait for SITL MAVLink on :5760"
for _ in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
if ! ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then
    echo "SITL did not open TCP :5760" >&2
    sg docker -c "docker logs --tail 60 bas-sitl 2>&1" >&2 || true
    exit 2
fi
sleep 10

# Если оператор включил FPV, поднимем mjpeg-стрим параллельно с ns-3, не
# блокируя основной пайплайн при ошибках камеры (start_fpv_pipeline печатает
# WARN'ы но не падает).
start_fpv_pipeline

echo "[6/7] start ns-3 control channel (baseline_wifi: 5ms delay, no loss)"
NS3_ARGS="--runId=${RUN_ID} --duration=${NS3_DURATION}"
NS3_ARGS="${NS3_ARGS} --ctrlDelayMs=5 --ctrlLoss=0.0"
NS3_ARGS="${NS3_ARGS} --ploadDelayMs=10 --ploadLoss=0.0"
if [ -n "$SIONNA_CONTAINER_PATH" ]; then
    NS3_ARGS="${NS3_ARGS} --sionnaChannelPath=${SIONNA_CONTAINER_PATH}"
fi

NS3_TMP_MOUNT=""
NS3_TMP_LINK=""
if [[ "$SIONNA_CHANNEL_PATH" == /tmp/* ]]; then
    NS3_TMP_MOUNT="-v /tmp:/host_tmp"
    NS3_TMP_LINK="ln -sf /host_tmp/$(basename "$SIONNA_CHANNEL_PATH") ${SIONNA_CONTAINER_PATH} && "
fi

sg docker -c "docker rm -f bas-ns3-stage24 2>/dev/null" >/dev/null 2>&1 || true
sg docker -c "docker run -d --name bas-ns3-stage24 --network host --cap-add NET_ADMIN --privileged \
    -e NS3_ARGS='${NS3_ARGS}' \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    ${NS3_TMP_MOUNT} \
    --entrypoint bash bas/ns3:dev -c '\
        ${NS3_TMP_LINK} \
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src \
        && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} \$NS3_ARGS'" > /dev/null

NS3_LOG="${LOG_DIR}/ns3_events.jsonl"
for _ in $(seq 1 $((NS3_START_TIMEOUT_SECONDS / 2))); do
    [ -s "$NS3_LOG" ] && break
    if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-ns3-stage24 2>/dev/null" | grep -q true; then
        echo "ns-3 container exited before readiness" >&2
        sg docker -c "docker logs --tail 120 bas-ns3-stage24 2>&1" >&2 || true
        exit 3
    fi
    sleep 2
done
if [ ! -s "$NS3_LOG" ]; then
    echo "ns-3 did not become ready within ${NS3_START_TIMEOUT_SECONDS}s" >&2
    sg docker -c "docker exec bas-ns3-stage24 tail -120 /tmp/build.log 2>&1" >&2 || true
    sg docker -c "docker logs --tail 120 bas-ns3-stage24 2>&1" >&2 || true
    exit 3
fi
echo "  ns-3 control channel is ready"
sleep 5

ip netns exec bas-ctrl-far ip neigh flush all 2>/dev/null || true
ip netns exec bas-uav ip neigh flush all 2>/dev/null || true
for ns in bas-ctrl-far bas-uav; do
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.mcast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.ucast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.retrans_time_ms=2000 >/dev/null 2>&1 || true
done

echo "[7/7] run MAVProxy command-line GCS"
echo "  acceptance commands are sent only through MAVProxy stdin"
echo "  mission upload: false"
echo "  direct pymavlink command path: false"
echo

DRIVER_MODE="--smoke"
if [ "$MODE" = "interactive" ]; then
    DRIVER_MODE="--interactive"
fi

set +e
if [ "$MODE" = "ui" ]; then
    UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
    UI_PORT="${BAS_GCS_UI_PORT:-8765}"
    echo "  operator UI: http://${UI_HOST}:${UI_PORT}/"
    echo "  Gazebo GUI: set BAS_GAZEBO_GUI=1 before launch to open the simulator window"
    "${REPO_ROOT}/.venv/bin/python" \
        "${REPO_ROOT}/scripts/gcs_web_ui_server.py" \
        --run-id "$RUN_ID" \
        --log-dir "$LOG_DIR" \
        --master "$MAVPROXY_MASTER" \
        --takeoff-alt "$TAKEOFF_ALT" \
        --netns bas-ctrl-far \
        --host "$UI_HOST" \
        --port "$UI_PORT" \
        ${BAS_GCS_RF_DEMO:+--rf-demo}
    RC=$?
elif [ "$MODE" = "interactive" ]; then
    ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/python" \
        "${REPO_ROOT}/scripts/mavproxy_stage_2_4_driver.py" \
        "$DRIVER_MODE" \
        --run-id "$RUN_ID" \
        --log-dir "$LOG_DIR" \
        --master "$MAVPROXY_MASTER" \
        --takeoff-alt "$TAKEOFF_ALT"
    RC=$?
else
    ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/python" \
        "${REPO_ROOT}/scripts/mavproxy_stage_2_4_driver.py" \
        "$DRIVER_MODE" \
        --run-id "$RUN_ID" \
        --log-dir "$LOG_DIR" \
        --master "$MAVPROXY_MASTER" \
        --takeoff-alt "$TAKEOFF_ALT" \
        2>&1 | tee "${LOG_DIR}/driver_stdout.log"
    RC=${PIPESTATUS[0]}
fi
set -e

sg docker -c "docker logs bas-sitl 2>&1" > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1" > "${LOG_DIR}/gazebo.log" 2>&1 || true
sg docker -c "docker logs bas-mavbridge 2>&1" > "${LOG_DIR}/mavbridge.log" 2>&1 || true
sg docker -c "docker logs bas-ns3-stage24 2>&1" > "${LOG_DIR}/ns3_stdout.log" 2>&1 || true
ip netns exec bas-ctrl-far ip addr > "${LOG_DIR}/bas_ctrl_far_addr.txt" 2>&1 || true
ip netns exec bas-uav ip addr > "${LOG_DIR}/bas_uav_addr.txt" 2>&1 || true

echo
echo "Stage 2.4 MAVProxy GCS result:"
echo "  exit=${RC}"
echo "  logs=${LOG_DIR}"
echo "  report=${LOG_DIR}/report.md"
exit "$RC"
