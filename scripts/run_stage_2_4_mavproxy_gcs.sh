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

cleanup() {
    set +e
    echo "[cleanup]"
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage24 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    rm -f /var/run/netns/bas-uav
    set -e
}

case "$MODE" in
    smoke|interactive|ui|dry-run) ;;
    *)
        echo "Unknown mode: ${MODE}. Use smoke, interactive, or dry-run." >&2
        exit 2
        ;;
esac

mkdir -p "$LOG_DIR"

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
UAV_PID=$(sg docker -c "docker inspect --format '{{.State.Pid}}' bas-uav-net")
mkdir -p /var/run/netns
ln -sf "/proc/${UAV_PID}/ns/net" /var/run/netns/bas-uav

ip link del veth-uav-br >/dev/null 2>&1 || true
ip link del veth-uav >/dev/null 2>&1 || true
ip link add veth-uav type veth peer name veth-uav-br
ip link set veth-uav-br master br-ctrl-near
ip link set veth-uav-br up
ip link set veth-uav netns bas-uav
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

echo "[6/7] start ns-3 control channel (baseline_wifi: 5ms delay, no loss)"
NS3_ARGS="--runId=${RUN_ID} --duration=${NS3_DURATION}"
NS3_ARGS="${NS3_ARGS} --ctrlDelayMs=5 --ctrlLoss=0.0"
NS3_ARGS="${NS3_ARGS} --ploadDelayMs=10 --ploadLoss=0.0"

sg docker -c "docker rm -f bas-ns3-stage24 2>/dev/null" >/dev/null 2>&1 || true
sg docker -c "docker run -d --name bas-ns3-stage24 --network host --cap-add NET_ADMIN --privileged \
    -e NS3_ARGS='${NS3_ARGS}' \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
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
        --port "$UI_PORT"
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
