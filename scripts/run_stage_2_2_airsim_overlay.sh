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
BAS_AIRSIM_BLOCKS_WIN_URL="${BAS_AIRSIM_BLOCKS_WIN_URL:-https://github.com/Cosys-Lab/Cosys-AirSim/releases/download/5.5-v3.3/Blocks_packaged_Windows_55_33.zip}"
BAS_AIRSIM_WIN_INSTALL_DIR="${BAS_AIRSIM_WIN_INSTALL_DIR:-/mnt/c/Users/${AIRSIM_RUN_USER}/cosys-airsim}"
BAS_AIRSIM_CAMERA="${BAS_AIRSIM_CAMERA:-front_center}"
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
    # Windows Blocks.exe — снести через taskkill (только если windows mode).
    if [ "${BAS_AIRSIM_MODE:-}" = "windows" ]; then
        cmd.exe /c "taskkill /F /IM Blocks.exe /T" 2>/dev/null || true
    fi
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
    echo "[airsim] note: -nullrhi обязателен в WSL2 — UE5.5 SM6 требует Vulkan 1.3+"
    echo "[airsim]       mesh_shader, недоступный через DZN/lavapipe. Для РЕАЛЬНОГО"
    echo "[airsim]       GPU-рендера используйте BAS_AIRSIM_MODE=windows (тот же RTX)."
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

# ---- Windows mode: real GPU rendering via WSL interop -------------------
# Почему Linux-mode не даёт GPU-картинку (проверено эмпирически на этой
# машине, RTX 5070 Ti, драйвер 595, UE5.5 Blocks):
#   UE5.5 RHI требует профиль VP_UE_Vulkan_SM6 — Vulkan 1.3 + VK_EXT_mesh_shader
#   + shader_image_atomic_int64 + maintenance4 + maxBoundDescriptorSets>=9.
#   В WSL2 доступны только два Vulkan-провайдера, и оба профиль НЕ проходят:
#     * DZN (Mesa Dozen, Vulkan-over-D3D12, GPU) — Vulkan 1.1, нет mesh_shader;
#     * lavapipe (программный, CPU) — "None of the 1 devices meet all criteria".
#   NVIDIA не поставляет native Vulkan ICD для WSL2 (в /usr/lib/wsl/lib только
#   CUDA/OptiX/D3D12, нет vulkan-producer). UE5 печатает "Vulkan Driver is
#   required to run the engine" и выходит с кодом 1.
# Вывод: реальный GPU-рендер этого бинаря возможен только там, где у RTX
# полный Vulkan 1.3/SM6 стек — на Windows-хосте. Поэтому для GPU-картинки
# запускаем Windows binary через cmd.exe, bridge подключается на Windows host
# через Hyper-V vEthernet gateway (172.x.x.1 → 0.0.0.0:41451 на Windows).
# Требует ОДНОРАЗОВУЮ настройку firewall (см. docs).
windows_host_ip() {
    # Default route gateway = Windows host vEthernet IP в WSL2 NAT mode.
    ip route show default 2>/dev/null | awk '/default/ {print $3; exit}'
}

install_windows_blocks() {
    local install_dir="$BAS_AIRSIM_WIN_INSTALL_DIR"
    local blocks_exe_win
    blocks_exe_win="${install_dir}/Blocks_packaged_Windows_55_33/Windows/Blocks.exe"
    if [ -f "$blocks_exe_win" ]; then
        echo "[airsim] Cosys-AirSim Windows build already installed"
        return 0
    fi
    echo "[airsim] downloading Cosys-AirSim Windows build (~556 MB) to ${install_dir}"
    mkdir -p "$install_dir"
    if ! curl -fsSL -o "${install_dir}/Blocks_packaged_Windows_55_33.zip" \
            "$BAS_AIRSIM_BLOCKS_WIN_URL"; then
        echo "  download failed; falling back to stub" >&2
        return 1
    fi
    echo "[airsim] unzipping via PowerShell"
    local win_zip_path
    win_zip_path="$(wslpath -w "${install_dir}/Blocks_packaged_Windows_55_33.zip")"
    local win_dest_path
    win_dest_path="$(wslpath -w "${install_dir}")"
    powershell.exe -Command "Expand-Archive -Path '${win_zip_path}' -DestinationPath '${win_dest_path}' -Force" \
        2>&1 | tail -3
    if [ -f "$blocks_exe_win" ]; then
        echo "  installed: ${blocks_exe_win}"
    else
        echo "  Blocks.exe not found after unzip" >&2
        return 1
    fi
}

