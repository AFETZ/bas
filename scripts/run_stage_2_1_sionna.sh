#!/usr/bin/env bash
# Этап 2.1: mission + видео + Sionna RT dynamic radio model.
#
# Базируется на run_stage_1_5_2_mission.sh, добавляет:
#   * sionna_channel_publisher.py запускается параллельно: читает UAV
#     position из events.jsonl, lookup'ит в radio_maps/iris_runway.npz,
#     пишет current loss_ratio в /tmp/sionna_channel.json
#   * ns-3 получает --sionnaChannelPath=/tmp/sionna_channel.json, каждые
#     100 мс читает файл и обновляет RateErrorModel для payload канала.
#
# Использование:
#   sudo bash scripts/run_stage_2_1_sionna.sh                  # wifi_good baseline + Sionna lookup
#   sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_2_1_sionna.sh  # с реальной камерой
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

# Сбрасываем stale файл, чтобы ns-3 не подхватил предыдущий прогон.
rm -f "$SIONNA_JSON"

export BAS_SIONNA_CHANNEL_PATH="$SIONNA_JSON"

# Запускаем publisher параллельно (run_stage_1_5_2 успеет создать events.jsonl
# к моменту когда mission стартует и polition обновляется). Привязываемся к
# logs/ -- run_stage_1_5_2 создаст LOG_DIR.
echo "==> ждём пока run_stage_1_5_2 создаст events.jsonl"

(
    # Subshell: ждём появления events.jsonl, потом запускаем publisher.
    for _ in $(seq 1 600); do
        latest_dir="$(ls -td "${REPO_ROOT}/logs/stage_1_5_2_mission_wifi_good_"* 2>/dev/null | head -1 || true)"
        if [ -n "$latest_dir" ] && [ -f "$latest_dir/events.jsonl" ]; then
            echo "[sionna-publisher] events.jsonl: $latest_dir/events.jsonl"
            exec "$SIONNA_VENV/bin/python" \
                "$REPO_ROOT/scripts/sionna_channel_publisher.py" \
                --events "$latest_dir/events.jsonl" \
                --radio-map "$RADIO_MAP" \
                --out "$SIONNA_JSON" \
                --interval-ms 100
        fi
        sleep 1
    done
    echo "[sionna-publisher] events.jsonl не появился за 600s" >&2
) &
PUB_PID=$!
echo "==> sionna_channel_publisher PID=$PUB_PID"

cleanup_sionna() {
    kill -TERM "$PUB_PID" 2>/dev/null || true
    wait "$PUB_PID" 2>/dev/null || true
}
trap cleanup_sionna EXIT INT TERM

# Делегируем основной прогон 1.5.2 скрипту. Он подхватит
# BAS_SIONNA_CHANNEL_PATH через env -- ns-3 запускается с этим путём.
exec bash "$REPO_ROOT/scripts/run_stage_1_5_2_mission.sh" wifi_good
