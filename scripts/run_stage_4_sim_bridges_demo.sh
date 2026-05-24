#!/usr/bin/env bash
# Stage 4 — Sim bridges demo (ArduPilot ↔ AirSim + MAVLink ↔ Gazebo/AirSim).
#
# End-to-end демонстрация двух interface contracts из зоны Федотенкова А.А.:
#
#   1. ArduPilot ↔ AirSim — JSON-FDM bridge + MAVLink mirror bridge.
#      Канонический ArduPilot pattern с physics callback через UDP 9002/9003,
#      плюс облегчённый MAVLink-based pose mirror (`simSetVehiclePose`).
#
#   2. MAVLink ↔ Gazebo / AirSim — UDP fanout router.
#      Один MAVLink source → N sinks: GCS (MAVProxy), Gazebo plugin,
#      AirSim adapter, log file. Прозрачный (не парсит payload),
#      поддерживает v1 и v2.
#
# Сценарий demo:
#
#   ┌──────────────────────────────────────────────────────────────┐
#   │ ArduPilot SITL (опц.)         ┐                              │
#   │   udpin:14550                 │                              │
#   │                                ▼                             │
#   │                       mavlink_sim_router                     │
#   │                       fanout 1→3                             │
#   │                         ┌──────┼──────┬──────────┐           │
#   │                         ▼      ▼      ▼          ▼           │
#   │                       :14551 :14552 :14553   router.mav      │
#   │                       MAVProxy Gazebo  AirSim-adapter         │
#   │                                                               │
#   │   AirSim stub server (msgpack-rpc :41451)                    │
#   │                ▲                                              │
#   │                │ simSetVehiclePose calls                      │
#   │                                                               │
#   │   arducopter_airsim_interface --mode=mavlink_mirror          │
#   │     subscribe :14553, forward pose → AirSim                  │
#   └──────────────────────────────────────────────────────────────┘
#
# Default mode = "smoke" (без SITL — два smoke теста), полный demo с
# SITL требует ArduCopter binary установлен. Запуск:
#
#   bash scripts/run_stage_4_sim_bridges_demo.sh [smoke|router|mirror|full]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="${1:-smoke}"
BAS_RUN_ID="${BAS_RUN_ID:-stage_4_sim_bridges_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${BAS_RUN_ID}"
mkdir -p "$LOG_DIR"

VENV="${REPO_ROOT}/.venv/bin/python"
[ -x "$VENV" ] || { echo "venv missing at $VENV"; exit 1; }