ensure_windows_airsim_settings() {
    local win_user="${AIRSIM_RUN_USER}"
    local win_docs="/mnt/c/Users/${win_user}/Documents/AirSim"
    mkdir -p "$win_docs"
    if [ ! -f "${win_docs}/settings.json" ]; then
        # ВАЖНО: камера задаётся в блоке "Cameras" (НЕ "Sensors") — иначе
        # simGetImages не находит камеру и кадр пустой. Verified: с этим
        # блоком Windows-GPU Blocks отдаёт реальный PNG 256×144 (RTX 5070 Ti).
        cat > "${win_docs}/settings.json" <<EOF
{
  "SettingsVersion": 2.0,
  "SimMode": "Multirotor",
  "ClockType": "SteppableClock",
  "ViewMode": "NoDisplay",
  "ApiServerEndpoint": "0.0.0.0:${BAS_AIRSIM_PORT}",
  "Vehicles": {
    "Copter": {
      "VehicleType": "SimpleFlight",
      "AutoCreate": true,
      "Cameras": {
        "front_center": {
          "CaptureSettings": [
            { "ImageType": 0, "Width": 256, "Height": 144, "FOV_Degrees": 90 }
          ],
          "X": 0.5, "Y": 0.0, "Z": 0.1, "Pitch": 0.0, "Roll": 0.0, "Yaw": 0.0
        }
      }
    }
  }
}
EOF
        echo "[airsim] wrote Windows settings.json to ${win_docs} (camera front_center)"
    fi
}

start_windows_blocks() {
    local install_dir="$BAS_AIRSIM_WIN_INSTALL_DIR"
    local blocks_exe_win
    blocks_exe_win="${install_dir}/Blocks_packaged_Windows_55_33/Windows/Blocks.exe"
    if [ ! -f "$blocks_exe_win" ]; then
        echo "[airsim] Blocks.exe missing at $blocks_exe_win" >&2
        return 1
    fi
    ensure_windows_airsim_settings

    local win_blocks_path
    win_blocks_path="$(wslpath -w "$blocks_exe_win")"
    echo "[airsim] starting Windows Blocks.exe (real GPU)"
    # `cmd.exe /c start /B` запускает .exe detached, без console window.
    cmd.exe /c start /B "" "$win_blocks_path" -RenderOffscreen -ResX=640 -ResY=480 \
        > "${LOG_DIR}/airsim_blocks_win.log" 2>&1 || true

    # Wait для Blocks процесса (PowerShell сам найдёт).
    local found_pid=""
    for _ in $(seq 1 30); do
        found_pid="$(powershell.exe -Command 'Get-Process Blocks -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Id' 2>/dev/null | tr -d '\r' | head -1)"
        if [ -n "$found_pid" ]; then
            echo "  Blocks.exe started: Win-PID=${found_pid}"
            break
        fi
        sleep 2
    done

    # Windows host IP для AirSim API access.
    local win_ip
    win_ip="$(windows_host_ip)"
    if [ -n "$win_ip" ] && [ "$win_ip" != "0.0.0.0" ]; then
        echo "  Windows host IP: ${win_ip}"
        export BAS_AIRSIM_HOST="$win_ip"
    fi

    # Probe API endpoint.
    for _ in $(seq 1 30); do
        if timeout 2 bash -c "cat </dev/tcp/${BAS_AIRSIM_HOST}/${BAS_AIRSIM_PORT}" \
                &>/dev/null; then
            echo "  AirSim API reachable on ${BAS_AIRSIM_HOST}:${BAS_AIRSIM_PORT}"
            return 0
        fi
        sleep 2
    done
    echo "  AirSim API not reachable on ${BAS_AIRSIM_HOST}:${BAS_AIRSIM_PORT}" >&2
    echo "  Возможно блокирует Windows Firewall. ОДНОРАЗОВО запусти" >&2
    echo "  на Windows (от админа PowerShell):" >&2
    echo "    netsh advfirewall firewall add rule name=CosysAirSim41451 \\\\" >&2
    echo "       dir=in action=allow protocol=TCP localport=${BAS_AIRSIM_PORT}" >&2
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
elif [ "$BAS_AIRSIM_MODE" = "windows" ]; then
    install_windows_blocks
    start_windows_blocks
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
