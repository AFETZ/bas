#!/usr/bin/env bash
# bas-prototype one-command bootstrap.
#
# Подготавливает свежую WSL2 / Ubuntu 22.04+ машину к запуску любого
# scripts/run_stage_*_demo.sh: ставит apt deps, Docker, Python venv,
# Playwright Chromium, опционально GPU Vulkan ICD (Dozen) и Sionna RT.
#
# Idempotent — повторный запуск проверяет каждый шаг и пропускает уже
# выполненные.
#
# Usage:
#   sudo bash scripts/bootstrap.sh                # минимальный (без Sionna и AirSim)
#   sudo bash scripts/bootstrap.sh --full         # включая Sionna venv + Cosys-AirSim
#   sudo bash scripts/bootstrap.sh --no-docker    # пропустить Docker install
#
# Время: ~5 мин минимум, ~20 мин full (с Sionna + AirSim download)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-${USER:-afetz}}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6 2>/dev/null || echo "/home/${RUN_USER}")"

FULL=0
SKIP_DOCKER=0
SKIP_GPU=0
while [ $# -gt 0 ]; do
    case "$1" in
        --full) FULL=1 ;;
        --no-docker) SKIP_DOCKER=1 ;;
        --no-gpu) SKIP_GPU=1 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

log() { echo "[bootstrap] $*"; }

log "user=${RUN_USER} home=${RUN_HOME} repo=${REPO_ROOT}"

# ---- 1. apt packages -----------------------------------------------------
log "1/7 — apt packages"
apt-get update -q
apt-get install -y --no-install-recommends \
    build-essential cmake git curl wget unzip ca-certificates \
    python3 python3-pip python3-venv \
    iproute2 bridge-utils socat jq \
    ffmpeg \
    vulkan-tools libvulkan1 mesa-vulkan-drivers vulkan-validationlayers \
    libsdl2-2.0-0 libsdl2-image-2.0-0 \
    libxss1 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpangocairo-1.0-0 libasound2-data libasound2t64 \
    fonts-liberation \
    > /dev/null
log "   apt OK"

# ---- 2. Docker -----------------------------------------------------------
if [ "$SKIP_DOCKER" -eq 0 ]; then
    log "2/7 — Docker daemon"
    if ! command -v docker >/dev/null; then
        apt-get install -y docker.io docker-compose-v2 > /dev/null
    fi
    if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q "^docker.service"; then
        systemctl enable --now docker 2>/dev/null || true
    else
        # WSL2 без systemd
        service docker start 2>/dev/null || true
    fi
    # Wait for daemon.
    for _ in $(seq 1 15); do
        docker info >/dev/null 2>&1 && break
        sleep 1
    done
    docker info >/dev/null || { echo "Docker daemon failed to start"; exit 3; }
    usermod -aG docker "$RUN_USER" 2>/dev/null || true
    log "   Docker $(docker --version 2>/dev/null | head -1)"
else
    log "2/7 — Docker skipped (--no-docker)"
fi

# ---- 3. Python venv ------------------------------------------------------
log "3/7 — Python venv (.venv)"
cd "$REPO_ROOT"
if [ ! -x "${REPO_ROOT}/.venv/bin/python" ]; then
    sudo -u "$RUN_USER" python3 -m venv .venv
fi
# pip + project + extras
sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet --upgrade pip setuptools wheel
[ -d "${REPO_ROOT}/orchestrator" ] && \
    sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet -e "${REPO_ROOT}/orchestrator"
[ -d "${REPO_ROOT}/analyzer" ] && \
    sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet -e "${REPO_ROOT}/analyzer"
sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet \
    msgpack playwright pymavlink mavproxy pyyaml requests
log "   .venv ready"

# ---- 4. Playwright Chromium ----------------------------------------------
log "4/7 — Playwright Chromium browser"
# Под user и под root (для sudo-запусков auto_demo recorder).
sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/playwright" install chromium --with-deps 2>&1 | tail -3
"${REPO_ROOT}/.venv/bin/playwright" install chromium 2>&1 | tail -3
log "   Playwright ready"