PIDS=()
cleanup() {
    echo "[stage-4] cleanup"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> RUN_ID=${BAS_RUN_ID}"
echo "==> Mode: ${MODE}"
echo "==> Logs: ${LOG_DIR}"

case "$MODE" in
    smoke)
        echo
        echo "==> [1/2] MAVLink sim router smoke"
        "$VENV" "${SCRIPT_DIR}/_mavlink_sim_router_smoke.py" \
            | tee "${LOG_DIR}/router_smoke.log"
        echo
        echo "==> [2/2] ArduPilot ↔ AirSim JSON-FDM smoke"
        "$VENV" "${SCRIPT_DIR}/_arducopter_airsim_smoke.py" \
            | tee "${LOG_DIR}/arducopter_airsim_smoke.log"
        echo
        echo "==> Smoke artifacts:"
        ls -la "${LOG_DIR}/"
        ;;

    router)
        echo
        echo "==> Запускаем router в standalone mode на 30с"
        echo "    Source: udpin:127.0.0.1:14550  (ожидает MAVLink от SITL)"
        echo "    Sinks:  14551 (GCS), 14552 (Gazebo), 14553 (AirSim), file"
        "$VENV" "${SCRIPT_DIR}/mavlink_sim_router.py" \
            --source udpin:127.0.0.1:14550 \
            --sink udpout:127.0.0.1:14551 \
            --sink udpout:127.0.0.1:14552 \
            --sink udpout:127.0.0.1:14553 \
            --sink "file:${LOG_DIR}/router_capture.mav" \
            --max-seconds 30 \
            --stats-every-s 5 \
            | tee "${LOG_DIR}/router.log"
        ;;

    mirror)
        echo
        echo "==> Запускаем AirSim stub + MAVLink mirror bridge на 30с"
        "$VENV" "${SCRIPT_DIR}/airsim_stub_server.py" \
            --port 41451 \
            --pose-log "${LOG_DIR}/airsim_pose.jsonl" \
            > "${LOG_DIR}/airsim_stub.log" 2>&1 &
        PIDS+=($!)
        echo "  airsim_stub pid=${PIDS[-1]}"
        sleep 1
        "$VENV" "${SCRIPT_DIR}/arducopter_airsim_interface.py" \
            --mode=mavlink_mirror \
            --mavlink-endpoint=udpin:127.0.0.1:14553 \
            --airsim-host=127.0.0.1 --airsim-port=41451 \
            --log-file "${LOG_DIR}/mirror_bridge.jsonl" \
            --max-seconds 30 \
            | tee "${LOG_DIR}/mirror_bridge.log"
        ;;

    full)
        echo "==> Full demo: SITL + router + bridges + AirSim stub"
        # 1. AirSim stub
        "$VENV" "${SCRIPT_DIR}/airsim_stub_server.py" \
            --port 41451 \
            --pose-log "${LOG_DIR}/airsim_pose.jsonl" \
            > "${LOG_DIR}/airsim_stub.log" 2>&1 &
        PIDS+=($!)
        echo "  airsim_stub pid=${PIDS[-1]}"

        # 2. MAVLink fanout router
        sleep 1
        "$VENV" "${SCRIPT_DIR}/mavlink_sim_router.py" \
            --source udpin:127.0.0.1:14550 \
            --sink udpout:127.0.0.1:14551 \
            --sink udpout:127.0.0.1:14552 \
            --sink udpout:127.0.0.1:14553 \
            --sink "file:${LOG_DIR}/router_capture.mav" \
            --max-seconds 45 \
            > "${LOG_DIR}/router.log" 2>&1 &
        PIDS+=($!)
        echo "  router pid=${PIDS[-1]}"

        # 3. MAVLink mirror bridge → AirSim
        sleep 1
        "$VENV" "${SCRIPT_DIR}/arducopter_airsim_interface.py" \
            --mode=mavlink_mirror \
            --mavlink-endpoint=udpin:127.0.0.1:14553 \
            --airsim-host=127.0.0.1 --airsim-port=41451 \
            --log-file "${LOG_DIR}/mirror_bridge.jsonl" \
            --max-seconds 45 \
            > "${LOG_DIR}/mirror_bridge.log" 2>&1 &
        PIDS+=($!)
        echo "  mirror pid=${PIDS[-1]}"

        # 4. ArduPilot SITL (опционально, если найден)
        SIM_VEHICLE="${SIM_VEHICLE:-${HOME}/ardupilot/Tools/autotest/sim_vehicle.py}"
        if [ -x "$SIM_VEHICLE" ]; then
            echo "  starting ArduPilot SITL via $SIM_VEHICLE"
            "$SIM_VEHICLE" -v ArduCopter --no-mavproxy --out=udp:127.0.0.1:14550 \
                > "${LOG_DIR}/sitl.log" 2>&1 &
            PIDS+=($!)
            echo "  sitl pid=${PIDS[-1]}"
        else
            echo "  (no sim_vehicle.py at $SIM_VEHICLE — skipping SITL, demo проверяет"
            echo "   только router + bridge без real traffic; всё равно артефакты пишутся)"
        fi

        echo
        echo "==> Running 45с..."
        sleep 47

        echo
        echo "==> Final stats:"
        echo "--- router.log (tail) ---"
        tail -10 "${LOG_DIR}/router.log" 2>&1 || true
        echo "--- mirror_bridge.log (tail) ---"
        tail -10 "${LOG_DIR}/mirror_bridge.log" 2>&1 || true
        echo "--- mirror_bridge.jsonl line count ---"
        wc -l "${LOG_DIR}/mirror_bridge.jsonl" 2>/dev/null || echo "0 (no AirSim activity — нет SITL)"
        echo "--- airsim_pose.jsonl line count ---"
        wc -l "${LOG_DIR}/airsim_pose.jsonl" 2>/dev/null || echo "0"
        ;;

    *)
        echo "Unknown mode: $MODE"
        echo "Usage: $0 [smoke|router|mirror|full]"
        exit 2
        ;;
esac

echo
echo "==> done ($MODE). Logs at ${LOG_DIR}"
