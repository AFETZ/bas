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
# Sionna live hook target flow: payload (back-compat default) | control | both.
# В FPV+RF демо имеет смысл "both" — за зданием падает и видео, и команды.
SIONNA_TARGET_FLOW="${BAS_SIONNA_TARGET_FLOW:-payload}"
STAGE24_PACKET_AUDIT="${BAS_STAGE24_PACKET_AUDIT:-0}"
STAGE24_PACKET_AUDIT_SEED="${BAS_STAGE24_PACKET_AUDIT_SEED:-1337}"
STAGE24_PACKET_AUDIT_CSV="${BAS_STAGE24_PACKET_AUDIT_CSV:-${LOG_DIR}/stage24_route_rssi_packets_raw.csv}"
BAS_REQUIRE_NS3_PAYLOAD="${BAS_REQUIRE_NS3_PAYLOAD:-0}"
BAS_PAYLOAD_BYPASS="${BAS_PAYLOAD_BYPASS:-0}"

# Stage 2.4 FPV/payload:
#   default network-realistic path:
#     Gazebo camera/video source -> bas-uav eth1 -> tap-pload-near ->
#     ns-3 payload channel -> tap-pload-far -> bas-pload-far-net receiver ->
#     receiver-side MJPEG TCP -> Web GCS.
#   debug-only bypass:
#     BAS_PAYLOAD_BYPASS=1 keeps the old bas-fpv-mjpeg UAV-side path.
#   BAS_REQUIRE_NS3_PAYLOAD=1 makes bypass invalid and fails the experiment if
#   video_rx/packet audit do not prove the post-ns-3 payload path.
BAS_GCS_FPV="${BAS_GCS_FPV:-0}"
export BAS_CAMERA_UDP_PORT="${BAS_CAMERA_UDP_PORT:-5600}"
export BAS_FPV_MJPEG_PORT="${BAS_FPV_MJPEG_PORT:-8766}"
if [ "$BAS_REQUIRE_NS3_PAYLOAD" = "1" ] && [ "$BAS_PAYLOAD_BYPASS" = "1" ]; then
    echo "Payload path bypassed ns-3, payload metrics are invalid for network experiment." >&2
    exit 2
fi
BAS_NS3_PAYLOAD="${BAS_NS3_PAYLOAD:-0}"
if [ "$BAS_REQUIRE_NS3_PAYLOAD" = "1" ]; then
    BAS_NS3_PAYLOAD=1
    STAGE24_PACKET_AUDIT=1
fi
if [ "$BAS_GCS_FPV" = "1" ] && [ "$BAS_PAYLOAD_BYPASS" != "1" ]; then
    BAS_NS3_PAYLOAD=1
fi
export BAS_NS3_PAYLOAD

# QGroundControl bridge: заменяет mavbridge (socat 1↔1) на bluenviron/mavp2p,
# который держит один TCP client к SITL и serves MAVLink на нескольких UDP
# server endpoint'ах одновременно — стандартный paттерн mavlink-router/Intel
# адаптированный под наш netns layout. При BAS_GCS_QGC=1 wrapper:
#  * пропускает mavbridge service;
#  * поднимает mavrouter (profile qgc);
#  * поднимает host-side socat UDP relay 14560 → 10.10.0.2:14560 (доступ из
#    Windows-QGC через WSL2 localhost/eth0 forwarding).
BAS_GCS_QGC="${BAS_GCS_QGC:-0}"
export BAS_QGC_HOST_PORT="${BAS_QGC_HOST_PORT:-14560}"
export BAS_QGC_UAV_PORT="${BAS_QGC_UAV_PORT:-14560}"

# Online Sionna RT: вместо geometric model (Web UI rf_loop) ИЛИ offline radio
# map lookup используем live PathSolver на каждый UAV update. Pattern из
# robpegurri/ns3-rt + bluenviron Sionna 1.x документации: load Mitsuba scene
# один раз, на каждом UAV move двигаем Receiver и runs PathSolver.
# Latency ~55мс на CPU (LLVM JIT), достаточно для 10Hz polling.
# Multi-UAV demo: 2 SITL экземпляра + iris_runway_multi.sdf + mavrouter-multi
# (vместо single mavbridge). Реалистичный pattern для будущего swarm-расширения.
BAS_GCS_MULTI_UAV="${BAS_GCS_MULTI_UAV:-0}"

