#!/usr/bin/env bash
# ============================================================================
#  BAS GRAND DEMO — backend-стек + полётный стек + ВСЕ 6 интеграций живьём.
# ============================================================================
# Один скрипт (без sudo, demo-режим) поднимает сразу:
#
#  BACKEND (как master demo):
#    • ИССГР node-A (:8770) + node-B (:8771) + multicast sync (pub/sub, :8811)
#    • OnBoardDB SQLite (time-series + composite метрики)
#
#  ПОЛЁТНЫЙ СТЕК + UI:
#    • Web GCS пульт (:8765, demo+RF, urban-препятствия)
#    • airsim_mjpeg_server (:8767) → камера AirSim/заглушка в окне FPV пульта
#    • gcs_to_issgr_publisher → живой UAV в двойнике
#    • cyber_defense_monitor → алерты в общий файл
#    • Admin витрина (:8810) — управление + двойник + детекты + баннер атаки
#
#  ЖИВАЯ ДЕМОНСТРАЦИЯ ВСЕХ ИНТЕГРАЦИЙ (по таймеру):
#    A — ARM + клик-goto из витрины → дрон летит (двойник двигается)
#    B — goto сквозь препятствие → борт сам облетает (geofence reroute)
#    C — CV-детекты с борта → метки на карте пульта И витрины
#    D — кибер-атака → красный баннер на пульте И витрине
#    E — RF-покрытие (кнопка 📶 на пульте) — тени за зданиями
#    E2 — камера AirSim в окне FPV пульта
#
# Запуск:
#   bash scripts/run_grand_demo.sh                 # all-in-one + браузер
#   bash scripts/run_grand_demo.sh --no-browser    # без авто-открытия
#   BAS_DEMO_DURATION=240 bash scripts/run_grand_demo.sh
# ----------------------------------------------------------------------------
set -uo pipefail   # НЕ -e: один упавший модуль не должен валить весь demo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV="${REPO_ROOT}/.venv/bin/python"
[ -x "$VENV" ] || { echo "venv missing at $VENV"; exit 1; }

BAS_DEMO_DURATION="${BAS_DEMO_DURATION:-150}"
NO_BROWSER=0
for arg in "$@"; do [ "$arg" = "--no-browser" ] && NO_BROWSER=1; done

# Ports.
ISSGR_A=8770; ISSGR_B=8771; PULT=8765; FPV=8767; ADMIN=8810
SYNC_STATS=8811; CYBER_MAV=14559
MCAST_GROUP=239.10.10.10; MCAST_PORT=5500

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/grand_demo_${TS}"
mkdir -p "${LOG_DIR}/cyber"

# Общий файл кибер-алертов: монитор пишет, пульт+витрина читают (integration D).
export BAS_CYBER_ALERTS="${LOG_DIR}/cyber/alerts.jsonl"
: > "$BAS_CYBER_ALERTS"
CYBER_CHAN="${LOG_DIR}/cyber/channel.json"
echo '{"loss_ratio":0.01,"rssi_dbm":-60.0,"extra_delay_ms":5}' > "$CYBER_CHAN"
# Urban-препятствия → пульт рисует geofence (B) + RF heatmap (E) на 8 зданиях.
export BAS_RF_OBSTACLE_PROFILE=urban
export BAS_GCS_UI_GOTO_SPEED=6
ONBOARD_DB="${LOG_DIR}/onboard.db"

PIDS=()
cleanup() {
    echo; echo "[grand] cleanup..."
    for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
    wait 2>/dev/null || true
    echo "[grand] all subprocess killed; logs at ${LOG_DIR}"
}
trap cleanup EXIT INT TERM

launch() {
    local label="$1"; shift
    "$@" >"${LOG_DIR}/${label}.log" 2>&1 &
    local pid=$!
    PIDS+=("$pid")
    printf "  [+] %-22s pid=%-6d  log=%s.log\n" "$label" "$pid" "$label"
}

wait_port() {
    local port="$1"; local label="$2"; local timeout="${3:-15}"
    for ((i=0; i<timeout*10; i++)); do
        if "$VENV" -c "import socket; s=socket.socket(); s.settimeout(0.1); s.connect(('127.0.0.1',$port)); s.close()" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    echo "  [!] port $port ($label) NOT ready за ${timeout}s — продолжаем"
    return 1
}

