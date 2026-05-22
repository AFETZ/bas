#!/usr/bin/env bash
# Stage 2.2 — Gazebo ↔ Cosys-AirSim overlay demo.
#
# Архитектура (per ТЗ Андрончева А.Д. + Федотенкова А.А.):
#   Gazebo + ArduPilot SITL = физика полёта
#   Cosys-AirSim (UE5)     = высокореалистичный визуал + сенсоры
#   bridge                  = forward Gazebo pose → AirSim setVehiclePose
#                            + pull AirSim camera/LiDAR → logs/
#
# По умолчанию запускает stub-режим: airsim_stub_server (msgpack-rpc на
# 41451) имитирует Cosys-AirSim для headless smoke без необходимости
# UE5 binary. Это позволяет проверить bridge работает end-to-end на CI.
#
# Реальный Cosys-AirSim (на Windows или другой Linux машине):
#   sudo env BAS_AIRSIM_STUB=0 BAS_AIRSIM_HOST=<windows-ip> \
#        bash scripts/run_stage_2_2_airsim_overlay.sh
#
# См. docs/stage_2_2_airsim_overlay.md для инструкций по установке
# Cosys-AirSim Linux binary / Windows Editor.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BAS_AIRSIM_HOST="${BAS_AIRSIM_HOST:-127.0.0.1}"
BAS_AIRSIM_PORT="${BAS_AIRSIM_PORT:-41451}"
BAS_AIRSIM_STUB="${BAS_AIRSIM_STUB:-1}"          # 1 = поднимать stub локально
BAS_AIRSIM_CAMERA="${BAS_AIRSIM_CAMERA:-front_center}"
BAS_AIRSIM_IMAGE_PERIOD_S="${BAS_AIRSIM_IMAGE_PERIOD_S:-2}"

# Base demo: используем тот же stack что fpv_rf_demo (gazebo физика +
# Web GCS) — AirSim добавляется поверх как дополнительный visual sink.
export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-0}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
export BAS_RUN_ID="${BAS_RUN_ID:-stage_2_2_airsim_overlay_$(date -u +%Y%m%dT%H%M%SZ)}"

LOG_DIR="${REPO_ROOT}/logs/${BAS_RUN_ID}"
mkdir -p "$LOG_DIR"

VENV_PY="${REPO_ROOT}/.venv/bin/python"
[ -x "$VENV_PY" ] || { echo "venv python not found: $VENV_PY" >&2; exit 1; }

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

STUB_PID=""
BRIDGE_PID=""
STACK_PID=""

cleanup() {
    set +e
    echo "[airsim-overlay] cleanup"
    [ -n "$BRIDGE_PID" ] && kill "$BRIDGE_PID" 2>/dev/null || true
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null || true
    if [ -n "$STACK_PID" ] && kill -0 "$STACK_PID" 2>/dev/null; then
        kill -INT "$STACK_PID" 2>/dev/null || true
        for _ in $(seq 1 30); do
            kill -0 "$STACK_PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$STACK_PID" 2>/dev/null || true
    fi
    set -e
}
trap cleanup EXIT INT TERM

echo "==> RUN_ID=${BAS_RUN_ID}"
echo "==> LOG_DIR=${LOG_DIR}"
echo "==> AirSim endpoint=${BAS_AIRSIM_HOST}:${BAS_AIRSIM_PORT}  stub=${BAS_AIRSIM_STUB}"

# ---- 1. Stub AirSim msgpack-rpc сервер (если включён) -------------------
if [ "$BAS_AIRSIM_STUB" = "1" ]; then
    if ss -tln 2>/dev/null | grep -q ":${BAS_AIRSIM_PORT}\b"; then
        echo "[airsim-overlay] port :${BAS_AIRSIM_PORT} already in use; assuming real AirSim is running"
    else
        echo "[airsim-overlay] starting stub AirSim on :${BAS_AIRSIM_PORT}"
        nohup "$VENV_PY" "${SCRIPT_DIR}/airsim_stub_server.py" \
            --host 0.0.0.0 --port "$BAS_AIRSIM_PORT" \
            --pose-log "${LOG_DIR}/airsim_stub_pose.jsonl" \
            > "${LOG_DIR}/airsim_stub.log" 2>&1 &
        STUB_PID=$!
        for _ in $(seq 1 15); do
            if ss -tln 2>/dev/null | grep -q ":${BAS_AIRSIM_PORT}\b"; then
                echo "  stub up on :${BAS_AIRSIM_PORT} (pid=${STUB_PID})"
                break
            fi
            sleep 0.3
        done
    fi
fi

# ---- 2. Запуск base SITL+Gazebo+Web GCS stack в background -------------
echo "[airsim-overlay] launch base stack (run_stage_2_4_fpv_rf_demo.sh)"
bash "${SCRIPT_DIR}/run_stage_2_4_fpv_rf_demo.sh" > "${LOG_DIR}/base_stack.log" 2>&1 &
STACK_PID=$!

echo "[airsim-overlay] waiting for Web GCS UI..."
for _ in $(seq 1 120); do
    if ss -tln 2>/dev/null | grep -q ":${BAS_GCS_UI_PORT}\b"; then
        echo "  UI :${BAS_GCS_UI_PORT} ready"
        break
    fi
    if ! kill -0 "$STACK_PID" 2>/dev/null; then
        echo "  base stack died before UI ready" >&2
        tail -40 "${LOG_DIR}/base_stack.log" >&2 || true
        exit 2
    fi
    sleep 1
done

# Wait для появления events.jsonl (orchestrator пишет flight events
# когда SITL + MAVProxy подключены).
EVENTS_PATH="${LOG_DIR}/events.jsonl"
for _ in $(seq 1 60); do
    [ -s "$EVENTS_PATH" ] && break
    sleep 1
done

# ---- 3. Bridge ------------------------------------------------------------
echo "[airsim-overlay] start bridge (events → AirSim setVehiclePose)"
"$VENV_PY" "${SCRIPT_DIR}/airsim_bridge.py" \
    --events "$EVENTS_PATH" \
    --log-dir "$LOG_DIR" \
    --airsim-host "$BAS_AIRSIM_HOST" \
    --airsim-port "$BAS_AIRSIM_PORT" \
    --camera-name "$BAS_AIRSIM_CAMERA" \
    --image-period-s "$BAS_AIRSIM_IMAGE_PERIOD_S" \
    2>&1 | tee "${LOG_DIR}/airsim_bridge.log" &
BRIDGE_PID=$!

# Ждём окончания base stack (UI блокирует) либо SIGINT.
wait "$STACK_PID"
STACK_RC=$?

echo
echo "==> Stage 2.2 AirSim overlay finished (base stack rc=${STACK_RC})"
echo "==> LOG_DIR=${LOG_DIR}"
echo "==>   * bridge log: airsim_bridge.log"
echo "==>   * pose forward: airsim_pose_forward.jsonl"
[ "$BAS_AIRSIM_STUB" = "1" ] && \
    echo "==>   * stub pose log: airsim_stub_pose.jsonl"
echo "==>   * camera frames: airsim_camera/ (empty в stub mode)"

exit "$STACK_RC"
