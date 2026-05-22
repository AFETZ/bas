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
# Three modes:
#   stub  — наш scripts/airsim_stub_server.py (msgpack-rpc API only; для CI)
#   linux — real Cosys-AirSim Linux packaged build (UE5.5); auto-download
#           + headless run в WSL2 (nullrhi, no GPU rendering — pose API ok,
#           image API возвращает empty bytes)
#   off   — не поднимать ничего; bridge подключается к external AirSim
#           (host=BAS_AIRSIM_HOST), типично Windows-side Cosys-AirSim Editor
BAS_AIRSIM_MODE="${BAS_AIRSIM_MODE:-stub}"
# UE5 binary refuses to run as root. Install в HOME реального user'а
# (через SUDO_USER), и запускаем Blocks через `sudo -u $SUDO_USER`.
AIRSIM_RUN_USER="${SUDO_USER:-${USER:-afetz}}"
AIRSIM_RUN_HOME="$(getent passwd "$AIRSIM_RUN_USER" 2>/dev/null | cut -d: -f6)"
[ -z "$AIRSIM_RUN_HOME" ] && AIRSIM_RUN_HOME="/home/${AIRSIM_RUN_USER}"
BAS_AIRSIM_INSTALL_DIR="${BAS_AIRSIM_INSTALL_DIR:-${AIRSIM_RUN_HOME}/cosys-airsim}"
BAS_AIRSIM_BLOCKS_URL="${BAS_AIRSIM_BLOCKS_URL:-https://github.com/Cosys-Lab/Cosys-AirSim/releases/download/5.5-v3.3/Blocks_packaged_Linux_55_33.zip}"
BAS_AIRSIM_CAMERA="${BAS_AIRSIM_CAMERA:-front_center_cam}"
BAS_AIRSIM_IMAGE_PERIOD_S="${BAS_AIRSIM_IMAGE_PERIOD_S:-2}"

# Back-compat: BAS_AIRSIM_STUB=0 → выключить stub.
if [ "${BAS_AIRSIM_STUB:-}" = "0" ]; then
    BAS_AIRSIM_MODE="off"
fi
if [ "${BAS_AIRSIM_STUB:-}" = "1" ]; then
    BAS_AIRSIM_MODE="stub"
fi

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
BLOCKS_PID=""

