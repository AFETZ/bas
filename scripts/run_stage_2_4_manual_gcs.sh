#!/usr/bin/env bash
# Diagnostic-only legacy mini-GCS runner.
#
# This file is kept as a direct pymavlink probe/fallback. It is not the
# Stage 2.4 acceptance path because it bypasses MAVProxy/GCS.
# Use scripts/run_stage_2_4_mavproxy_gcs.sh for Stage 2.4 acceptance.
#
# Historical diagnostic architecture:
#
#   host: bash scripts/run_stage_2_4_manual_gcs.sh
#       │
#       ├── full stack:
#       │     setup_radio_net.sh up + uav-net + gazebo + sitl + mavbridge
#       │     + ns-3 control канал (two_channel.cc, profile baseline_wifi)
#       │
#       └── pymavlink probe в bas-ctrl-far netns:
#             scripts/manual_gcs_demo.py --endpoint udpout:10.10.0.2:14550
#
# The probe sends live GUIDED commands and never uploads a mission, but it is
# direct pymavlink. It must not be used as the Stage 2.4 GCS acceptance proof.
#
# Diagnostic usage only:
#   sudo bash scripts/run_stage_2_4_manual_gcs.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${BAS_RUN_ID:-stage_2_4_manual_gcs_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.shared-netns.yml"
DEFAULT_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"
GCS_SCRIPT="${BAS_GCS_SCRIPT:-${REPO_ROOT}/scripts/manual_gcs_demo.py}"
NS3_DURATION="${NS3_DURATION:-600}"
NS3_START_TIMEOUT_SECONDS="${NS3_START_TIMEOUT_SECONDS:-300}"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }

ensure_docker() {
    if docker info >/dev/null 2>&1; then return 0; fi
    service docker start >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    echo "Docker daemon не поднялся" >&2; return 1
}

cleanup() {
    set +e
    echo "[cleanup]"
    timeout 30 sg docker -c "docker rm -f bas-ns3-stage24 2>/dev/null" >/dev/null 2>&1
    timeout 60 sg docker -c "docker compose -f ${COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1
    rm -f /var/run/netns/bas-uav
    set -e
}
trap cleanup EXIT INT TERM

ensure_root
ensure_docker
mkdir -p "$LOG_DIR"
[ -f "$GCS_SCRIPT" ] || { echo "GCS script не найден: $GCS_SCRIPT" >&2; exit 1; }
[ -x "${REPO_ROOT}/.venv/bin/python" ] || {
    echo ".venv/bin/python не найден" >&2; exit 1;
}

echo "==> run_id=${RUN_ID}"
echo "==> логи: ${LOG_DIR}"
echo "==> diagnostic probe script: ${GCS_SCRIPT}"
echo "==> direct pymavlink probe → bas-ctrl-far netns → ns-3 control → mavbridge → SITL"
echo "==> NOT Stage 2.4 acceptance; use scripts/run_stage_2_4_mavproxy_gcs.sh"

echo "[1/7] подготовка control bridges/TAPs"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

echo "[2/7] тушим default compose"
sg docker -c "docker compose -f ${DEFAULT_COMPOSE_FILE} down -v 2>/dev/null" >/dev/null 2>&1 || true

echo "[3/7] pause-контейнер uav-net + control veth"
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

echo "[4/7] gazebo + sitl + mavbridge"
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d gazebo" 2>&1 | tail -3
echo "  ждём 6с пока Gazebo откроет FDM"
sleep 6
sg docker -c "docker compose -f ${COMPOSE_FILE} up -d sitl mavbridge" 2>&1 | tail -3

echo "[5/7] ждём SITL MAVLink на :5760 + settle"
for i in $(seq 1 60); do
    if ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
ip netns exec bas-uav ss -tln 2>/dev/null | grep -q ":5760" || {
    echo "SITL 5760 не открылся" >&2; sg docker -c "docker logs --tail 20 bas-sitl 2>&1"; exit 2
}
sleep 10

echo "[6/7] запуск ns-3 control канал (baseline_wifi: 5ms delay, no loss)"
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
for i in $(seq 1 $((NS3_START_TIMEOUT_SECONDS / 2))); do
    [ -s "$NS3_LOG" ] && break
    if ! sg docker -c "docker inspect -f '{{.State.Running}}' bas-ns3-stage24 2>/dev/null" | grep -q true; then
        echo "ns-3 контейнер завершился до старта" >&2
        sg docker -c "docker logs --tail 80 bas-ns3-stage24 2>&1" >&2 || true
        exit 3
    fi
    sleep 2
