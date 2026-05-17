#!/usr/bin/env bash
# Этап 2.1: mission + видео + Sionna RT dynamic radio model.
#
# Архитектура (без race condition в выборе LOG_DIR):
#   1. Этот скрипт первым делом ВЫЧИСЛЯЕТ RUN_ID и экспортирует BAS_RUN_ID.
#   2. Запускает sionna_channel_publisher параллельно, который ждёт
#      events.jsonl ИМЕННО в ${REPO_ROOT}/logs/${RUN_ID}/events.jsonl
#      (не "последний по pattern" -- так не подцепить старый прогон).
#   3. exec'ит run_stage_1_5_2_mission.sh, который видит BAS_RUN_ID
#      из env и переиспользует его как свой RUN_ID + LOG_DIR.
#   4. ns-3 получает --sionnaChannelPath=/tmp/sionna_channel.json,
#      каждые 100 мс читает файл и обновляет RateErrorModel + channel
#      delay для payload канала.
#
# Использование:
#   sudo bash scripts/run_stage_2_1_sionna.sh
#   sudo env BAS_VIDEO_SOURCE=camera BAS_GAZEBO_GUI=1 bash scripts/run_stage_2_1_sionna.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RADIO_MAP="${BAS_SIONNA_RADIO_MAP:-${REPO_ROOT}/radio_maps/iris_runway.npz}"
SIONNA_VENV="${REPO_ROOT}/sionna_env"
SIONNA_JSON="/tmp/sionna_channel.json"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

if [ ! -f "$RADIO_MAP" ]; then
    echo "Radio map не найдена: $RADIO_MAP" >&2
    echo "Сначала запустите:" >&2
    echo "  bash scripts/setup_sionna.sh && \\" >&2
    echo "  ${SIONNA_VENV}/bin/python scripts/export_scene_to_sionna.py && \\" >&2
    echo "  ${SIONNA_VENV}/bin/python scripts/compute_radio_map.py --save-png" >&2
    exit 1
fi

if [ ! -x "$SIONNA_VENV/bin/python" ]; then
    echo "Sionna venv не найден: $SIONNA_VENV" >&2
    echo "Сначала: bash scripts/setup_sionna.sh" >&2
    exit 1
fi

# Pre-compute RUN_ID -- этот же ID будет использован run_stage_1_5_2_mission.sh
# (через BAS_RUN_ID env), и publisher будет ждать LOG_DIR именно по этому RUN_ID.
PROFILE="${PROFILE:-wifi_good}"
RUN_ID="stage_2_1_sionna_${PROFILE}_$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
export BAS_RUN_ID="$RUN_ID"
echo "==> RUN_ID=$RUN_ID"
echo "==> LOG_DIR=$LOG_DIR"

# Сбрасываем stale файл, чтобы ns-3 не подхватил предыдущий прогон.
rm -f "$SIONNA_JSON"

export BAS_SIONNA_CHANNEL_PATH="$SIONNA_JSON"

# Запускаем publisher параллельно. Он ждёт events.jsonl ИМЕННО в нашем LOG_DIR.
echo "==> ждём пока run_stage_1_5_2 создаст ${LOG_DIR}/events.jsonl"

(
    # Subshell: ждём появления events.jsonl в нашем конкретном LOG_DIR.
    EVENTS_PATH="${LOG_DIR}/events.jsonl"
    for _ in $(seq 1 600); do
        if [ -f "$EVENTS_PATH" ]; then
            echo "[sionna-publisher] events.jsonl: $EVENTS_PATH"
            exec "$SIONNA_VENV/bin/python" \
                "$REPO_ROOT/scripts/sionna_channel_publisher.py" \
                --events "$EVENTS_PATH" \
                --radio-map "$RADIO_MAP" \
                --out "$SIONNA_JSON" \
                --interval-ms 100
        fi
        sleep 1
    done
    echo "[sionna-publisher] events.jsonl не появился за 600s в $EVENTS_PATH" >&2
) &
PUB_PID=$!
echo "==> sionna_channel_publisher PID=$PUB_PID"

cleanup_sionna() {
    kill -TERM "$PUB_PID" 2>/dev/null || true
    wait "$PUB_PID" 2>/dev/null || true
    # Дополнительно убиваем orphan'ов (на случай если publisher fork'нул что-то).
    pkill -f sionna_channel_publisher.py 2>/dev/null || true
}
trap cleanup_sionna EXIT INT TERM

# Делегируем основной прогон 1.5.2 скрипту. Он подхватит BAS_RUN_ID +
# BAS_SIONNA_CHANNEL_PATH через env.
exec bash "$REPO_ROOT/scripts/run_stage_1_5_2_mission.sh" "$PROFILE"
