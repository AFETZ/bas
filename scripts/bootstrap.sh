#!/usr/bin/env bash
# bas-prototype one-command bootstrap.
#
# Подготавливает свежую Ubuntu 22.04+ / WSL2 машину к запуску любого
# scripts/run_stage_*_demo.sh: ставит apt deps, Docker, Python venv,
# Playwright Chromium, собирает Docker-образы, опционально GPU Vulkan ICD
# (Dozen для WSL2) и Sionna RT.
#
# Idempotent — повторный запуск проверяет каждый шаг и пропускает уже
# выполненные. Полностью неинтерактивен (никаких debconf/needrestart prompt).
#
# Usage:
#   sudo bash scripts/bootstrap.sh                # базовая среда + Docker-образы
#   sudo bash scripts/bootstrap.sh --full         # + Sionna RT venv (TensorFlow)
#   sudo bash scripts/bootstrap.sh --no-docker    # пропустить Docker + сборку образов
#   sudo bash scripts/bootstrap.sh --no-gpu       # пропустить GPU Vulkan (Dozen)
#
# Время: ~5–8 мин базовая (с первой сборкой образов ~15 мин), ~25 мин --full.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6 2>/dev/null || echo "/home/${RUN_USER}")"

# Полностью неинтерактивный apt — иначе debconf/needrestart могут «подвесить»
# установку, ожидая ввод (особенно при выводе в /dev/null).
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

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

ensure_root() { [ "$EUID" -eq 0 ] || { echo "Запускать только под sudo: sudo bash scripts/bootstrap.sh" >&2; exit 1; }; }
ensure_root

# ---- Прогресс-вывод (этап / проценты / бар / время) ----------------------
TOTAL_STEPS=7
STEP=0
T_START="$(date +%s)"
if [ -t 1 ]; then
    C_HDR=$'\033[1;36m'; C_OK=$'\033[1;32m'; C_SUB=$'\033[0;33m'; C_ERR=$'\033[1;31m'; C_OFF=$'\033[0m'
else
    C_HDR=''; C_OK=''; C_SUB=''; C_ERR=''; C_OFF=''
fi

_fmt_time() { local s=$1; printf '%02d:%02d' $((s/60)) $((s%60)); }

step() {  # step "Название" "ETA"
    STEP=$((STEP+1))
    local pct=$(( STEP * 100 / TOTAL_STEPS ))
    local el=$(( $(date +%s) - T_START ))
    local filled=$(( pct / 5 )) bar='' i
    for ((i=0; i<20; i++)); do [ "$i" -lt "$filled" ] && bar+='█' || bar+='░'; done
    printf '\n%s[%d/%d] %3d%% %s  ⏱ %s%s\n' \
        "$C_HDR" "$STEP" "$TOTAL_STEPS" "$pct" "$bar" "$(_fmt_time "$el")" "$C_OFF"
    printf '%s▶ %s%s%s\n' "$C_HDR" "$1" "${2:+  ${C_SUB}(${2})}" "$C_OFF"
}
sub()     { printf '   %s•%s %s\n' "$C_SUB" "$C_OFF" "$*"; }
ok()      { printf '   %s✓%s %s\n' "$C_OK"  "$C_OFF" "$*"; }
warn()    { printf '   %s!%s %s\n' "$C_ERR" "$C_OFF" "$*"; }

sub "user=${RUN_USER}  home=${RUN_HOME}  repo=${REPO_ROOT}"

# Оставляет в списке только реально существующие в репозиториях пакеты —
# защищает от расхождений имён между релизами Ubuntu (напр. libasound2 на
# jammy vs libasound2t64 на noble).
apt_install() {
    local want=("$@") have=() p
    for p in "${want[@]}"; do
        if apt-cache show "$p" >/dev/null 2>&1; then
            have+=("$p")
        else
            sub "пропуск (нет в репозитории): $p"
        fi
    done
    apt-get install -y --no-install-recommends "${have[@]}" >/dev/null
}