# ---- 5. GPU Vulkan ICD (опционально) -------------------------------------
if [ "$SKIP_GPU" -eq 0 ]; then
    log "5/7 — GPU Vulkan ICD (Dozen for WSL2 NVIDIA)"
    if grep -q "Microsoft" /proc/version 2>/dev/null; then
        # WSL2 — добавляем kisak-mesa PPA для Dozen
        if ! grep -rq "kisak" /etc/apt/sources.list.d/ 2>/dev/null; then
            add-apt-repository -y ppa:kisak/kisak-mesa 2>&1 | tail -2
            apt-get update -q
            apt-get install -y mesa-vulkan-drivers > /dev/null
        fi
        if [ -f /usr/share/vulkan/icd.d/dzn_icd.json ]; then
            log "   Dozen ICD: /usr/share/vulkan/icd.d/dzn_icd.json"
            vulkaninfo --summary 2>/dev/null | grep deviceName | head -3 | sed 's/^/   /'
        else
            log "   Dozen ICD not found (kisak PPA might be unavailable)"
        fi
    else
        log "   not WSL2, skipping Dozen"
    fi
else
    log "5/7 — GPU skipped (--no-gpu)"
fi

# ---- 6. Sionna RT (опционально, --full) ----------------------------------
if [ "$FULL" -eq 1 ]; then
    log "6/7 — Sionna RT venv (sionna_env/)"
    if [ ! -x "${REPO_ROOT}/sionna_env/bin/python" ]; then
        sudo -u "$RUN_USER" python3 -m venv "${REPO_ROOT}/sionna_env"
    fi
    if [ -f "${REPO_ROOT}/requirements_sionna.txt" ]; then
        sudo -u "$RUN_USER" "${REPO_ROOT}/sionna_env/bin/pip" install --quiet --upgrade pip
        sudo -u "$RUN_USER" "${REPO_ROOT}/sionna_env/bin/pip" install --quiet \
            -r "${REPO_ROOT}/requirements_sionna.txt"
    else
        sudo -u "$RUN_USER" "${REPO_ROOT}/sionna_env/bin/pip" install --quiet \
            sionna mitsuba drjit numpy tensorflow
    fi
    log "   Sionna RT ready"
else
    log "6/7 — Sionna RT skipped (use --full to install)"
fi

# ---- 7. Docker images build ----------------------------------------------
if [ "$SKIP_DOCKER" -eq 0 ]; then
    log "7/7 — Docker images build (gazebo/sitl/ns3/video/mavros)"
    cd "$REPO_ROOT"
    # bas/ns3:dev — основной (включает ns-3 build, ~10 мин)
    if ! docker image inspect bas/ns3:dev >/dev/null 2>&1; then
        log "   building bas/ns3:dev (~10 мин)..."
        sg docker -c "docker compose -f docker-compose.shared-netns.yml build ns3 2>&1" | tail -3 || true
    fi
    # Остальные образы быстрее
    for svc in gazebo sitl video mavros; do
        if ! docker image inspect bas/${svc}:dev >/dev/null 2>&1; then
            log "   building bas/${svc}:dev..."
            sg docker -c "docker compose -f docker-compose.shared-netns.yml build ${svc} 2>&1" | tail -3 || true
        fi
    done
    sg docker -c "docker images bas/* --format '{{.Repository}}:{{.Tag}}\\t{{.Size}}'" 2>&1 | head -10
else
    log "7/7 — Docker images build skipped"
fi

# ---- Verify --------------------------------------------------------------
log "Verify install:"
echo "  - .venv python: $(${REPO_ROOT}/.venv/bin/python --version 2>&1)"
[ "$FULL" -eq 1 ] && echo "  - sionna_env python: $(${REPO_ROOT}/sionna_env/bin/python --version 2>&1)"
echo "  - ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"
echo "  - Docker: $(docker --version 2>/dev/null)"
echo "  - Vulkan: $(vulkaninfo --summary 2>/dev/null | grep deviceName | head -1 | tr -s ' ')"
echo "  - Playwright: $(${REPO_ROOT}/.venv/bin/playwright --version 2>/dev/null)"

cat <<'EOF'

[bootstrap] DONE. Next steps:

  1. Smoke test:
     sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good

  2. Full demo с авто-записью:
     sudo bash scripts/run_stage_2_4_auto_demo.sh

  3. Web GCS интерактивный:
     sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh
     open http://127.0.0.1:8765/

См. docs/QUICKSTART.md для полного каталога команд.
EOF