# Pre-clean занятые порты от прошлых прогонов.
for p in "$ISSGR_A" "$ISSGR_B" "$PULT" "$FPV" "$ADMIN" "$SYNC_STATS"; do
    fuser -k "${p}/tcp" 2>/dev/null
done
fuser -k "${CYBER_MAV}/udp" 2>/dev/null
sleep 1

cat <<EOF

==========================================================================
  BAS GRAND DEMO — backend + полётный стек + ВСЕ 6 интеграций
==========================================================================
  Run ID:    grand_demo_${TS}
  Duration:  ${BAS_DEMO_DURATION}s
  Logs:      ${LOG_DIR}
==========================================================================
EOF

# --- BACKEND ---------------------------------------------------------------
echo; echo "==> BACKEND: ИССГР двойник + sync + onboard"
launch issgr_a "$VENV" "${SCRIPT_DIR}/issgr_api_server.py" --port "$ISSGR_A" --seed-profile urban
launch issgr_b "$VENV" "${SCRIPT_DIR}/issgr_api_server.py" --port "$ISSGR_B" --no-seed
wait_port "$ISSGR_A" "ИССГР-A"; wait_port "$ISSGR_B" "ИССГР-B"
# BAS_GRAND_LIGHT=1 — пропустить multicast sync + onboard (меньше нагрузки,
# напр. для записи видео). UI пульта/витрины от этого не зависит.
if [ "${BAS_GRAND_LIGHT:-0}" = "1" ]; then
    echo "  (light mode: пропускаю sync + onboard)"
else
    launch sync_pub "$VENV" "${SCRIPT_DIR}/issgr_sync_publisher.py" \
        --issgr-url "http://127.0.0.1:${ISSGR_A}" --group "$MCAST_GROUP" --port "$MCAST_PORT" \
        --ttl 1 --interval 1.0 --node-id grand-pub --stats-port "$SYNC_STATS" \
        --max-seconds "$BAS_DEMO_DURATION"
    launch sync_sub "$VENV" "${SCRIPT_DIR}/issgr_sync_subscriber.py" \
        --issgr-url "http://127.0.0.1:${ISSGR_B}" --group "$MCAST_GROUP" --port "$MCAST_PORT" \
        --max-seconds "$BAS_DEMO_DURATION"
    launch onboard "$VENV" "${SCRIPT_DIR}/issgr_onboard_demo.py" \
        --db-path "$ONBOARD_DB" --synth-hz 5 --max-seconds "$BAS_DEMO_DURATION"
fi

# --- ПОЛЁТНЫЙ СТЕК + UI -----------------------------------------------------
echo; echo "==> ПОЛЁТ + UI: AirSim FPV + пульт + publisher + cyber + витрина"
launch airsim_mjpeg "$VENV" "${SCRIPT_DIR}/airsim_mjpeg_server.py" \
    --host 127.0.0.1 --port "$FPV" --fps 10

# Пульт: demo+RF, urban, ИССГР (для CV-слоя), FPV upstream → AirSim MJPEG,
# BAS_CYBER_ALERTS (для баннера) наследуется из export выше.
BAS_FPV_UPSTREAM_HOST=127.0.0.1 BAS_FPV_UPSTREAM_PORT="$FPV" \
launch pult "$VENV" "${SCRIPT_DIR}/gcs_web_ui_server.py" \
    --demo --rf-demo --host 127.0.0.1 --port "$PULT" \
    --issgr-url "http://127.0.0.1:${ISSGR_A}"
wait_port "$PULT" "Пульт"

launch publisher "$VENV" "${SCRIPT_DIR}/gcs_to_issgr_publisher.py" \
    --gcs-url "http://127.0.0.1:${PULT}" --issgr-url "http://127.0.0.1:${ISSGR_A}" \
    --period-s 0.4

launch cyber "$VENV" "${SCRIPT_DIR}/cyber_defense_monitor.py" \
    --mavlink-port "$CYBER_MAV" --channel-file "$CYBER_CHAN" \
    --log-file "$BAS_CYBER_ALERTS" --jump-window-ms 2500 \
    --max-seconds "$BAS_DEMO_DURATION"

