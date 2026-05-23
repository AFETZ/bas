#!/usr/bin/env bash
# Stage 3 ИССГР — REST/OGC API server поверх base BAS stack.
#
# Запускает scripts/run_stage_2_4_fpv_rf_demo.sh в background, дожидается
# UI ready, потом scripts/issgr_api_server.py с live-tailer'ом events.jsonl:
# каждое flight event обновляет UAV в ИССГР repository, что даёт АСУ-клиентам
# real-time state дрона через стандартный OGC API Features endpoint.
#
# Артефакты:
#   logs/<run>/events.jsonl         ← orchestrator (source-of-truth для UAV)
#   logs/<run>/issgr_api.log        ← API server stdout
#   /tmp/bas_issgr_state.jsonl      ← repository snapshot (warm restart)
#
# Точки входа для АСУ:
#   http://127.0.0.1:8770/             — landing page
#   http://127.0.0.1:8770/docs          — Swagger UI (interactive)
#   http://127.0.0.1:8770/openapi.json  — OpenAPI 3.0 spec
#   http://127.0.0.1:8770/collections   — список ИССГР collections
#   http://127.0.0.1:8770/digital_twin  — единая GeoJSON FeatureCollection
#
# Пример из АСУ через curl:
#   curl http://127.0.0.1:8770/collections/uavs/items | jq .
#   curl http://127.0.0.1:8770/collections/uavs/items?bbox=149.16,-35.37,149.17,-35.36 | jq .
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BAS_ISSGR_HOST="${BAS_ISSGR_HOST:-0.0.0.0}"
BAS_ISSGR_PORT="${BAS_ISSGR_PORT:-8770}"
BAS_ISSGR_PERSIST="${BAS_ISSGR_PERSIST:-/tmp/bas_issgr_state.jsonl}"

# Base stack: FPV+RF (дрон + obstacles + Web GCS).
STACK_SCRIPT="${BAS_ISSGR_STACK:-run_stage_2_4_fpv_rf_demo.sh}"
STACK_PATH="${SCRIPT_DIR}/${STACK_SCRIPT}"
[ -x "$STACK_PATH" ] || { echo "stack script missing: $STACK_PATH" >&2; exit 1; }

UI_PORT="${BAS_GCS_UI_PORT:-8765}"
BAS_RUN_ID="${BAS_RUN_ID:-stage_3_issgr_demo_$(date -u +%Y%m%dT%H%M%SZ)}"
export BAS_RUN_ID
LOG_DIR="${REPO_ROOT}/logs/${BAS_RUN_ID}"
mkdir -p "$LOG_DIR"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

STACK_PID=""
ISSGR_PID=""

cleanup() {
    set +e
    echo "[issgr-demo] cleanup"
    [ -n "$ISSGR_PID" ] && kill "$ISSGR_PID" 2>/dev/null || true
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
echo "==> ISSGR API: http://${BAS_ISSGR_HOST}:${BAS_ISSGR_PORT}"
echo "==> Web GCS UI: http://127.0.0.1:${UI_PORT}"

# ---- 1. Base stack в background -----------------------------------------
echo "[issgr-demo] starting base stack (${STACK_SCRIPT})"
bash "$STACK_PATH" > "${LOG_DIR}/base_stack.log" 2>&1 &
STACK_PID=$!
echo "  base stack pid=${STACK_PID}"

echo "[issgr-demo] waiting for Web GCS UI on :${UI_PORT}"
for _ in $(seq 1 120); do
    if ss -tln 2>/dev/null | grep -q ":${UI_PORT}\b"; then
        echo "  UI ready"
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
# Дать events.jsonl появиться — orchestrator пишет после MAVLink connect.
for _ in $(seq 1 60); do
    [ -s "$EVENTS_PATH" ] && break
    sleep 1
done

# ---- 2. ИССГР API server -------------------------------------------------
echo "[issgr-demo] starting ISSGR REST/OGC API server"
"${REPO_ROOT}/.venv/bin/python" "${SCRIPT_DIR}/issgr_api_server.py" \
    --host "$BAS_ISSGR_HOST" \
    --port "$BAS_ISSGR_PORT" \
    --events "$EVENTS_PATH" \
    --persist "$BAS_ISSGR_PERSIST" \
    > "${LOG_DIR}/issgr_api.log" 2>&1 &
ISSGR_PID=$!
echo "  ISSGR API pid=${ISSGR_PID}"

# Wait for ISSGR port.
for _ in $(seq 1 30); do
    if ss -tln 2>/dev/null | grep -q ":${BAS_ISSGR_PORT}\b"; then
        echo "  ISSGR API ready"
        break
    fi
    sleep 1
done

cat <<INFO

==========================================================================
 BAS Prototype Stage 3 — ИССГР REST/OGC API запущен
--------------------------------------------------------------------------
 Web GCS UI:        http://127.0.0.1:${UI_PORT}/
 ИССГР API landing:  http://127.0.0.1:${BAS_ISSGR_PORT}/
 Swagger UI:         http://127.0.0.1:${BAS_ISSGR_PORT}/docs
 OpenAPI 3.0 spec:   http://127.0.0.1:${BAS_ISSGR_PORT}/openapi.json
 OGC Collections:    http://127.0.0.1:${BAS_ISSGR_PORT}/collections
 Цифровой двойник:   http://127.0.0.1:${BAS_ISSGR_PORT}/digital_twin
 Classifier:         http://127.0.0.1:${BAS_ISSGR_PORT}/classifier

 Пример АСУ-клиента (curl):
   curl -s http://127.0.0.1:${BAS_ISSGR_PORT}/collections/uavs/items | jq .
   curl -s http://127.0.0.1:${BAS_ISSGR_PORT}/collections/obstacles/items | jq .
   curl -s 'http://127.0.0.1:${BAS_ISSGR_PORT}/collections/uavs/items?issgr_class=operational_situation.uav.rotary_wing' | jq .

 Live tailer of orchestrator events.jsonl: UAV state обновляется при
 каждом flight event (TAKEOFF/GOTO/LAND).
==========================================================================

INFO

# Wait для base stack окончания.
wait "$STACK_PID"
exit $?
