#!/usr/bin/env bash
# Stage 2.4 Auto Demo: один command → готовое демо-видео + screenshots +
# Markdown отчёт для grant-отчётности.
#
# Этот wrapper:
#   1. Запускает выбранный demo-stack в background (run_stage_2_4_fpv_rf_demo.sh
#      по умолчанию — FPV + RF в одном кадре).
#   2. Ждёт пока /api/health и mavlink connect станут готовы.
#   3. Запускает scripts/auto_demo_recorder.py — Playwright + ffmpeg.
#   4. После завершения recorder'а останавливает stack.
#
# Артефакты:
#   logs/<run_id>/demo_report.md
#   logs/<run_id>/video/web_gcs.webm
#   logs/<run_id>/video/fpv.mjpeg.mp4
#   logs/<run_id>/screenshots/*.png + *.jpg
#
# Использование:
#   sudo bash scripts/run_stage_2_4_auto_demo.sh
#   sudo env BAS_AUTO_DEMO_STACK=run_stage_2_4_multi_uav_demo.sh \
#        bash scripts/run_stage_2_4_auto_demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Какой stack запускать. По дефолту fpv_rf_demo — самый показательный:
# и video поток, и RF spike-и при заходе за ангар.
STACK_SCRIPT="${BAS_AUTO_DEMO_STACK:-run_stage_2_4_fpv_rf_demo.sh}"
STACK_PATH="${SCRIPT_DIR}/${STACK_SCRIPT}"
if [ ! -x "$STACK_PATH" ]; then
    echo "Stack script not found or not executable: ${STACK_PATH}" >&2
    exit 1
fi

UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
UI_PORT="${BAS_GCS_UI_PORT:-8765}"
UI_URL="http://${UI_HOST}:${UI_PORT}"

# Запускаем headless по умолчанию — оператор автоматизирует, не смотрит.
export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-0}"
export BAS_GCS_UI_HOST="$UI_HOST"
export BAS_GCS_UI_PORT="$UI_PORT"

# Recorder выберет log_dir из RUN_ID который stack создаст. Нам нужно
# узнать его — генерируем заранее и пробрасываем в stack через BAS_RUN_ID.
RUN_ID="${BAS_RUN_ID:-stage_2_4_auto_demo_$(date -u +%Y%m%dT%H%M%SZ)}"
export BAS_RUN_ID="$RUN_ID"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
mkdir -p "$LOG_DIR"
echo "==> RUN_ID=${RUN_ID}"
echo "==> LOG_DIR=${LOG_DIR}"
echo "==> STACK=${STACK_SCRIPT}"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

STACK_LOG="${LOG_DIR}/auto_demo_stack.log"
RECORDER_LOG="${LOG_DIR}/auto_demo_recorder.log"

cleanup() {
    set +e
    echo "[auto_demo] cleanup"
    # Сначала тушим stack — он сам имеет cleanup trap который снимает
    # docker compose, ns3, fpv-mjpeg, mavbridge/mavrouter, etc.
    if [ -n "${STACK_PID:-}" ] && kill -0 "$STACK_PID" 2>/dev/null; then
        kill -INT "$STACK_PID" 2>/dev/null || true
        # Дать stack time для graceful cleanup.
        for _ in $(seq 1 30); do
            if ! kill -0 "$STACK_PID" 2>/dev/null; then break; fi
            sleep 1
        done
        kill -9 "$STACK_PID" 2>/dev/null || true
    fi
    set -e
}
trap cleanup EXIT INT TERM

echo "[auto_demo] launch stack in background"
bash "$STACK_PATH" > "$STACK_LOG" 2>&1 &
STACK_PID=$!
echo "  stack pid=${STACK_PID}, log=${STACK_LOG}"

echo "[auto_demo] wait for Web GCS UI"
# Просто ждём что Python web server откроет TCP:UI_PORT. Само api/health
# проверяется внутри recorder'а.
for i in $(seq 1 120); do
    if ss -tln 2>/dev/null | grep -q ":${UI_PORT}\b"; then
        echo "  UI port :${UI_PORT} listening (${i}s)"
        break
    fi
    if ! kill -0 "$STACK_PID" 2>/dev/null; then
        echo "  stack pid died before UI ready — см. ${STACK_LOG}" >&2
        tail -40 "$STACK_LOG" >&2 || true
        exit 2
    fi
    sleep 1
done
if ! ss -tln 2>/dev/null | grep -q ":${UI_PORT}\b"; then
    echo "  UI port :${UI_PORT} never opened" >&2
    tail -60 "$STACK_LOG" >&2 || true
    exit 2
fi

echo "[auto_demo] start recorder (Playwright + ffmpeg)"
"${REPO_ROOT}/.venv/bin/python" "${SCRIPT_DIR}/auto_demo_recorder.py" \
    --ui-url "$UI_URL" \
    --log-dir "$LOG_DIR" \
    --duration-budget-s 240 \
    2>&1 | tee "$RECORDER_LOG"
RECORDER_RC=${PIPESTATUS[0]}

echo
echo "[auto_demo] recorder finished rc=${RECORDER_RC}"
echo
if [ -f "${LOG_DIR}/demo_report.md" ]; then
    echo "==> demo report: ${LOG_DIR}/demo_report.md"
    echo "==> video:       ${LOG_DIR}/video/"
    echo "==> screenshots: ${LOG_DIR}/screenshots/"
fi

exit "$RECORDER_RC"