BAS_SIONNA_RT_ONLINE="${BAS_SIONNA_RT_ONLINE:-0}"
export BAS_RT_SCENE_PATH="${BAS_RT_SCENE_PATH:-${REPO_ROOT}/scene/iris_runway.xml}"
export BAS_RT_TX_POS="${BAS_RT_TX_POS:-0,-60,1.5}"
export BAS_RT_MAX_DEPTH="${BAS_RT_MAX_DEPTH:-2}"
export BAS_MITSUBA_VARIANT="${BAS_MITSUBA_VARIANT:-${MITSUBA_VARIANT:-}}"
export BAS_SIONNA_REQUIRE_GPU="${BAS_SIONNA_REQUIRE_GPU:-0}"
# Online RT пишет в отдельный JSON чтобы не конфликтовать с Web UI rf_loop
# (тот продолжает обслуживать UI panel). ns-3 поллит RT файл напрямую.
export BAS_RT_CHANNEL_PATH="${BAS_RT_CHANNEL_PATH:-/tmp/bas_stage24_rt.json}"
export BAS_RT_HISTORY_PATH="${BAS_RT_HISTORY_PATH:-${LOG_DIR}/sionna_rt_history.jsonl}"
if [ "$BAS_SIONNA_RT_ONLINE" = "1" ]; then
    # Override SIONNA_CHANNEL_PATH — ns-3 теперь поллит RT live JSON
    SIONNA_CHANNEL_PATH="$BAS_RT_CHANNEL_PATH"
fi
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
export BAS_VIDEO_SOURCE_RAW="${BAS_VIDEO_SOURCE:-camera}"
case "$BAS_VIDEO_SOURCE_RAW" in
    camera) export BAS_VIDEO_SOURCE="udpsrc:${BAS_CAMERA_UDP_PORT}" ;;
    *)      export BAS_VIDEO_SOURCE="$BAS_VIDEO_SOURCE_RAW" ;;
esac
export BAS_VIDEO_DEST_HOST="${BAS_VIDEO_DEST_HOST:-10.20.0.3}"
export BAS_VIDEO_DEST_PORT="${BAS_VIDEO_DEST_PORT:-5000}"
export BAS_VIDEO_BITRATE_KBPS="${BAS_VIDEO_BITRATE_KBPS:-2000}"
export BAS_VIDEO_FPS="${BAS_VIDEO_FPS:-30}"
export BAS_VIDEO_WIDTH="${BAS_VIDEO_WIDTH:-640}"
export BAS_VIDEO_HEIGHT="${BAS_VIDEO_HEIGHT:-480}"
export BAS_VIDEO_TX_LOG="/work/logs/${RUN_ID}/video_tx.jsonl"
export BAS_VIDEO_RX_LOG="/work/logs/${RUN_ID}/video_rx.jsonl"
export BAS_VIDEO_RECORD_MP4="${BAS_VIDEO_RECORD_MP4:-/work/logs/${RUN_ID}/video_rx.mp4}"
export BAS_VIDEO_MJPEG_PORT="${BAS_VIDEO_MJPEG_PORT:-$BAS_FPV_MJPEG_PORT}"
export BAS_VIDEO_MJPEG_WIDTH="${BAS_VIDEO_MJPEG_WIDTH:-$BAS_FPV_WIDTH}"
export BAS_VIDEO_MJPEG_HEIGHT="${BAS_VIDEO_MJPEG_HEIGHT:-$BAS_FPV_HEIGHT}"
export BAS_VIDEO_MJPEG_FPS="${BAS_VIDEO_MJPEG_FPS:-$BAS_FPV_FPS}"
export BAS_VIDEO_MJPEG_QUALITY="${BAS_VIDEO_MJPEG_QUALITY:-$BAS_FPV_QUALITY}"
export BAS_VIDEO_MJPEG_BOUNDARY="${BAS_VIDEO_MJPEG_BOUNDARY:-spionkop}"
export BAS_NS3_PAYLOAD_WARMUP_SECONDS="${BAS_NS3_PAYLOAD_WARMUP_SECONDS:-45}"
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    export BAS_FPV_UPSTREAM_HOST="${BAS_FPV_UPSTREAM_HOST:-10.20.0.3}"
    export BAS_FPV_UPSTREAM_PORT="${BAS_FPV_UPSTREAM_PORT:-$BAS_VIDEO_MJPEG_PORT}"
    export BAS_FPV_BOUNDARY="${BAS_FPV_BOUNDARY:-$BAS_VIDEO_MJPEG_BOUNDARY}"
