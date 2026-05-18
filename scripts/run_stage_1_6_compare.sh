#!/usr/bin/env bash
# Этап 1.6: сравнительный отчёт WiFi vs LoRa.
#
# Прогоняет оба профиля через scripts/run_stage_1_5_2_mission.sh (mission +
# RTP-видео через ns-3) и склеивает их в один side-by-side отчёт + CSV
# через `bas-analyzer-compare`.
#
# Использование:
#   sudo bash scripts/run_stage_1_6_compare.sh
#
# Опционально:
#   BAS_VIDEO_SOURCE=camera ...    # реальная бортовая Gazebo POV camera (1.5.2.b)
#   STAGE16_SKIP_RUNS=1 ...        # не запускать новые прогоны, использовать
#                                  # последние существующие logs/stage_1_5_2_*
#                                  # (полезно для итерации над comparator'ом)
#
# Output:
#   logs/stage_1_6_<UTC>/
#     comparison.md
#     comparison.csv
#     wifi_good/   -> симлинк на исходный logs/stage_1_5_2_mission_wifi_good_*
#     degraded_lora/ -> симлинк на исходный
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE="$(date -u +%Y%m%dT%H%M%SZ)"
SUITE_DIR="${REPO_ROOT}/logs/stage_1_6_${DATE}"
RUN_SCRIPT="${REPO_ROOT}/scripts/run_stage_1_5_2_mission.sh"
ANALYZER="${REPO_ROOT}/.venv/bin/bas-analyzer-compare"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

mkdir -p "$SUITE_DIR"
echo "==> suite_dir: $SUITE_DIR"

latest_run() {
    local pattern="$1"
    ls -1dt "${REPO_ROOT}/logs/${pattern}"_* 2>/dev/null | head -1
}

run_or_pick() {
    local profile="$1"
    if [ "${STAGE16_SKIP_RUNS:-0}" = "1" ]; then
        local existing
        existing="$(latest_run "stage_1_5_2_mission_${profile}")"
        if [ -z "$existing" ] || [ ! -d "$existing" ]; then
            echo "STAGE16_SKIP_RUNS=1, но не нашли logs/stage_1_5_2_mission_${profile}_*" >&2
            exit 1
        fi
        echo "$existing"
        return
    fi

    echo "==> прогон $profile" >&2
    bash "$RUN_SCRIPT" "$profile" >&2 || {
        echo "Прогон $profile упал" >&2
        return 1
    }
    latest_run "stage_1_5_2_mission_${profile}"
}

WIFI_RUN="$(run_or_pick wifi_good)"
echo "==> wifi_good run-dir: $WIFI_RUN"

LORA_RUN="$(run_or_pick degraded_lora)"
echo "==> degraded_lora run-dir: $LORA_RUN"

# Симлинки для удобной навигации.
ln -sfn "$WIFI_RUN" "$SUITE_DIR/wifi_good"
ln -sfn "$LORA_RUN" "$SUITE_DIR/degraded_lora"

# Запуск comparator.
"$ANALYZER" "$WIFI_RUN" "$LORA_RUN" \
    --label-a wifi_good \
    --label-b degraded_lora \
    --out-dir "$SUITE_DIR" \
    >/dev/null

echo
echo "==> отчёт сравнения:"
echo "    $SUITE_DIR/comparison.md"
echo "    $SUITE_DIR/comparison.csv"
echo
echo "==> верхушка comparison.md:"
sed -n '1,40p' "$SUITE_DIR/comparison.md"
