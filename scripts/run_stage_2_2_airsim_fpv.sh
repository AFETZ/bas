#!/usr/bin/env bash
# Stage 2.2 AirSim FPV — камера AirSim в окне FPV пульта (integration E2).
#
# Поднимает airsim_mjpeg_server (берёт кадры из Cosys-AirSim RPC, или кадр-
# заглушку если AirSim не запущен) + Web GCS пульт с FPV upstream на этот
# сервер. В браузере окно FPV (<img src="/camera.mjpg">) показывает камеру
# AirSim.
#
# Если Cosys-AirSim запущен на Windows-GPU (RPC :41451) — в окно идёт реальный
# рендер; иначе — кадр-заглушка со статусом NO SIGNAL (FPV-конвейер виден).
#
# Не требует sudo: пульт в demo-режиме + MJPEG-сервер, всё на localhost.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"

FPV_PORT="${BAS_AIRSIM_FPV_PORT:-8767}"
UI_PORT="${BAS_GCS_UI_PORT:-8765}"
AIRSIM_HOST="${BAS_AIRSIM_HOST:-127.0.0.1}"
AIRSIM_PORT="${BAS_AIRSIM_PORT:-41451}"
CAMERA="${BAS_AIRSIM_CAMERA:-front_center}"

MJPEG_PID=""
UI_PID=""
cleanup() {
    set +e
    [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null || true
    [ -n "$MJPEG_PID" ] && kill "$MJPEG_PID" 2>/dev/null || true
    set -e
}
trap cleanup EXIT INT TERM

echo "[airsim-fpv] airsim_mjpeg_server :${FPV_PORT} ← Cosys-AirSim ${AIRSIM_HOST}:${AIRSIM_PORT} cam=${CAMERA}"
"$VENV_PY" "${SCRIPT_DIR}/airsim_mjpeg_server.py" \
    --host 127.0.0.1 --port "$FPV_PORT" \
    --airsim-host "$AIRSIM_HOST" --airsim-port "$AIRSIM_PORT" \
    --camera "$CAMERA" --fps 10 &
MJPEG_PID=$!
sleep 1

echo "[airsim-fpv] Web GCS пульт :${UI_PORT} (FPV upstream ← 127.0.0.1:${FPV_PORT})"
BAS_FPV_UPSTREAM_HOST=127.0.0.1 BAS_FPV_UPSTREAM_PORT="$FPV_PORT" \
    "$VENV_PY" "${SCRIPT_DIR}/gcs_web_ui_server.py" \
    --demo --host 127.0.0.1 --port "$UI_PORT" &
UI_PID=$!

cat <<INFO

==========================================================================
 Stage 2.2 AirSim FPV — камера AirSim в окне пульта
--------------------------------------------------------------------------
 🕹  ПУЛЬТ: http://127.0.0.1:${UI_PORT}/   — окно FPV = камера AirSim
       (клавиша F — показать/скрыть FPV)

 Источник: airsim_mjpeg_server :${FPV_PORT} ← Cosys-AirSim ${AIRSIM_HOST}:${AIRSIM_PORT}
 AirSim не запущен → кадр-заглушка NO SIGNAL. Запусти Cosys-AirSim Blocks
 на Windows-GPU (sudo bash scripts/run_stage_2_2_airsim_overlay.sh) →
 в окне реальный рендер.
==========================================================================
INFO

wait "$UI_PID"
