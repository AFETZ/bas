#!/usr/bin/env bash
# Stage 3 cyber-resilience demo — атака видна на пульте И на витрине.
#
# Поднимает digital-twin стек (пульт :8765 + витрина :8810 + ИССГР :8770) через
# run_stage_3_issgr_demo.sh, запускает cyber_defense_monitor (детектит сигнатуры
# атак и пишет алерты в общий NDJSON), затем демонстрирует GPS-спуфинг. Алерт
# загорается красным баннером И на пульте, И на витрине; на карте витрины
# рисуется «подменённая позиция» (gps_spoof).
#
# defensive research ONLY: cyber_attack_simulator бьёт строго по localhost
# demo-порту (не по реальному SITL). Это closed-loop стенд «атака → детект →
# визуализация во всех интерфейсах».
#
# Артефакты:
#   $BAS_CYBER_ALERTS                       ← NDJSON алертов (читают /api/alerts)
#   logs/<run>/defense_monitor.log          ← stdout монитора
#   logs/<run>/attack_*.log                 ← результаты атак
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"

# Общий файл алертов: monitor пишет, пульт+витрина читают через /api/alerts.
# export — чтобы gcs_web_ui_server и admin_web_server (через base stack)
# прочитали тот же путь из BAS_CYBER_ALERTS.
export BAS_CYBER_ALERTS="${BAS_CYBER_ALERTS:-/tmp/bas_cyber_alerts.jsonl}"
CYBER_MAVLINK_PORT="${BAS_CYBER_MAVLINK_PORT:-14650}"
CYBER_CHANNEL="${BAS_CYBER_CHANNEL:-/tmp/sionna_channel.json}"
ADMIN_PORT="${BAS_ADMIN_PORT:-8810}"

STACK_SCRIPT="${BAS_CYBER_STACK:-run_stage_3_issgr_demo.sh}"
STACK_PATH="${SCRIPT_DIR}/${STACK_SCRIPT}"
[ -x "$STACK_PATH" ] || { echo "stack script missing: $STACK_PATH" >&2; exit 1; }

BAS_RUN_ID="${BAS_RUN_ID:-stage_3_cyber_demo_$(date -u +%Y%m%dT%H%M%SZ)}"
export BAS_RUN_ID
LOG_DIR="${REPO_ROOT}/logs/${BAS_RUN_ID}"
mkdir -p "$LOG_DIR"

ensure_root() { [ "$EUID" -eq 0 ] || { echo "sudo only" >&2; exit 1; }; }
ensure_root

: > "$BAS_CYBER_ALERTS"   # чистим алерты прошлого прогона

STACK_PID=""
MON_PID=""
cleanup() {
    set +e
    echo "[cyber-demo] cleanup"
    [ -n "$MON_PID" ] && kill "$MON_PID" 2>/dev/null || true
    if [ -n "$STACK_PID" ] && kill -0 "$STACK_PID" 2>/dev/null; then
        kill -INT "$STACK_PID" 2>/dev/null || true
        for _ in $(seq 1 30); do kill -0 "$STACK_PID" 2>/dev/null || break; sleep 1; done
        kill -9 "$STACK_PID" 2>/dev/null || true
    fi
    set -e
}
trap cleanup EXIT INT TERM

echo "==> RUN_ID=${BAS_RUN_ID}"
echo "==> Alerts NDJSON:       ${BAS_CYBER_ALERTS}"
echo "==> Defense MAVLink UDP: 127.0.0.1:${CYBER_MAVLINK_PORT}"

# ---- 1. Base stack: пульт + витрина + ИССГР ------------------------------
echo "[cyber-demo] starting base stack (${STACK_SCRIPT})"
bash "$STACK_PATH" > "${LOG_DIR}/base_stack.log" 2>&1 &
STACK_PID=$!

echo "[cyber-demo] waiting for Admin витрина :${ADMIN_PORT}"
for _ in $(seq 1 180); do
    ss -tln 2>/dev/null | grep -q ":${ADMIN_PORT}\b" && { echo "  ready"; break; }
    if ! kill -0 "$STACK_PID" 2>/dev/null; then
        echo "  base stack died" >&2; tail -30 "${LOG_DIR}/base_stack.log" >&2 || true; exit 2
    fi
    sleep 1
done

# ---- 2. Defense monitor --------------------------------------------------
echo "[cyber-demo] starting cyber_defense_monitor (MAVLink :${CYBER_MAVLINK_PORT} + channel)"
"$VENV_PY" "${SCRIPT_DIR}/cyber_defense_monitor.py" \
    --mavlink-port "$CYBER_MAVLINK_PORT" \
    --channel-file "$CYBER_CHANNEL" \
    --log-file "$BAS_CYBER_ALERTS" \
    --jump-window-ms 2500 \
    > "${LOG_DIR}/defense_monitor.log" 2>&1 &
MON_PID=$!
sleep 2

# ---- 3. Seed baseline position, потом GPS spoof (jump > threshold) --------
echo "[cyber-demo] seeding baseline GLOBAL_POSITION_INT (нужно для jump-детекта)"
BAS_CYBER_DEMO_PORT="$CYBER_MAVLINK_PORT" BAS_CYBER_SCRIPTS="$SCRIPT_DIR" \
    "$VENV_PY" - <<'PYEOF'
import os, socket, sys
sys.path.insert(0, os.environ["BAS_CYBER_SCRIPTS"])
from cyber_attack_simulator import (
    _mavlink_v2_frame, _pack_global_position_int, MSG_GLOBAL_POSITION_INT,
)
port = int(os.environ["BAS_CYBER_DEMO_PORT"])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = _pack_global_position_int(0, -35.363262, 149.165237, 50.0, 0, 0, 0, 0)
s.sendto(_mavlink_v2_frame(1, 1, MSG_GLOBAL_POSITION_INT, payload),
         ("127.0.0.1", port))
s.close()
print("baseline sent")
PYEOF
sleep 1

echo "[cyber-demo] firing demo GPS spoof (+200m teleport) → alert"
"$VENV_PY" "${SCRIPT_DIR}/cyber_attack_simulator.py" gps_spoof \
    --target-port "$CYBER_MAVLINK_PORT" --offset-lat-m 200 --offset-lon-m 200 \
    --duration-s 3 > "${LOG_DIR}/attack_gps_spoof.log" 2>&1 || true

cat <<INFO

==========================================================================
 Stage 3 CYBER demo — атака видна во ВСЕХ интерфейсах
--------------------------------------------------------------------------
 🕹  ПУЛЬТ:    http://127.0.0.1:8765/      — красный баннер «КИБЕРАТАКА»
 🗺  ВИТРИНА:  http://127.0.0.1:${ADMIN_PORT}/      — баннер + «подменённая
       позиция» на карте БАС (gps_spoof: ✖ + пунктир от реальной точки)

 Демо-атака уже отправлена (GPS spoof). Запусти ещё (defensive research,
 только по localhost demo-порту ${CYBER_MAVLINK_PORT}):

   # Инъекция команд (unauthorized sysid):
   ${VENV_PY} scripts/cyber_attack_simulator.py cmd_inject \\
       --target-port ${CYBER_MAVLINK_PORT} --fake-sysid 254

   # РЧ-глушение (бьёт по каналу Sionna):
   ${VENV_PY} scripts/cyber_attack_simulator.py rf_jam \\
       --channel-file ${CYBER_CHANNEL} --rssi-dbm -110 --duration-s 6

 Алерты: tail -f ${BAS_CYBER_ALERTS}
==========================================================================

INFO

wait "$STACK_PID"
exit $?