fi
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
    # Стопим QGC host-side socat если он запускался.
    if [ -f /tmp/bas_qgc_socat.pid ]; then
        kill "$(cat /tmp/bas_qgc_socat.pid)" 2>/dev/null || true
        rm -f /tmp/bas_qgc_socat.pid
    fi
    # Стопим Sionna RT publisher если он запускался.
    if [ -f /tmp/bas_sionna_rt.pid ]; then
        kill "$(cat /tmp/bas_sionna_rt.pid)" 2>/dev/null || true
        rm -f /tmp/bas_sionna_rt.pid
    fi
    pkill -f "sionna_channel_publisher.*rt-online" 2>/dev/null || true
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage24 2>/dev/null" >/dev/null 2>&1
    timeout 30 sg docker -c "docker rm -f bas-fpv-mjpeg bas-video-sender bas-video-receiver bas-pload-far-net 2>/dev/null" >/dev/null 2>&1
    timeout 30 sg docker -c "docker rm -f bas-mavrouter 2>/dev/null" >/dev/null 2>&1
    timeout 30 sg docker -c "docker rm -f bas-mavrouter-multi bas-sitl2 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} --profile fpv --profile qgc --profile multi down -v 2>/dev/null" >/dev/null 2>&1
    # FPV host-IP cleanup (idemptotent: del fails silently если не было).
    ip addr del 10.10.0.254/24 dev br-ctrl-near 2>/dev/null || true
    ip addr del 10.20.0.254/24 dev br-pload-far 2>/dev/null || true
    ip link del veth-uav-br >/dev/null 2>&1 || true
    ip link del veth-uav >/dev/null 2>&1 || true
    ip link del veth-upl-br >/dev/null 2>&1 || true
    ip link del veth-upl >/dev/null 2>&1 || true
    ip link del veth-pfar-br >/dev/null 2>&1 || true
    ip link del veth-pfar >/dev/null 2>&1 || true
    umount /var/run/netns/bas-uav >/dev/null 2>&1 || true
    umount /var/run/netns/bas-pload-far-pod >/dev/null 2>&1 || true
    rm -f /var/run/netns/bas-uav
    rm -f /var/run/netns/bas-pload-far-pod
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
    [ "$BAS_PAYLOAD_BYPASS" = "1" ] || return 0

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

enable_gazebo_camera_stream() {
    [ "$BAS_VIDEO_SOURCE_RAW" = "camera" ] || return 0

    echo "[camera] enable iris_with_gimbal GstCameraPlugin"
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
        if sg docker -c "docker exec bas-gazebo gz topic -t '${BAS_CAMERA_ENABLE_TOPIC}' -m gz.msgs.Boolean -p 'data: true' >/tmp/bas_camera_enable.log 2>&1"; then
            ok=1
        fi
        sleep 1
    done
    if [ "$ok" -ne 1 ]; then
        echo "  WARN: enable_streaming publish failed — camera RTP may not appear" >&2
        sg docker -c "docker exec bas-gazebo cat /tmp/bas_camera_enable.log 2>/dev/null" >&2 || true
        return 1
    fi
}