launch admin "$VENV" "${SCRIPT_DIR}/admin_web_server.py" \
    --host 127.0.0.1 --port "$ADMIN" \
    --issgr-url "http://127.0.0.1:${ISSGR_A}" \
    --gcs-url "http://127.0.0.1:${PULT}" \
    --onboard-db "$ONBOARD_DB" \
    --sync-stats-url "http://127.0.0.1:${SYNC_STATS}/stats"
wait_port "$ADMIN" "Витрина"

# --- ЖИВАЯ ДЕМОНСТРАЦИЯ ИНТЕГРАЦИЙ (фоном, по таймеру) ----------------------
(
    cj() { curl -s -X POST -H 'Content-Type: application/json' -d "$2" "$1" >/dev/null 2>&1; }
    sleep 4
    echo "[demo] A+B: ARM + goto(0,75) сквозь Hangar → облёт (цель в коридоре)" >> "${LOG_DIR}/demo_actions.log"
    cj "http://127.0.0.1:${PULT}/api/command" '{"action":"arm"}'
    sleep 1
    cj "http://127.0.0.1:${PULT}/api/goto" '{"north":0,"east":75,"altitude":15}'

    sleep 6
    echo "[demo] C: inject CV-детекты в ИССГР" >> "${LOG_DIR}/demo_actions.log"
    BAS_ISSGR_URL="http://127.0.0.1:${ISSGR_A}" "$VENV" - <<'PYEOF' >> "${LOG_DIR}/demo_actions.log" 2>&1
import json, math, os, urllib.request, uuid
ISS = os.environ["BAS_ISSGR_URL"]; OLAT, OLON = -35.363262, 149.165237
def ll(n, e):
    return OLAT + n/111319.9, OLON + e/(111319.9*math.cos(math.radians(OLAT)))
for cls, conf, n, e in [("person",0.9,25,18),("car",0.82,-14,30),("truck",0.74,40,-16),("person",0.66,8,-22)]:
    glat, glon = ll(n, e)
    p = {"id":{"domain":"cv-detector","system":"yolov8n","object_uuid":str(uuid.uuid4())},
         "name":f"CV:{cls}","issgr_class":"functional_objects.sensor.ground_station",
         "source_uav_id":{"domain":"bas","system":"fizulin-rig","object_uuid":"00000000-0000-0000-0000-000000000100"},
         "sensor_type":"camera_object_detection",
         "value":{"class_name":cls,"issgr_class_guess":"operational_situation.target.point",
                  "confidence":conf,"bbox":[10,10,60,80],"ground_lat":glat,"ground_lon":glon},
         "pose_at_observation":{"latitude_deg":OLAT,"longitude_deg":OLON,"altitude_m":20.0,"heading_deg":0.0}}
    urllib.request.urlopen(urllib.request.Request(ISS+"/collections/sensor_readings/items",
        data=json.dumps(p).encode(), headers={"Content-Type":"application/json"}, method="POST"), timeout=3).read()
print("injected 4 CV detections")
PYEOF

    sleep 18
    echo "[demo] D: кибер-атака (baseline → GPS spoof + cmd inject)" >> "${LOG_DIR}/demo_actions.log"
    BAS_GD_PORT="$CYBER_MAV" BAS_GD_SCRIPTS="$SCRIPT_DIR" "$VENV" - <<'PYEOF' >> "${LOG_DIR}/demo_actions.log" 2>&1
import os, socket, sys
sys.path.insert(0, os.environ["BAS_GD_SCRIPTS"])
from cyber_attack_simulator import _mavlink_v2_frame, _pack_global_position_int, MSG_GLOBAL_POSITION_INT
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
p = _pack_global_position_int(0, -35.363262, 149.165237, 50.0, 0, 0, 0, 0)
s.sendto(_mavlink_v2_frame(1, 1, MSG_GLOBAL_POSITION_INT, p), ("127.0.0.1", int(os.environ["BAS_GD_PORT"])))
s.close(); print("baseline sent")
PYEOF
    sleep 1
    "$VENV" "${SCRIPT_DIR}/cyber_attack_simulator.py" gps_spoof \
        --target-port "$CYBER_MAV" --offset-lat-m 220 --offset-lon-m 220 --duration-s 3 \
        >> "${LOG_DIR}/demo_actions.log" 2>&1
    "$VENV" "${SCRIPT_DIR}/cyber_attack_simulator.py" cmd_inject \
        --target-port "$CYBER_MAV" --fake-sysid 254 --n 3 \
        >> "${LOG_DIR}/demo_actions.log" 2>&1
) &
PIDS+=("$!")