cleanup() {
    set +e
    echo "[airsim-overlay] cleanup"
    [ -n "$BRIDGE_PID" ] && kill "$BRIDGE_PID" 2>/dev/null || true
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null || true
    [ -n "$BLOCKS_PID" ] && kill "$BLOCKS_PID" 2>/dev/null || true
    # Дополнительно: если Blocks форкнул child UE5 процессы — снести их.
    pkill -f "Blocks_packaged_Linux_55_33" 2>/dev/null || true
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
echo "==> AirSim endpoint=${BAS_AIRSIM_HOST}:${BAS_AIRSIM_PORT}  mode=${BAS_AIRSIM_MODE}"

# ---- Helper: install + run REAL Cosys-AirSim Linux packaged build -------
ensure_airsim_settings() {
    local target_user_dir="$AIRSIM_RUN_HOME"
    mkdir -p "${target_user_dir}/Documents/AirSim"
    chown -R "${AIRSIM_RUN_USER}:${AIRSIM_RUN_USER}" "${target_user_dir}/Documents/AirSim" 2>/dev/null || true
    if [ ! -f "${target_user_dir}/Documents/AirSim/settings.json" ]; then
        cat > "${target_user_dir}/Documents/AirSim/settings.json" <<EOF
{
  "SettingsVersion": 2.0,
  "SimMode": "Multirotor",
  "ClockType": "SteppableClock",
  "ViewMode": "SpringArmChase",
  "ApiServerEndpoint": "0.0.0.0:${BAS_AIRSIM_PORT}",
  "Vehicles": {
    "Copter": {
      "VehicleType": "SimpleFlight",
      "AutoCreate": true
    }
  }
}
EOF
        chown "${AIRSIM_RUN_USER}:${AIRSIM_RUN_USER}" "${target_user_dir}/Documents/AirSim/settings.json" 2>/dev/null || true
        echo "[airsim] wrote default settings.json to ${target_user_dir}/Documents/AirSim/"
    fi
}

install_linux_blocks() {
    local install_dir="$BAS_AIRSIM_INSTALL_DIR"
    local blocks_sh="${install_dir}/Blocks_packaged_Linux_55_33/Linux/Blocks.sh"
    if [ -x "$blocks_sh" ]; then
        echo "[airsim] Cosys-AirSim Linux build already installed at ${install_dir}"
        return 0
    fi
    echo "[airsim] downloading Cosys-AirSim Linux build (~637 MB) to ${install_dir}"
    mkdir -p "$install_dir"
    if ! curl -fsSL -o "${install_dir}/Blocks_packaged_Linux_55_33.zip" \
            "$BAS_AIRSIM_BLOCKS_URL"; then
        echo "  download failed; falling back to stub mode" >&2
        return 1
    fi
    echo "[airsim] unzipping"
    (cd "$install_dir" && unzip -q -o Blocks_packaged_Linux_55_33.zip)
    chmod +x "$blocks_sh" || true
    chmod +x "${install_dir}/Blocks_packaged_Linux_55_33/Linux/Blocks" || true
    chown -R "${AIRSIM_RUN_USER}:${AIRSIM_RUN_USER}" "$install_dir" 2>/dev/null || true
    echo "  installed at ${blocks_sh}"
}

start_linux_blocks() {
    local blocks_sh="${BAS_AIRSIM_INSTALL_DIR}/Blocks_packaged_Linux_55_33/Linux/Blocks.sh"
    if [ ! -x "$blocks_sh" ]; then
        echo "[airsim] Blocks.sh missing at $blocks_sh; aborting linux mode" >&2
        return 1
    fi
    ensure_airsim_settings
    # UE5 binary защищается от запуска под root. Используем sudo -u чтобы
    # запустить как реального пользователя.
    echo "[airsim] starting Cosys-AirSim Blocks as user=${AIRSIM_RUN_USER} (headless, nullrhi)"
    cd "$(dirname "$blocks_sh")"
    if [ "$EUID" -eq 0 ] && [ "$AIRSIM_RUN_USER" != "root" ]; then
        sudo -u "$AIRSIM_RUN_USER" -H \
            nohup "$blocks_sh" -RenderOffscreen -nullrhi -nosound -nosplash \
            > "${LOG_DIR}/airsim_blocks.log" 2>&1 &
    else
        nohup "$blocks_sh" -RenderOffscreen -nullrhi -nosound -nosplash \
            > "${LOG_DIR}/airsim_blocks.log" 2>&1 &
    fi
    BLOCKS_PID=$!
    echo "  pid=${BLOCKS_PID}, log=${LOG_DIR}/airsim_blocks.log"
    # Wait for API port (до 90 с — UE5 init time на CPU вирtual GPU
    # ощутимый при первом запуске).
    for _ in $(seq 1 45); do
        if ss -tln 2>/dev/null | grep -q ":${BAS_AIRSIM_PORT}\b"; then
            echo "  AirSim API ready on :${BAS_AIRSIM_PORT}"
            return 0
        fi
        sleep 2
    done
    echo "  AirSim API did not open on :${BAS_AIRSIM_PORT} within 90s" >&2
    tail -30 "${LOG_DIR}/airsim_blocks.log" >&2 || true
    return 1
}

# ---- 1. AirSim startup according to mode ---------------------------------
if [ "$BAS_AIRSIM_MODE" = "stub" ]; then
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
elif [ "$BAS_AIRSIM_MODE" = "linux" ]; then
    install_linux_blocks
    start_linux_blocks
elif [ "$BAS_AIRSIM_MODE" = "off" ]; then
    echo "[airsim-overlay] BAS_AIRSIM_MODE=off — assuming external AirSim at ${BAS_AIRSIM_HOST}:${BAS_AIRSIM_PORT}"
else
    echo "[airsim-overlay] WARN: unknown BAS_AIRSIM_MODE=${BAS_AIRSIM_MODE}; assuming external" >&2
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