setup_ns3_payload_netns() {
    [ "$BAS_NS3_PAYLOAD" = "1" ] || return 0

    echo "  setup payload path: bas-uav eth1=10.20.0.2/24 -> ns-3 payload -> bas-pload-far-net eth0=10.20.0.3/24"

    ip link del veth-upl-br >/dev/null 2>&1 || true
    ip link del veth-upl >/dev/null 2>&1 || true
    ip link del veth-pfar-br >/dev/null 2>&1 || true
    ip link del veth-pfar >/dev/null 2>&1 || true

    ip link add veth-upl type veth peer name veth-upl-br
    ip link set veth-upl-br master br-pload-near
    ip link set veth-upl-br up
    ip link set veth-upl netns bas-uav
    ip -n bas-uav link set veth-upl name eth1
    ip -n bas-uav addr add 10.20.0.2/24 dev eth1
    ip -n bas-uav link set eth1 up

    PFAR_PID="$(wait_for_container_netns bas-pload-far-net bas-pload-far-pod)"
    echo "  bas-pload-far-net netns: PID=${PFAR_PID}"
    ip link add veth-pfar type veth peer name veth-pfar-br
    ip link set veth-pfar-br master br-pload-far
    ip link set veth-pfar-br up
    ip link set veth-pfar netns bas-pload-far-pod
    ip -n bas-pload-far-pod link set veth-pfar name eth0
    ip -n bas-pload-far-pod addr add 10.20.0.3/24 dev eth0
    ip -n bas-pload-far-pod link set eth0 up
    ip -n bas-pload-far-pod link set lo up

    if ! ip -4 addr show br-pload-far 2>/dev/null | grep -q "10.20.0.254/24"; then
        ip addr add 10.20.0.254/24 dev br-pload-far 2>/dev/null || true
        echo "  br-pload-far host IP: 10.20.0.254/24 (route to post-ns3 receiver)"
    fi
}

start_ns3_payload_pipeline() {
    [ "$BAS_NS3_PAYLOAD" = "1" ] || return 0

    echo "[payload] start post-ns3 video receiver (${BAS_VIDEO_DEST_PORT}/udp, MJPEG ${BAS_FPV_UPSTREAM_HOST}:${BAS_FPV_UPSTREAM_PORT})"
    sg docker -c "docker compose -f ${COMPOSE_FILE} up -d video-receiver" 2>&1 | tail -3
    sleep 3
    sg docker -c "docker logs --tail 8 bas-video-receiver 2>&1" | sed 's/^/  rx: /'

    echo "[payload] start video sender: ${BAS_VIDEO_SOURCE_RAW} (${BAS_VIDEO_SOURCE}) -> ${BAS_VIDEO_DEST_HOST}:${BAS_VIDEO_DEST_PORT}"
    sg docker -c "docker compose -f ${COMPOSE_FILE} up -d video-sender" 2>&1 | tail -3
    sleep 3
    sg docker -c "docker logs --tail 8 bas-video-sender 2>&1" | sed 's/^/  tx: /'
    enable_gazebo_camera_stream || true
    check_ns3_payload_flow
}

check_ns3_payload_flow() {
    [ "$BAS_NS3_PAYLOAD" = "1" ] || return 0

    local tx_log="${LOG_DIR}/video_tx.jsonl"
    local rx_log="${LOG_DIR}/video_rx.jsonl"
    local tx_lines=0
    local rx_lines=0
    local payload_audit_rows=0
    local waited=0
    while [ "$waited" -le "$BAS_NS3_PAYLOAD_WARMUP_SECONDS" ]; do
        [ -f "$tx_log" ] && tx_lines=$(wc -l < "$tx_log" 2>/dev/null || echo 0)
        [ -f "$rx_log" ] && rx_lines=$(wc -l < "$rx_log" 2>/dev/null || echo 0)
        if [ "$STAGE24_PACKET_AUDIT" = "1" ] && [ -f "$STAGE24_PACKET_AUDIT_CSV" ]; then
            payload_audit_rows=$(awk -F, 'NR>1 && $4=="payload"{n++} END{print n+0}' "$STAGE24_PACKET_AUDIT_CSV" 2>/dev/null || echo 0)
        fi
        if [ "${tx_lines:-0}" -gt 1 ] && [ "${rx_lines:-0}" -gt 1 ] && { [ "$STAGE24_PACKET_AUDIT" != "1" ] || [ "${payload_audit_rows:-0}" -gt 0 ]; }; then
            break
        fi
        if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-video-sender 2>/dev/null" | grep -q true; then
            break
        fi
        if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-video-receiver 2>/dev/null" | grep -q true; then
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done

    echo "  payload sanity: video_tx=${tx_lines:-0} video_rx=${rx_lines:-0} ns3_payload_audit=${payload_audit_rows:-0} (${waited}s)"
    if [ "${tx_lines:-0}" -le 1 ] || [ "${rx_lines:-0}" -le 1 ] || { [ "$STAGE24_PACKET_AUDIT" = "1" ] && [ "${payload_audit_rows:-0}" -le 0 ]; }; then
        local msg="Payload path bypassed ns-3, payload metrics are invalid for network experiment."
        echo "  WARN: ${msg}" >&2
        sg docker -c "docker logs bas-video-sender 2>&1"   > "${LOG_DIR}/video_sender.log" 2>&1 || true
        sg docker -c "docker logs bas-video-receiver 2>&1" > "${LOG_DIR}/video_receiver.log" 2>&1 || true
        if [ "$BAS_REQUIRE_NS3_PAYLOAD" = "1" ]; then
            echo "${msg}" >&2
            exit 5
        fi
    fi
}

