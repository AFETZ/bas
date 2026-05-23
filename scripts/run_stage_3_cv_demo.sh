#!/usr/bin/env bash
# Stage 3 CV-обработка видовых данных.
#
# Запускает базовый FPV+RF stack + ИССГР REST API + YOLOv8n детектор
# подписанный на /camera.mjpg. Каждое обнаружение geo-tag-ится через UAV
# pose + camera FOV ray-cast и POSTится в ИССГР API как SensorReading
# (time-series). Annotated frames сохраняются в logs/<run>/cv_detections/.
#
# Идея для grant отчёта: дрон облетает RF demo сцену, FPV камера видит
# препятствия → YOLOv8n (хотя на UE5 Blocks plain-shape props классы COCO
# не найдут — для реалистичного теста замените scene на urban environment
# или используйте --fpv-url с COCO-friendly стрима).
#
# Артефакты:
#   logs/<run>/cv_detections/frame_NNNNNN.jpg   ← annotated frames
#   logs/<run>/cv_detections.jsonl              ← NDJSON detection log
#   ISSGR /collections/sensor_readings/items    ← REST endpoint с детекциями
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BAS_ISSGR_PORT="${BAS_ISSGR_PORT:-8770}"
BAS_CV_INTERVAL_S="${BAS_CV_INTERVAL_S:-2}"
BAS_CV_MIN_CONFIDENCE="${BAS_CV_MIN_CONFIDENCE:-0.4}"
BAS_CV_CAMERA_FOV_DEG="${BAS_CV_CAMERA_FOV_DEG:-80}"
BAS_CV_CAMERA_PITCH_DEG="${BAS_CV_CAMERA_PITCH_DEG:--45}"
BAS_CV_FPV_URL="${BAS_CV_FPV_URL:-http://127.0.0.1:8765/camera.mjpg}"
STACK_SCRIPT="${BAS_CV_STACK:-run_stage_3_issgr_demo.sh}"
STACK_PATH="${SCRIPT_DIR}/${STACK_SCRIPT}"

BAS_RUN_ID="${BAS_RUN_ID:-stage_3_cv_demo_$(date -u +%Y%m%dT%H%M%SZ)}"
export BAS_RUN_ID
LOG_DIR="${REPO_ROOT}/logs/${BAS_RUN_ID}"
mkdir -p "$LOG_DIR"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

STACK_PID=""
CV_PID=""

cleanup() {
    set +e
    echo "[cv-demo] cleanup"
    [ -n "$CV_PID" ] && kill "$CV_PID" 2>/dev/null || true
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
echo "==> FPV stream:   ${BAS_CV_FPV_URL}"
echo "==> ИССГР API:    http://127.0.0.1:${BAS_ISSGR_PORT}"
echo "==> Detector:     interval=${BAS_CV_INTERVAL_S}s, min_conf=${BAS_CV_MIN_CONFIDENCE}, fov=${BAS_CV_CAMERA_FOV_DEG}°, pitch=${BAS_CV_CAMERA_PITCH_DEG}°"

# ---- 1. Base stack: FPV+RF + ISSGR --------------------------------------
echo "[cv-demo] starting base stack (${STACK_SCRIPT})"
bash "$STACK_PATH" > "${LOG_DIR}/base_stack.log" 2>&1 &
STACK_PID=$!

echo "[cv-demo] waiting for FPV stream + ISSGR API"
for _ in $(seq 1 180); do
    if ss -tln 2>/dev/null | grep -q ":${BAS_ISSGR_PORT}\b" \
            && curl -sf -m 2 "$BAS_CV_FPV_URL" -r 0-1 -o /dev/null 2>/dev/null; then
        echo "  ready"
        break
    fi
    if ! kill -0 "$STACK_PID" 2>/dev/null; then
        echo "  base stack died" >&2
        tail -30 "${LOG_DIR}/base_stack.log" >&2 || true
        exit 2
    fi
    sleep 1
done

EVENTS_PATH="${LOG_DIR}/events.jsonl"

# ---- 2. CV-detector ------------------------------------------------------
echo "[cv-demo] starting CV detector"
"${REPO_ROOT}/.venv/bin/python" "${SCRIPT_DIR}/cv_detector.py" \
    --fpv-url "$BAS_CV_FPV_URL" \
    --issgr-url "http://127.0.0.1:${BAS_ISSGR_PORT}" \
    --events "$EVENTS_PATH" \
    --interval-s "$BAS_CV_INTERVAL_S" \
    --min-confidence "$BAS_CV_MIN_CONFIDENCE" \
    --camera-fov-deg "$BAS_CV_CAMERA_FOV_DEG" \
    --camera-pitch-deg "$BAS_CV_CAMERA_PITCH_DEG" \
    --log-dir "$LOG_DIR" \
    2>&1 | tee "${LOG_DIR}/cv_detector.log" &
CV_PID=$!

cat <<INFO

==========================================================================
 Stage 3 CV-обработка запущена
--------------------------------------------------------------------------
 Web GCS UI:        http://127.0.0.1:8765/
 ИССГР API landing:  http://127.0.0.1:${BAS_ISSGR_PORT}/
 ИССГР Swagger UI:   http://127.0.0.1:${BAS_ISSGR_PORT}/docs

 Live CV detections:
   curl -s http://127.0.0.1:${BAS_ISSGR_PORT}/collections/sensor_readings/items | jq .

 Annotated frames:
   ${LOG_DIR}/cv_detections/frame_NNNNNN.jpg

 Detection log (NDJSON):
   tail -f ${LOG_DIR}/cv_detections.jsonl
==========================================================================

INFO

wait "$STACK_PID"
exit $?
