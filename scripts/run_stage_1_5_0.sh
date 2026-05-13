#!/usr/bin/env bash
# Этап 1.5.0: SITL+Gazebo на host-network (как в 1.4), но GCS-сторона
# подключается к SITL через ns-3 радио-канал (control profile).
#
# Архитектура:
#                              ┌─────────────────────────────────────────┐
#                              │   netns bas-ctrl-far (GCS-сторона)      │
#                              │   eth0: 10.10.0.1/24                    │
#                              │   shadow_gcs.py → 10.10.0.99:5760       │
#                              └────────────────┬────────────────────────┘
#                                               │ veth → br-ctrl-far
#                                               │ ↕ tap-ctrl-far
#                                               │  ns-3 (delay+loss+outage)
#                                               │ ↕ tap-ctrl-near
#                                               │ → br-ctrl-near (10.10.0.99/24)
#                                               │
#                              ┌────────────────▼────────────────────────┐
#                              │   host (WSL2 namespace)                 │
#                              │   socat: 10.10.0.99:5760 → 127.0.0.1:5760
#                              │   ↓                                     │
#                              │   bas-sitl (network_mode: host)         │
#                              │   arducopter TCP 5760                   │
#                              └─────────────────────────────────────────┘
#
# Использование:
#   sudo bash scripts/run_stage_1_5_0.sh                      # с дефолтным профилем
#   sudo bash scripts/run_stage_1_5_0.sh degraded_lora       # с другим профилем
#
# Завершается после `duration` секунд (по умолчанию 180).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-wifi_good}"
RUN_ID="stage_1_5_0_$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
DURATION="${DURATION:-180}"
NS3_BIN="/work/ns3-src/build/scratch/ns3.40-two_channel-optimized"

# Параметры профилей (для этапа 1.5.0 берём из YAML вручную).
case "$PROFILE" in
    wifi_good)
        CTRL_DELAY_MS=5
        CTRL_LOSS=0.0
        CTRL_OUTAGE=""
        ;;
    degraded_lora)
        CTRL_DELAY_MS=250
        CTRL_LOSS=0.02
        CTRL_OUTAGE="30-33,60-62"
        ;;
    *)
        echo "Неизвестный профиль: $PROFILE (доступны: wifi_good, degraded_lora)" >&2
        exit 1
        ;;
esac

ensure_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "Запускайте с sudo (нужен root)." >&2
        exit 1
    fi
}

cleanup() {
    set +e
    echo "[cleanup] остановка процессов"
    [ -n "${SHADOW_PID:-}" ] && kill "$SHADOW_PID" 2>/dev/null
    [ -n "${SOCAT_PID:-}" ]  && kill "$SOCAT_PID"  2>/dev/null
    sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null 2>&1
    sg docker -c "docker compose -f ${REPO_ROOT}/docker-compose.yml down -v 2>/dev/null" >/dev/null
    set -e
}
trap cleanup EXIT INT TERM

ensure_root

mkdir -p "$LOG_DIR"
echo "==> run_id=${RUN_ID}, profile=${PROFILE}, duration=${DURATION}s"
echo "==> логи: $LOG_DIR"

# 1. Подготовка радио-сети (down + up для гарантированного чистого состояния).
echo "[1/6] setup_radio_net.sh down + up"
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" down >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/setup_radio_net.sh" up | tail -3

# 2. Поднимаем gazebo+sitl через compose.
echo "[2/6] docker compose up gazebo sitl"
sg docker -c "docker compose -f ${REPO_ROOT}/docker-compose.yml up -d gazebo sitl" | tail -3

# 3. Ждём SITL на 127.0.0.1:5760.
echo "[3/6] жду TCP 5760"
for i in $(seq 1 30); do
    if ss -tln 2>/dev/null | grep -q ":5760"; then break; fi
    sleep 1
done
if ! ss -tln 2>/dev/null | grep -q ":5760"; then
    echo "SITL 5760 не открылся за 30с" >&2
    exit 2
fi
# Дополнительный settle wait чтобы SITL прошёл init.
sleep 10

# 4. Запускаем ns-3 (на host network, монтирует TAP'ы).
echo "[4/6] ns-3 docker run"
sg docker -c "docker rm -f bas-ns3-stage15 2>/dev/null" >/dev/null || true

# Собираем аргументы ns-3 динамически (пустой outage НЕ передаём).
NS3_ARGS="--runId=${RUN_ID} --duration=$((DURATION + 30)) --ctrlDelayMs=${CTRL_DELAY_MS} --ctrlLoss=${CTRL_LOSS} --ploadDelayMs=200 --ploadLoss=0.0"
if [ -n "${CTRL_OUTAGE}" ]; then
    NS3_ARGS="${NS3_ARGS} --ctrlOutage=${CTRL_OUTAGE}"
fi

# Передаём в контейнер скрипт через env, чтобы избежать хаоса с кавычками.
sg docker -c "docker run -d --name bas-ns3-stage15 --network host --cap-add NET_ADMIN --privileged \
    -e NS3_ARGS='${NS3_ARGS}' \
    -v ${REPO_ROOT}/ns3:/work/ns3:ro \
    -v ${REPO_ROOT}/logs:/work/logs \
    --entrypoint bash bas/ns3:dev -c '\
        cp /work/ns3/scenarios/two_channel.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src \
        && ./ns3 build > /tmp/build.log 2>&1 \
        && ${NS3_BIN} \$NS3_ARGS'" > /dev/null
echo "  ns-3 контейнер запущен (args: ${NS3_ARGS})"

# Ждём первое событие в ns3_events.jsonl (значит ns-3 поднял TAP'ы).
NS3_LOG="${LOG_DIR}/ns3_events.jsonl"
for i in $(seq 1 60); do
    [ -s "$NS3_LOG" ] && break
    sleep 2
done
if ! [ -s "$NS3_LOG" ]; then
    echo "ns-3 не стартовал (нет ns3_events.jsonl)" >&2
    sg docker -c "docker logs bas-ns3-stage15 2>&1 | tail -20" >&2
    exit 3
fi
echo "  ns-3 готов"

# 5. socat-proxy host:5760 ↔ 10.10.0.99:5760.
echo "[5/6] socat-proxy 10.10.0.99:5760 → 127.0.0.1:5760"
# bind на br-ctrl-near IP (10.10.0.99) - ловит трафик из far netns через ns-3.
socat TCP4-LISTEN:5760,bind=10.10.0.99,fork,reuseaddr TCP4:127.0.0.1:5760 &
SOCAT_PID=$!
sleep 1

# 6. Shadow GCS в bas-ctrl-far netns.
echo "[6/6] shadow_gcs.py в bas-ctrl-far netns"
ip netns exec bas-ctrl-far "${REPO_ROOT}/.venv/bin/python3" \
    "${REPO_ROOT}/scripts/shadow_gcs.py" \
    --endpoint tcp:10.10.0.99:5760 \
    --out "${LOG_DIR}/shadow_gcs.jsonl" \
    --duration "${DURATION}" &
SHADOW_PID=$!

echo
echo "Всё работает. Ждём ${DURATION}с пока shadow_gcs соберёт телеметрию."
echo "  host-side MAVLink: tcp:127.0.0.1:5760 (доступен из других host-приложений)"
echo "  shadow-side через ns-3: tcp:10.10.0.99:5760 → радио → 10.10.0.1 (GCS)"
echo

wait "$SHADOW_PID" || true

echo
echo "=== Итог ==="
echo "Логи: ${LOG_DIR}"
ls -la "${LOG_DIR}/" || true