# Host-side socat UDP relay для QGroundControl. QGC на Windows пишет на
# UDP :14560 хоста (через WSL2 localhost forwarding или mirrored networking).
# Мы переадресуем эти пакеты в bas-uav netns на mavrouter UDP 14560 server.
# Bidirectional: ответы (heartbeat от SITL) идут обратно через тот же socket.
start_qgc_host_relay() {
    [ "$BAS_GCS_QGC" = "1" ] || return 0

    # Убедимся что mavrouter поднял :14560 в bas-uav netns.
    local waited=0
    while [ "$waited" -lt 12 ]; do
        if ip netns exec bas-uav ss -uln 2>/dev/null | grep -q ":${BAS_QGC_UAV_PORT}"; then
            echo "  mavrouter UDP listener up on bas-uav:${BAS_QGC_UAV_PORT}"
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
    if ! ip netns exec bas-uav ss -uln 2>/dev/null | grep -q ":${BAS_QGC_UAV_PORT}"; then
        echo "  WARN: mavrouter did not open :${BAS_QGC_UAV_PORT} within ${waited}s" >&2
        sg docker -c "docker logs --tail 30 bas-mavrouter 2>&1" | sed 's/^/  mavrouter: /' >&2 || true
        return 1
    fi

    # Host IP на br-ctrl-near чтобы достучаться до bas-uav 10.10.0.2 (тот же
    # подход что в start_fpv_pipeline — добавляем 10.10.0.254/24 если ещё нет).
    if ! ip -4 addr show br-ctrl-near 2>/dev/null | grep -q "10.10.0.254/24"; then
        ip addr add 10.10.0.254/24 dev br-ctrl-near 2>/dev/null || true
        echo "[qgc] br-ctrl-near host IP: 10.10.0.254/24 (route to bas-uav)"
    fi

    # Убрать старый relay если он остался (например после crashed run).
    pkill -f "socat.*UDP4-LISTEN:${BAS_QGC_HOST_PORT}.*10.10.0.2" 2>/dev/null || true
    sleep 1

    # Запускаем socat в background. Каждое QGC-подключение получает свой
    # forked процесс, который держит UDP socket к bas-uav:14560 двусторонне.
    # bind=0.0.0.0 — слушаем на всех интерфейсах, чтобы Windows-QGC мог
    # подключиться по WSL eth0 IP или через mirrored localhost.
    nohup socat -d \
        "UDP4-LISTEN:${BAS_QGC_HOST_PORT},bind=0.0.0.0,reuseaddr,fork" \
        "UDP4:10.10.0.2:${BAS_QGC_UAV_PORT}" \
        > "${LOG_DIR}/qgc_socat.log" 2>&1 &
    echo $! > /tmp/bas_qgc_socat.pid
    sleep 1
    if ! kill -0 "$(cat /tmp/bas_qgc_socat.pid 2>/dev/null)" 2>/dev/null; then
        echo "  WARN: host-side QGC socat не стартовал — см. ${LOG_DIR}/qgc_socat.log" >&2
        return 1
    fi
    echo "[qgc] host UDP relay: 0.0.0.0:${BAS_QGC_HOST_PORT} -> 10.10.0.2:${BAS_QGC_UAV_PORT}"

    # Печатаем WSL eth0 IP — оператору это нужно для QGC connection link.
    local wsl_ip
    wsl_ip="$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
    cat <<QGC

==========================================================================
 QGroundControl bridge готов
--------------------------------------------------------------------------
 В QGC: Application Settings → Comm Links → Add → UDP
   * Name:        BAS-WSL
   * Port:        ${BAS_QGC_HOST_PORT}
   * Server addr: ${wsl_ip:-<WSL_eth0_IP>}   (или localhost если WSL2 mirrored)
 Затем Connect — heartbeat появится сразу после старта SITL.
==========================================================================

QGC
}