# ---- 1. apt packages -----------------------------------------------------
step "APT-пакеты (build tools, python, ffmpeg, vulkan, libs)" "~2 мин"
apt-get update -q
apt_install \
    build-essential cmake git curl wget unzip ca-certificates \
    python3 python3-pip python3-venv \
    iproute2 bridge-utils socat jq \
    ffmpeg \
    vulkan-tools libvulkan1 mesa-vulkan-drivers vulkan-validationlayers \
    libsdl2-2.0-0 libsdl2-image-2.0-0 \
    libxss1 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpangocairo-1.0-0 libasound2-data libasound2t64 libasound2 \
    fonts-liberation
ok "apt OK"

# ---- 2. Docker -----------------------------------------------------------
if [ "$SKIP_DOCKER" -eq 0 ]; then
    step "Docker Engine + Compose" "~1 мин"
    if ! command -v docker >/dev/null; then
        apt_install docker.io docker-compose-v2
    fi
    if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q "^docker.service"; then
        systemctl enable --now docker 2>/dev/null || true
    else
        service docker start 2>/dev/null || true   # WSL2 без systemd
    fi
    for _ in $(seq 1 15); do docker info >/dev/null 2>&1 && break; sleep 1; done
    docker info >/dev/null 2>&1 || { warn "Docker daemon не стартовал"; exit 3; }
    usermod -aG docker "$RUN_USER" 2>/dev/null || true
    ok "$(docker --version 2>/dev/null | head -1)"
else
    step "Docker — пропущен (--no-docker)"
fi

# ---- 3. Python venv (.venv) ----------------------------------------------
step "Python venv (.venv) + orchestrator/analyzer" "~1 мин"
cd "$REPO_ROOT"
if [ ! -x "${REPO_ROOT}/.venv/bin/python" ]; then
    sudo -u "$RUN_USER" python3 -m venv .venv
fi
sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet --upgrade pip setuptools wheel
[ -d "${REPO_ROOT}/orchestrator" ] && \
    sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet -e "${REPO_ROOT}/orchestrator"
[ -d "${REPO_ROOT}/analyzer" ] && \
    sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet -e "${REPO_ROOT}/analyzer"
sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/pip" install --quiet \
    msgpack playwright pymavlink mavproxy pyyaml requests
ok ".venv готов ($(${REPO_ROOT}/.venv/bin/python --version 2>&1))"

# ---- 4. Playwright Chromium ----------------------------------------------
step "Playwright Chromium" "~1 мин"
# Системные зависимости ставим напрямую от root (мы уже root) — иначе
# playwright --with-deps под обычным пользователем дёргает sudo apt и виснет
# на запросе пароля. Браузер ставим и под user, и под root.
"${REPO_ROOT}/.venv/bin/playwright" install-deps chromium >/dev/null 2>&1 || true
sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/playwright" install chromium >/dev/null 2>&1 || \
    sudo -u "$RUN_USER" "${REPO_ROOT}/.venv/bin/playwright" install chromium 2>&1 | tail -3
"${REPO_ROOT}/.venv/bin/playwright" install chromium >/dev/null 2>&1 || true
ok "Playwright готов ($(${REPO_ROOT}/.venv/bin/playwright --version 2>/dev/null))"

# ---- 5. GPU Vulkan ICD (только WSL2) -------------------------------------
if [ "$SKIP_GPU" -eq 0 ]; then
    step "GPU Vulkan ICD (Dozen, только WSL2)"
    if grep -qi "microsoft" /proc/version 2>/dev/null; then
        if ! grep -rq "kisak" /etc/apt/sources.list.d/ 2>/dev/null; then
            add-apt-repository -y ppa:kisak/kisak-mesa 2>&1 | tail -2 || true
            apt-get update -q || true
            apt_install mesa-vulkan-drivers || true
        fi
        if [ -f /usr/share/vulkan/icd.d/dzn_icd.json ]; then
            ok "Dozen ICD: /usr/share/vulkan/icd.d/dzn_icd.json"
        else
            warn "Dozen ICD не найден (kisak PPA недоступен?)"
        fi
    else
        sub "не WSL2 — Dozen не нужен, пропуск"
    fi
else
    step "GPU — пропущен (--no-gpu)"