done
[ -s "$NS3_LOG" ] || {
    echo "ns-3 не стартовал за ${NS3_START_TIMEOUT_SECONDS}s" >&2
    sg docker -c "docker exec bas-ns3-stage24 tail -80 /tmp/build.log 2>&1" >&2 || true
    sg docker -c "docker logs --tail 80 bas-ns3-stage24 2>&1" >&2 || true
    exit 3
}
echo "  ns-3 control канал готов"
sleep 5

# ARP-hygiene.
ip netns exec bas-ctrl-far ip neigh flush all 2>/dev/null || true
ip netns exec bas-uav        ip neigh flush all 2>/dev/null || true
for ns in bas-ctrl-far bas-uav; do
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.mcast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.ucast_solicit=5 >/dev/null 2>&1 || true
    ip netns exec "$ns" sysctl -w net.ipv4.neigh.default.retrans_time_ms=2000 >/dev/null 2>&1 || true
done

echo "[7/7] запуск Python mini-GCS в bas-ctrl-far netns"
echo "  script: ${GCS_SCRIPT}"
echo "  endpoint: udpout:10.10.0.2:14550 (через ns-3 control)"
echo "  source-system: 255"
echo "  DIAGNOSTIC ONLY: live GUIDED commands, no mission upload, but direct pymavlink"
echo

# Lightweight pymavlink probe in bas-ctrl-far netns. This intentionally remains
# outside Stage 2.4 acceptance because it bypasses MAVProxy/GCS.
set +e
ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/python" \
    "${GCS_SCRIPT}" \
    --endpoint udpout:10.10.0.2:14550 \
    --takeoff-alt 30 \
    --square-side 50 \
    2>&1 | tee "${LOG_DIR}/gcs_stdout.log"
RC=${PIPESTATUS[0]}
set -e

# Контейнерные логи.
sg docker -c "docker logs bas-sitl 2>&1"     > "${LOG_DIR}/sitl.log" 2>&1 || true
sg docker -c "docker logs bas-gazebo 2>&1"   > "${LOG_DIR}/gazebo.log" 2>&1 || true
sg docker -c "docker logs bas-mavbridge 2>&1" > "${LOG_DIR}/mavbridge.log" 2>&1 || true
sg docker -c "docker logs bas-ns3-stage24 2>&1" > "${LOG_DIR}/ns3_stdout.log" 2>&1 || true

# Sanity-check: были ли отправлены команды и пришла ли disarm.
# В mavproxy stdout есть строки вида "AP: ARMING MOTORS", "AP: LANDED".
ARMED=$(grep -c -E "Got ARMED|MOTORS\s*ARMED" "${LOG_DIR}/sitl.log" 2>/dev/null || echo 0)
LANDED=$(grep -c -E "Landed|LAND_COMPLETE|Disarming motors" "${LOG_DIR}/sitl.log" 2>/dev/null || echo 0)
MODES=$(grep -cE "Mode.*GUIDED|Mode.*LAND" "${LOG_DIR}/sitl.log" 2>/dev/null || echo 0)

# Также сложить summary в report.md.
cat > "${LOG_DIR}/report.md" <<EOF
# Diagnostic pymavlink mini-GCS probe report

- **run_id:** \`${RUN_ID}\`
- **probe script:** \`${GCS_SCRIPT}\`
- **endpoint:** \`udpout:10.10.0.2:14550\` (через ns-3 control канал, baseline_wifi профиль)
- **GCS acceptance path:** false
- **Direct pymavlink command path used:** true
- **Mission upload used:** false

## Scope

This is a diagnostic probe only. Stage 2.4 acceptance must use:
\`scripts/run_stage_2_4_mavproxy_gcs.sh\`, where MAVProxy command-line GCS is
the only sender of manual flight commands.

## Diagnostic command pipeline

1. \`mode GUIDED\`
2. \`arm\`
3. \`takeoff\`
4. \`SET_POSITION_TARGET_LOCAL_NED\`
5. \`LAND\`

## Результат прогона

- exit code: ${RC}
- SITL armed events: ${ARMED}
- SITL mode changes (GUIDED/LAND): ${MODES}
- SITL landed/disarm events: ${LANDED}

EOF

echo
echo "Stage 2.4 sanity:"
echo "  exit=${RC} armed=${ARMED} mode_changes=${MODES} landed=${LANDED}"
echo "  логи: ${LOG_DIR}"
echo "  report: ${LOG_DIR}/report.md"

echo
echo "Прогон завершён (RC=${RC})"
exit ${RC}
