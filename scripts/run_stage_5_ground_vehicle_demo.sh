#!/usr/bin/env bash
# Stage 5 — НАЗЕМНЫЙ ТРАНСПОРТ (ArduRover SITL) в цифровом двойнике ИССГР.
#
# Закрывает пункт ТЗ "среда моделирования наземного дорожного транспорта и
# транспорта вне дорог". Базовый автопилот — ArduPilot ArduRover (тот же
# autopilot-стек, что и ArduCopter для БАС; frame=Rover).
#
# Поднимает: ИССГР :8770 + ArduRover SITL (:5770) + publisher (телеметрия →
# ИССГР как ground_vehicle объект) + Admin витрина :8810 + ручное вождение.
# Машина едет — и появляется/двигается в цифровом двойнике рядом с БАС.
#
# FRAME: rover=дорожный (ackermann) | rover-skid=вне дорог (skid-steer).
# Без sudo. Запуск: bash scripts/run_stage_5_ground_vehicle_demo.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV="${REPO_ROOT}/.venv/bin/python"
ARDUPILOT="${BAS_ARDUPILOT:-/home/afetz/ardupilot}"
ARDUROVER="${ARDUPILOT}/build/sitl/bin/ardurover"

FRAME="${BAS_ROVER_FRAME:-rover}"
ISSGR_PORT="${BAS_ISSGR_PORT:-8770}"
ADMIN_PORT="${BAS_ADMIN_PORT:-8810}"
ROVER_MAV=5770        # SERIAL0 (primary) — драйвер шлёт команды
ROVER_MAV_TELEM=5772  # SERIAL1 (telem1) — publisher читает телеметрию
DUR="${BAS_DEMO_DURATION:-40}"
NO_BROWSER=0
for a in "$@"; do [ "$a" = "--no-browser" ] && NO_BROWSER=1; done

[ -x "$VENV" ] || { echo "venv missing: $VENV"; exit 1; }
[ -x "$ARDUROVER" ] || { echo "ArduRover не собран. Собрать: cd $ARDUPILOT && ./waf rover"; exit 1; }

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/ground_vehicle_${TS}"
mkdir -p "$LOG_DIR"
PARM="${LOG_DIR}/rover.parm"
SRC_PARM="${ARDUPILOT}/Tools/autotest/default_params/${FRAME}.parm"
[ -f "$SRC_PARM" ] || SRC_PARM="${ARDUPILOT}/Tools/autotest/default_params/rover.parm"
cat "$SRC_PARM" > "$PARM"
printf "\nARMING_CHECK 0\nFS_GCS_ENABLE 0\nFS_THR_ENABLE 0\n" >> "$PARM"

PIDS=()
cleanup() {
    echo; echo "[gv] cleanup"
    for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
    pkill -9 -f "ardurover --model rover" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

launch() {
    local label="$1"; shift
    "$@" >"${LOG_DIR}/${label}.log" 2>&1 &
    PIDS+=("$!")
    printf "  [+] %-16s pid=%-6d\n" "$label" "$!"
}
wait_port() {
    for _ in $(seq 1 "${3:-30}"0); do
        "$VENV" -c "import socket;s=socket.socket();s.settimeout(0.1);s.connect(('127.0.0.1',$1));s.close()" 2>/dev/null && return 0
        sleep 0.1
    done
    echo "  [!] port $1 ($2) not ready"; return 1
}

# Pre-clean.
fuser -k "${ISSGR_PORT}/tcp" "${ADMIN_PORT}/tcp" 2>/dev/null
pkill -9 ardurover 2>/dev/null
sleep 1

cat <<EOF

==========================================================================
  НАЗЕМНЫЙ ТРАНСПОРТ (ArduRover) в цифровом двойнике ИССГР
==========================================================================
  Frame:   ${FRAME} ($([ "$FRAME" = "rover-skid" ] && echo "вне дорог, skid-steer" || echo "дорожный, ackermann"))
  ИССГР:   http://127.0.0.1:${ISSGR_PORT}/   витрина: http://127.0.0.1:${ADMIN_PORT}/
  Logs:    ${LOG_DIR}
==========================================================================
EOF

echo "==> [1/5] ИССГР цифровой двойник :${ISSGR_PORT}"
launch issgr "$VENV" "${SCRIPT_DIR}/issgr_api_server.py" --port "$ISSGR_PORT" --seed-profile urban
wait_port "$ISSGR_PORT" "ИССГР"

echo "==> [2/5] ArduRover SITL (--model rover, frame=${FRAME}) :${ROVER_MAV}"
( cd "$LOG_DIR" && exec "$ARDUROVER" --model rover --speedup 1 \
    --defaults "$PARM" --home -35.363262,149.165237,584,90 \
    --instance 1 -S ) >"${LOG_DIR}/ardurover.log" 2>&1 &
PIDS+=("$!")
printf "  [+] %-16s pid=%-6d\n" "ardurover" "$!"
wait_port "$ROVER_MAV" "ArduRover"

echo "==> [3/5] publisher: ArduRover телеметрия → ИССГР (ground_vehicle)"
launch publisher "$VENV" "${SCRIPT_DIR}/rover_to_issgr_publisher.py" \
    --mavlink "tcp:127.0.0.1:${ROVER_MAV_TELEM}" \
    --issgr-url "http://127.0.0.1:${ISSGR_PORT}" \
    --frame "$FRAME" --max-seconds "$((DUR + 15))"

echo "==> [4/5] Admin витрина :${ADMIN_PORT} (двойник — машина на карте БАС)"
launch admin "$VENV" "${SCRIPT_DIR}/admin_web_server.py" \
    --host 127.0.0.1 --port "$ADMIN_PORT" \
    --issgr-url "http://127.0.0.1:${ISSGR_PORT}"
wait_port "$ADMIN_PORT" "Admin"

echo "==> [5/5] ручное вождение машины (${DUR}s)"
launch drive "$VENV" "${SCRIPT_DIR}/rover_manual_drive.py" \
    --mavlink "tcp:127.0.0.1:${ROVER_MAV}" --seconds "$DUR"

if [ "$NO_BROWSER" = "0" ] && command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe Start-Process "http://127.0.0.1:${ADMIN_PORT}/" >/dev/null 2>&1 || true
fi

echo; echo "  Live: машина едет и двигается в ИССГР. KPI каждые 5s:"
T0=$(date +%s)
while [ "$(( $(date +%s) - T0 ))" -lt "$DUR" ]; do
    INFO=$(curl -sf "http://127.0.0.1:${ISSGR_PORT}/collections/uavs/items?limit=20" 2>/dev/null \
        | "$VENV" -c "import json,sys
d=json.load(sys.stdin)
gv=[f for f in d.get('features',[]) if 'ground_vehicle' in (f.get('properties') or {}).get('issgr_class','')]
if gv:
    p=gv[0]['properties']; c=gv[0]['geometry']['coordinates']
    print(f\"машина: ({c[1]:.6f},{c[0]:.6f}) {p.get('flight_mode')} armed={p.get('armed')}\")
else:
    print('машина ещё не в ИССГР...')" 2>/dev/null || echo "ИССГР poll fail")
    printf "    [t+%3ds] %s\n" "$(( $(date +%s) - T0 ))" "$INFO"
    sleep 5
done

echo; echo "=========================================================================="
echo "  Готово. Наземный транспорт отъездил и был в цифровом двойнике."
echo "  Логи: ${LOG_DIR}/"
echo "=========================================================================="