fi

# ---- 6. Sionna RT (--full) -----------------------------------------------
if [ "$FULL" -eq 1 ]; then
    step "Sionna RT venv (sionna_env) — TensorFlow и пр." "~10 мин"
    if [ ! -x "${REPO_ROOT}/sionna_env/bin/python" ]; then
        sudo -u "$RUN_USER" python3 -m venv "${REPO_ROOT}/sionna_env"
    fi
    sudo -u "$RUN_USER" "${REPO_ROOT}/sionna_env/bin/pip" install --quiet --upgrade pip
    if [ -f "${REPO_ROOT}/requirements_sionna.txt" ]; then
        sudo -u "$RUN_USER" "${REPO_ROOT}/sionna_env/bin/pip" install --quiet \
            -r "${REPO_ROOT}/requirements_sionna.txt"
    else
        sudo -u "$RUN_USER" "${REPO_ROOT}/sionna_env/bin/pip" install --quiet \
            sionna mitsuba drjit numpy tensorflow
    fi
    ok "Sionna RT готов"
else
    step "Sionna RT — пропущен (добавьте --full для установки)"
fi

# ---- 7. Docker images build ----------------------------------------------
# ВАЖНО: build-секции и теги образов лежат в docker-compose.yml
# (docker-compose.shared-netns.yml только запускает уже собранные образы).
if [ "$SKIP_DOCKER" -eq 0 ]; then
    step "Сборка Docker-образов (ns3 ~10 мин, остальные быстрее)" "~10–15 мин"
    cd "$REPO_ROOT"
    # service -> итоговый тег образа (для idempotent-проверки)
    declare -A IMG=(
        [orchestrator]="bas/orchestrator:dev"
        [video]="bas/video:dev"
        [gazebo]="bas/gazebo-harmonic:dev"
        [sitl]="bas/ardupilot-sitl:dev"
        [ns3]="bas/ns3:dev"
    )
    # Порядок: от быстрых к долгим (ns3 последним).
    for svc in orchestrator video gazebo sitl ns3; do
        tag="${IMG[$svc]}"
        if docker image inspect "$tag" >/dev/null 2>&1; then
            ok "уже собран: $tag"
            continue
        fi
        sub "сборка $svc → $tag ..."
        if docker compose -f docker-compose.yml build "$svc"; then
            ok "$tag собран"
        else
            warn "не удалось собрать $svc (см. вывод выше)"
        fi
    done
    echo
    docker images 'bas/*' --format '   {{.Repository}}:{{.Tag}}  {{.Size}}' 2>/dev/null | head -10
else
    step "Сборка Docker-образов — пропущена (--no-docker)"
fi

# ---- Verify --------------------------------------------------------------
TOTAL_EL=$(( $(date +%s) - T_START ))
printf '\n%s══ Проверка установки ══%s\n' "$C_HDR" "$C_OFF"
echo "  - .venv python : $(${REPO_ROOT}/.venv/bin/python --version 2>&1)"
[ "$FULL" -eq 1 ] && echo "  - sionna_env   : $(${REPO_ROOT}/sionna_env/bin/python --version 2>&1)"
echo "  - ffmpeg       : $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
[ "$SKIP_DOCKER" -eq 0 ] && echo "  - Docker       : $(docker --version 2>/dev/null)"
echo "  - Vulkan       : $(vulkaninfo --summary 2>/dev/null | grep -m1 deviceName | tr -s ' ' | sed 's/^ *//')"
echo "  - Playwright   : $(${REPO_ROOT}/.venv/bin/playwright --version 2>/dev/null)"

cat <<EOF

$(printf '%s' "$C_OK")[bootstrap] ГОТОВО за $(_fmt_time "$TOTAL_EL").$(printf '%s' "$C_OFF") Следующие шаги:

  1) Smoke-тест:
     sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good

  2) Демо с авто-записью:
     sudo bash scripts/run_stage_2_4_auto_demo.sh

  3) Веб-GCS (интерактивно):
     sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh
     open http://127.0.0.1:8765/

Полный каталог команд: docs/QUICKSTART.md
EOF