# --- Browser auto-open ------------------------------------------------------
if [ "$NO_BROWSER" = "0" ] && command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe Start-Process "http://127.0.0.1:${ADMIN}/" >/dev/null 2>&1 || true
    powershell.exe Start-Process "http://127.0.0.1:${PULT}/" >/dev/null 2>&1 || true
    echo "  [+] открыл витрину :${ADMIN} и пульт :${PULT} в браузере"
fi

cat <<ROUTE

==========================================================================
  ВСЁ ПОДНЯТО. Открой в браузере (стенд жив ${BAS_DEMO_DURATION}s):
--------------------------------------------------------------------------
  🕹  ПУЛЬТ:    http://127.0.0.1:${PULT}/
        → RF-покрытие (📶, E) · geofence-зоны (B) · CV-метки (C) ·
          окно FPV = камера AirSim (E2) · баннер атаки (D)
  🗺  ВИТРИНА:  http://127.0.0.1:${ADMIN}/   вкладка «БАС»
        → управление дроном (A) · двойник едет (A) · CV-метки (C) ·
          geofence-зоны (B) · баннер атаки (D); вкладка «Синхронизация» (sync)
  ──────────────────────────────────────────────────────────────────────
  ИССГР Swagger:  http://127.0.0.1:${ISSGR_A}/docs
  Sync stats:     http://127.0.0.1:${SYNC_STATS}/stats

  Сценарий идёт сам: ARM+облёт (~t4) → CV-детекты (~t11) → кибер-атака (~t30).
  RF-покрытие включается кнопкой 📶 на карте пульта.
==========================================================================

  Live KPIs (каждые 10s):
ROUTE

# --- KPI loop --------------------------------------------------------------
status() {
    local t="$1" uavs dets alerts mode
    uavs=$(curl -sf "http://127.0.0.1:${ISSGR_A}/collections/uavs/items?limit=50" 2>/dev/null \
        | "$VENV" -c "import json,sys;print(len(json.load(sys.stdin).get('features',[])))" 2>/dev/null || echo "?")
    dets=$(curl -sf "http://127.0.0.1:${ISSGR_A}/collections/sensor_readings/items?limit=500" 2>/dev/null \
        | "$VENV" -c "import json,sys;d=json.load(sys.stdin);print(sum(1 for f in d.get('features',[]) if (f.get('properties') or {}).get('sensor_type')=='camera_object_detection'))" 2>/dev/null || echo "?")
    alerts=$(curl -sf "http://127.0.0.1:${ADMIN}/api/admin/alerts" 2>/dev/null \
        | "$VENV" -c "import json,sys;print(json.load(sys.stdin).get('count','?'))" 2>/dev/null || echo "?")
    mode=$(curl -sf "http://127.0.0.1:${PULT}/api/state" 2>/dev/null \
        | "$VENV" -c "import json,sys;d=json.load(sys.stdin);print((d.get('goto_status') or {}).get('state','-'),'|',d.get('current_mode','-'))" 2>/dev/null || echo "?")
    printf "    [t+%3ds] двойник_uavs=%s CV-детекты=%s cyber_alerts=%s | goto/режим: %s\n" \
        "$t" "$uavs" "$dets" "$alerts" "$mode"
}
T0=$(date +%s)
while true; do
    EL=$(( $(date +%s) - T0 ))
    [ "$EL" -ge "$BAS_DEMO_DURATION" ] && break
    status "$EL"
    sleep 10
done
status "$BAS_DEMO_DURATION"

echo
echo "=========================================================================="
echo "  Grand demo complete. Logs: ${LOG_DIR}/"
echo "=========================================================================="