start_sionna_rt_publisher() {
    [ "$BAS_SIONNA_RT_ONLINE" = "1" ] || return 0

    local venv_python="${REPO_ROOT}/sionna_env/bin/python"
    if [ ! -x "$venv_python" ]; then
        echo "  WARN: sionna_env venv не найден (${venv_python}); skip live RT" >&2
        return 1
    fi
    if [ ! -f "$BAS_RT_SCENE_PATH" ]; then
        echo "  WARN: Mitsuba scene не найдена: ${BAS_RT_SCENE_PATH}; skip live RT" >&2
        return 1
    fi

    # Стопим старый publisher если остался от crashed run.
    pkill -f "sionna_channel_publisher.*rt-online" 2>/dev/null || true

    # Initial seed file — пока publisher делает warm-up (~2с), ns-3 видит
    # дефолтный LOS state без потерь, не висит на чтении.
    mkdir -p "$(dirname "$BAS_RT_CHANNEL_PATH")"
    printf '{"loss_ratio":0.0,"extra_delay_ms":0.0,"rss_db":-55.0,"status":"LOS","los":true,"channel_model":"rt_online_warmup"}\n' \
        > "$BAS_RT_CHANNEL_PATH"

    local gpu_args=()
    if [ -n "$BAS_MITSUBA_VARIANT" ]; then
        gpu_args+=(--mitsuba-variant "$BAS_MITSUBA_VARIANT")
    fi
    if [ "$BAS_SIONNA_REQUIRE_GPU" = "1" ]; then
        gpu_args+=(--require-gpu)
    fi
    local history_args=()
    if [ -n "$BAS_RT_HISTORY_PATH" ]; then
        history_args+=(--history-out "$BAS_RT_HISTORY_PATH")
    fi
    local launcher=()
    if [ "$BAS_SIONNA_REQUIRE_GPU" = "1" ] || [[ "$BAS_MITSUBA_VARIANT" == cuda* ]]; then
        launcher=(bash "${REPO_ROOT}/scripts/run_sionna_live.sh" --)
    fi

    echo "[sionna-rt] start live PathSolver publisher (scene=${BAS_RT_SCENE_PATH##*/})"
    echo "  history: ${BAS_RT_HISTORY_PATH:-disabled}"
    nohup "${launcher[@]}" \
        "$venv_python" "${REPO_ROOT}/scripts/sionna_channel_publisher.py" \
        --events "${LOG_DIR}/events.jsonl" \
        --rt-online \
        --rt-scene "$BAS_RT_SCENE_PATH" \
        --rt-tx "$BAS_RT_TX_POS" \
        --rt-max-depth "$BAS_RT_MAX_DEPTH" \
        --out "$BAS_RT_CHANNEL_PATH" \
        --interval-ms 100 \
        "${gpu_args[@]}" \
        "${history_args[@]}" \
        > "${LOG_DIR}/sionna_rt_publisher.log" 2>&1 &
    echo $! > /tmp/bas_sionna_rt.pid
    echo "  pid=$(cat /tmp/bas_sionna_rt.pid) → ${BAS_RT_CHANNEL_PATH}"
    echo "  log: ${LOG_DIR}/sionna_rt_publisher.log"
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
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    echo "==> payload chain: Gazebo/video source -> bas-uav eth1 -> ns-3 payload -> bas-pload-far-net receiver -> Web GCS"
    echo "==> payload bypass: disabled"
    echo "==> require ns-3 payload: ${BAS_REQUIRE_NS3_PAYLOAD}"
elif [ "$BAS_PAYLOAD_BYPASS" = "1" ]; then
    echo "==> payload bypass: enabled (debug only; payload metrics invalid for network experiments)"
    echo "==> require ns-3 payload: ${BAS_REQUIRE_NS3_PAYLOAD}"
fi

"${REPO_ROOT}/.venv/bin/mavproxy.py" --help > "${LOG_DIR}/mavproxy_help.txt" 2>&1 || true
"${REPO_ROOT}/.venv/bin/mavproxy.py" --version > "${LOG_DIR}/mavproxy_version.txt" 2>&1 || true
if grep -q -- "--script" "${LOG_DIR}/mavproxy_help.txt"; then
    echo "Unexpected MAVProxy --script option found; review runner assumptions" >&2
    exit 2
fi

echo "[1/7] prepare control+payload bridges/TAPs"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

echo "[2/7] stop default compose stack"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    echo "[3/7] start uav-net + pload-far-net pause containers and inject veths"
    sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net pload-far-net" 2>&1 | tail -4
else
    echo "[3/7] start uav-net pause container and inject control veth"
    sg docker -c "docker compose -f ${COMPOSE_FILE} up -d uav-net" 2>&1 | tail -3
fi
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
setup_ns3_payload_netns

if [ "$BAS_GCS_MULTI_UAV" = "1" ]; then
    echo "[4/7] start Gazebo (multi-UAV world), SITL1+SITL2, mavrouter-multi"
elif [ "$BAS_GCS_QGC" = "1" ]; then
    echo "[4/7] start Gazebo, SITL, mavrouter (QGC bridge mode)"
else
    echo "[4/7] start Gazebo, SITL, mavbridge"
fi
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -3
echo "  waiting 6s for Gazebo FDM"
sleep 6
if [ "$BAS_GCS_MULTI_UAV" = "1" ]; then
    # Multi-UAV: 2 SITL экземпляра (-I0 и -I1) + единый mavp2p router
    # multiplexing оба TCP в один UDP 14550 для MAVProxy. UAV2 sysid=2
    # переопределяется через --sysid CLI ArduCopter. Gazebo iris_runway_multi.sdf
    # содержит обе iris модели на разных fdm_port (9002 и 9012).
    sg docker -c "docker compose -f ${COMPOSE_FILE} --profile multi up -d sitl sitl2 mavrouter-multi" 2>&1 | tail -5
elif [ "$BAS_GCS_QGC" = "1" ]; then
    # mavrouter (bluenviron/mavp2p) держит один TCP к SITL и serves UDP 14550
    # для MAVProxy GCS + UDP 14560 для QGC bridge. mavbridge не запускаем —
    # SITL TCP 5760 single-client, и mavp2p и socat конфликтуют.
    sg docker -c "docker compose -f ${COMPOSE_FILE} --profile qgc up -d sitl mavrouter" 2>&1 | tail -3
else
    sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl mavbridge" 2>&1 | tail -3
fi

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

# Debug-only bypass FPV. Network-realistic Stage 2.4 payload uses
# start_ns3_payload_pipeline() after ns-3 readiness below.
start_fpv_pipeline
# QGC host-side relay поднимаем здесь же — mavrouter уже стартовал в [4/7]
# когда BAS_GCS_QGC=1, нам осталось только пробросить UDP с хоста.
start_qgc_host_relay
# Sionna RT live publisher (если BAS_SIONNA_RT_ONLINE=1). Должен подняться
# ДО ns-3 чтобы initial seed JSON был на месте к моменту первого polling tick.
start_sionna_rt_publisher

echo "[6/7] start ns-3 control channel (baseline_wifi: 5ms delay, no loss)"
NS3_ARGS="--runId=${RUN_ID} --duration=${NS3_DURATION}"
NS3_ARGS="${NS3_ARGS} --ctrlDelayMs=5 --ctrlLoss=0.0"
NS3_ARGS="${NS3_ARGS} --ploadDelayMs=10 --ploadLoss=0.0"
if [ -n "$SIONNA_CONTAINER_PATH" ]; then
    NS3_ARGS="${NS3_ARGS} --sionnaChannelPath=${SIONNA_CONTAINER_PATH}"
    NS3_ARGS="${NS3_ARGS} --sionnaTargetFlow=${SIONNA_TARGET_FLOW}"
    echo "==> Sionna target flow: ${SIONNA_TARGET_FLOW}"
fi
if [ "$STAGE24_PACKET_AUDIT" = "1" ]; then
    NS3_ARGS="${NS3_ARGS} --packetAuditCsv=/work/logs/${RUN_ID}/$(basename "$STAGE24_PACKET_AUDIT_CSV")"
    NS3_ARGS="${NS3_ARGS} --packetAuditSeed=${STAGE24_PACKET_AUDIT_SEED}"
    echo "==> packet audit CSV: ${STAGE24_PACKET_AUDIT_CSV}"
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
start_ns3_payload_pipeline

ip netns exec bas-ctrl-far ip neigh flush all 2>/dev/null || true
ip netns exec bas-uav ip neigh flush all 2>/dev/null || true
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    ip netns exec bas-pload-far-pod ip neigh flush all 2>/dev/null || true
fi
NEIGH_NETNS=(bas-ctrl-far bas-uav)
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    NEIGH_NETNS+=(bas-pload-far-pod)
fi
for ns in "${NEIGH_NETNS[@]}"; do
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
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    sleep 3
    sg docker -c "docker logs bas-video-sender 2>&1"   > "${LOG_DIR}/video_sender.log" 2>&1 || true
    sg docker -c "docker logs bas-video-receiver 2>&1" > "${LOG_DIR}/video_receiver.log" 2>&1 || true
    TX_LINES=$(wc -l < "${LOG_DIR}/video_tx.jsonl" 2>/dev/null || echo 0)
    RX_RTP_LINES=$(grep -c '"event_type":"video_rx"' "${LOG_DIR}/video_rx.jsonl" 2>/dev/null || echo 0)
    RX_FRAME_LINES=$(grep -c '"event_type":"video_frame"' "${LOG_DIR}/video_rx.jsonl" 2>/dev/null || echo 0)
    PAYLOAD_AUDIT_ROWS=0
    if [ "$STAGE24_PACKET_AUDIT" = "1" ] && [ -f "$STAGE24_PACKET_AUDIT_CSV" ]; then
        PAYLOAD_AUDIT_ROWS=$(awk -F, 'NR>1 && $4=="payload"{n++} END{print n+0}' "$STAGE24_PACKET_AUDIT_CSV" 2>/dev/null || echo 0)
    fi
    cat > "${LOG_DIR}/payload_path_runtime.md" <<PAYLOAD_RUNTIME
# Stage 2.4 Payload Runtime Check

- Path: Gazebo/video source -> bas-uav eth1 -> tap-pload-near -> ns-3 payload channel -> tap-pload-far -> bas-pload-far-net receiver -> Web GCS
- Bypass disabled: true
- Require ns-3 payload: ${BAS_REQUIRE_NS3_PAYLOAD}
- Video source: ${BAS_VIDEO_SOURCE_RAW} (${BAS_VIDEO_SOURCE})
- Web GCS FPV upstream: ${BAS_FPV_UPSTREAM_HOST}:${BAS_FPV_UPSTREAM_PORT}
- video_tx log rows: ${TX_LINES}
- video_rx RTP packet rows: ${RX_RTP_LINES}
- decoded frame rows: ${RX_FRAME_LINES}
- ns-3 payload audit rows: ${PAYLOAD_AUDIT_ROWS}
PAYLOAD_RUNTIME
fi
ip netns exec bas-ctrl-far ip addr > "${LOG_DIR}/bas_ctrl_far_addr.txt" 2>&1 || true
ip netns exec bas-uav ip addr > "${LOG_DIR}/bas_uav_addr.txt" 2>&1 || true
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    ip netns exec bas-pload-far-pod ip addr > "${LOG_DIR}/bas_pload_far_addr.txt" 2>&1 || true
fi

echo
echo "Stage 2.4 MAVProxy GCS result:"
echo "  exit=${RC}"
echo "  logs=${LOG_DIR}"
echo "  report=${LOG_DIR}/report.md"
if [ "$BAS_NS3_PAYLOAD" = "1" ]; then
    echo "  payload_runtime=${LOG_DIR}/payload_path_runtime.md"
fi
exit "$RC"
