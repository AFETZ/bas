#!/usr/bin/env bash
# BAS Master Demo — поднимает ВСЕ работающие модули и открывает живой GUI.
#
# Что включает:
#   1. ИССГР API server (OGC API Features 1.0) на :8770
#   2. ИССГР second node (для multicast sync demo) на :8771
#   3. OnBoardDB SQLite persistence + composite engine
#   4. Multicast sync publisher (с /stats endpoint :8811)
#   5. Multicast sync subscriber (node-A → node-B replication)
#   6. AirSim stub server (msgpack-rpc :41451)
#   7. AirSim scene populator (26 obstacles spawn'ed)
#   8. ArduPilot↔AirSim JsonFdmBridge (real X-config physics)
#   9. Synthetic SITL emulator (drives bridge with PWM frames)
#   10. CV detector OPTIONAL (if FPV stream exists, posts to ИССГР)
#   11. Cyber defense monitor (live MAVLink + RF watch)
#   12. Sionna real-tile pre-compute showcase
#   13. Admin Dashboard на :8810 (live tabs: Overview / ИССГР /
#       Multi-UAV / OnBoard / Tile Map / Sync) — открывает в браузере
#   14. Web GCS UI на :8765 — если установлен Gazebo/MAVProxy stack
#
# Каждый модуль имеет HEALTH check; неудачный module logs warning
# но не блокирует demo. Все логи в logs/master_demo_<ts>/.
#
# Запуск:
#   bash scripts/run_master_demo.sh                     # all-in-one
#   bash scripts/run_master_demo.sh --no-browser        # CI / SSH
#   BAS_DEMO_DURATION=120 bash scripts/run_master_demo.sh  # 2 минуты
#
# Графический UI:
#   - http://127.0.0.1:8810/  — Admin Dashboard (главный экран)
#   - http://127.0.0.1:8770/docs — ИССГР Swagger UI
#   - http://127.0.0.1:8811/stats — Live sync publisher stats
#
# Cleanup: Ctrl+C → kill всех subprocess через trap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV="${REPO_ROOT}/.venv/bin/python"

# --- Config ---
BAS_DEMO_DURATION="${BAS_DEMO_DURATION:-180}"
BAS_DEMO_NO_BROWSER=0
for arg in "$@"; do
    case "$arg" in
        --no-browser) BAS_DEMO_NO_BROWSER=1 ;;
    esac
done

# Ports.
ISSGR_PORT_A=8770
ISSGR_PORT_B=8771
MCAST_GROUP=239.10.10.10
MCAST_PORT=5500
SYNC_STATS_PORT=8811
AIRSIM_PORT=41451
ADMIN_PORT=8810
JSONFDM_IN=9003
JSONFDM_OUT=9002

# Logs.
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${REPO_ROOT}/logs/master_demo_${TS}"
mkdir -p "$LOG_DIR"

# PID tracking + cleanup.
PIDS=()
cleanup() {
    echo
    echo "[master] cleanup..."
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "[master] all subprocess killed; logs at ${LOG_DIR}"
}
trap cleanup EXIT INT TERM

# --- Banner ---
cat <<EOF

==========================================================================
  BAS Master Demo — all modules, live integration
==========================================================================
  Run ID:        master_demo_${TS}
  Duration:      ${BAS_DEMO_DURATION}s (BAS_DEMO_DURATION to override)
  Logs:          ${LOG_DIR}
  Browser:       $([ "$BAS_DEMO_NO_BROWSER" = "1" ] && echo "DISABLED" || echo "ENABLED")

  Endpoints:
    ИССГР node-A:    http://127.0.0.1:${ISSGR_PORT_A}/docs
    ИССГР node-B:    http://127.0.0.1:${ISSGR_PORT_B}/docs
    Multicast:       ${MCAST_GROUP}:${MCAST_PORT}
    Sync stats:      http://127.0.0.1:${SYNC_STATS_PORT}/stats
    AirSim stub:     msgpack-rpc :${AIRSIM_PORT}
    JSON-FDM:        UDP :${JSONFDM_IN} (PWM) :${JSONFDM_OUT} (sensor)
    Admin Dashboard: http://127.0.0.1:${ADMIN_PORT}/   ← открыть в браузере

==========================================================================
EOF

[ -x "$VENV" ] || { echo "venv missing at $VENV"; exit 1; }

# --- Helper: launch + add to PIDS + log ---
launch() {
    local label="$1"; shift
    local logfile="${LOG_DIR}/${label}.log"
    "$@" >"$logfile" 2>&1 &
    local pid=$!
    PIDS+=("$pid")
    printf "  [+] %-30s pid=%-6d  log=%s\n" "$label" "$pid" "$(basename "$logfile")"
}

wait_port() {
    local port="$1"; local label="$2"; local timeout="${3:-10}"
    for ((i=0; i<timeout*10; i++)); do
        if "$VENV" -c "import socket,sys; s=socket.socket(); s.settimeout(0.1); s.connect(('127.0.0.1',$port)); s.close()" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    echo "  [!] port $port ($label) NOT ready после ${timeout}s — продолжаем без него"
    return 1
}

# ---------------------------------------------------------------------------
echo
echo "==> [1/14] ИССГР node-A (source-of-truth) :${ISSGR_PORT_A}"
launch issgr_node_a \
    "$VENV" "${SCRIPT_DIR}/issgr_api_server.py" \
    --port "$ISSGR_PORT_A" --seed-profile urban

echo "==> [2/14] ИССГР node-B (replica для sync demo) :${ISSGR_PORT_B}"
launch issgr_node_b \
    "$VENV" "${SCRIPT_DIR}/issgr_api_server.py" \
    --port "$ISSGR_PORT_B" --no-seed

wait_port "$ISSGR_PORT_A" "ИССГР-A" 15
wait_port "$ISSGR_PORT_B" "ИССГР-B" 15

# Seed node-A с одним движущимся UAV.
echo "==> seeding node-A с тестовым UAV (sysid=1)..."
"$VENV" - <<EOF >>"${LOG_DIR}/seed_uav.log" 2>&1 || true
import json, urllib.request
url = "http://127.0.0.1:${ISSGR_PORT_A}/collections/uavs/items"
payload = {
    "id": {"domain": "bas", "system": "master-demo",
           "object_uuid": "00000000-0000-0000-0000-000000000001"},
    "name": "Iris-Master-1", "sysid": 1,
    "issgr_class": "operational_situation.uav.rotary_wing",
    "pose": {"latitude_deg": -35.363262, "longitude_deg": 149.165237,
             "altitude_m": 25.0, "heading_deg": 90.0},
    "armed": True, "flight_mode": "AUTO",
    "battery_v": 12.4,
}
urllib.request.urlopen(
    urllib.request.Request(url, data=json.dumps(payload).encode(),
                           headers={"Content-Type": "application/json"},
                           method="POST"), timeout=3).read()
print("UAV seeded")
EOF

# ---------------------------------------------------------------------------
ONBOARD_DB="${LOG_DIR}/onboard.db"
echo "==> [3/14] OnBoardDB SQLite + composite engine ($ONBOARD_DB)"
launch onboard_demo \
    "$VENV" "${SCRIPT_DIR}/issgr_onboard_demo.py" \
    --db-path "$ONBOARD_DB" --synth-hz 5 \
    --max-seconds "$BAS_DEMO_DURATION"

# ---------------------------------------------------------------------------
echo "==> [4/14] Multicast sync publisher (node-A → multicast) с /stats"
launch sync_publisher \
    "$VENV" "${SCRIPT_DIR}/issgr_sync_publisher.py" \
    --issgr-url "http://127.0.0.1:${ISSGR_PORT_A}" \
    --group "$MCAST_GROUP" --port "$MCAST_PORT" --ttl 1 \
    --interval 1.0 --node-id master-pub \
    --max-seconds "$BAS_DEMO_DURATION" \
    --stats-port "$SYNC_STATS_PORT" \
    --log-file "${LOG_DIR}/sync_publisher_packets.tsv"

echo "==> [5/14] Multicast sync subscriber (multicast → node-B POST)"
launch sync_subscriber \
    "$VENV" "${SCRIPT_DIR}/issgr_sync_subscriber.py" \
    --issgr-url "http://127.0.0.1:${ISSGR_PORT_B}" \
    --group "$MCAST_GROUP" --port "$MCAST_PORT" \
    --max-seconds "$BAS_DEMO_DURATION" \
    --log-file "${LOG_DIR}/sync_subscriber_packets.tsv"

wait_port "$SYNC_STATS_PORT" "sync-stats" 10

# ---------------------------------------------------------------------------
echo "==> [6/14] AirSim stub server (msgpack-rpc :${AIRSIM_PORT})"
launch airsim_stub \
    "$VENV" "${SCRIPT_DIR}/airsim_stub_server.py" \
    --port "$AIRSIM_PORT" \
    --pose-log "${LOG_DIR}/airsim_pose.jsonl" \
    --spawn-log "${LOG_DIR}/airsim_spawn.jsonl"

wait_port "$AIRSIM_PORT" "AirSim-stub" 10

# ---------------------------------------------------------------------------
echo "==> [7/14] AirSim scene populator (spawn 26 urban objects)"
"$VENV" "${SCRIPT_DIR}/airsim_scene_builder.py" \
    --populate --airsim-host 127.0.0.1 --airsim-port "$AIRSIM_PORT" \
    --spawn-log "${LOG_DIR}/scene_populate.jsonl" \
    >"${LOG_DIR}/scene_populate.log" 2>&1 || \
    echo "  [!] scene populate failed (см. scene_populate.log)"

# ---------------------------------------------------------------------------
echo "==> [8/14] ArduPilot↔AirSim JsonFdmBridge (real X-config 6DOF physics)"
launch jsonfdm_bridge \
    "$VENV" "${SCRIPT_DIR}/arducopter_airsim_interface.py" \
    --mode=json_fdm \
    --fdm-in-port "$JSONFDM_IN" --fdm-out-port "$JSONFDM_OUT" \
    --airsim-host 127.0.0.1 --airsim-port "$AIRSIM_PORT" \
    --log-file "${LOG_DIR}/jsonfdm_bridge.jsonl" \
    --max-seconds "$BAS_DEMO_DURATION"

# ---------------------------------------------------------------------------
echo "==> [9/14] Synthetic SITL emulator (PWM frames → JsonFdmBridge)"
launch sitl_emulator \
    "$VENV" - <<EOF
import socket, json, time, math, sys
sys.path.insert(0, "${SCRIPT_DIR}")
from multirotor_dynamics import MultirotorDynamics
hover = MultirotorDynamics().hover_pwm_estimate
pub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
fc = 0
t0 = time.time()
print(f"[sitl-emu] sending PWM @ ~50Hz to :${JSONFDM_IN}; hover_pwm={hover}", flush=True)
while time.time() - t0 < ${BAS_DEMO_DURATION}:
    t = time.time() - t0
    # Simple mission: take off (climb 2s) → cruise → yaw spin → hover.
    if t < 2.0:
        pwm = [hover + 400] * 4 + [1500] * 12
    elif t < 8.0:
        pwm = [hover + 50] * 4 + [1500] * 12   # cruise (slight climb)
    elif t < 12.0:
        pwm = [hover + 250, hover + 250, hover + 50, hover + 50] + [1500] * 12   # yaw
    else:
        pwm = [hover] * 4 + [1500] * 12   # hover
    packet = {"magic": 18458, "frame_rate": 50, "frame_count": fc, "pwm": pwm}
    pub.sendto((json.dumps(packet) + "\n").encode(), ("127.0.0.1", ${JSONFDM_IN}))
    fc += 1
    time.sleep(0.02)
print(f"[sitl-emu] done, {fc} frames sent", flush=True)
EOF

# ---------------------------------------------------------------------------
echo "==> [10/14] Cyber defense monitor (watch MAVLink + RF channel)"
mkdir -p "${LOG_DIR}/cyber"
# Integration D: общий путь алертов → витрина (Admin :8810) показывает красный
# баннер «КИБЕРАТАКА» когда монитор детектит запланированные атаки. export —
# чтобы admin_web_server (шаг 12) прочитал тот же файл через /api/admin/alerts.
export BAS_CYBER_ALERTS="${LOG_DIR}/cyber/defense_alerts.jsonl"
SIONNA_CHAN_FILE="${LOG_DIR}/sionna_channel.json"
echo '{"loss_ratio": 0.01, "rssi_dbm": -60.0, "extra_delay_ms": 5}' \
    > "$SIONNA_CHAN_FILE"
launch cyber_defense \
    "$VENV" "${SCRIPT_DIR}/cyber_defense_monitor.py" \
    --mavlink-port 14559 \
    --channel-file "$SIONNA_CHAN_FILE" \
    --log-file "$BAS_CYBER_ALERTS" \
    --jump-window-ms 2500 \
    --max-seconds "$BAS_DEMO_DURATION"

# Optional: schedule attack injection в середине demo для demonstration.
(
    sleep 30
    echo "[cyber] scheduled GPS spoof @ t+30s" >> "${LOG_DIR}/cyber/scheduled.log"
    "$VENV" "${SCRIPT_DIR}/cyber_attack_simulator.py" gps_spoof \
        --target-host 127.0.0.1 --target-port 14559 \
        --sysid 1 --offset-lat-m 250 --offset-lon-m 250 \
        --duration-s 3 >> "${LOG_DIR}/cyber/scheduled.log" 2>&1 || true
    sleep 5
    echo "[cyber] scheduled cmd injection @ t+38s" >> "${LOG_DIR}/cyber/scheduled.log"
    "$VENV" "${SCRIPT_DIR}/cyber_attack_simulator.py" cmd_inject \
        --target-host 127.0.0.1 --target-port 14559 \
        --fake-sysid 254 --n 3 >> "${LOG_DIR}/cyber/scheduled.log" 2>&1 || true
    sleep 5
    echo "[cyber] scheduled RF jam @ t+45s" >> "${LOG_DIR}/cyber/scheduled.log"
    "$VENV" "${SCRIPT_DIR}/cyber_attack_simulator.py" rf_jam \
        --channel-file "$SIONNA_CHAN_FILE" --duration-s 3 \
        --loss-ratio 0.92 --rssi-dbm -105 \
        >> "${LOG_DIR}/cyber/scheduled.log" 2>&1 || true
) &
PIDS+=($!)

# ---------------------------------------------------------------------------
echo "==> [11/14] Sionna RT showcase (live Munich city if GPU, else cached)"
mkdir -p "${LOG_DIR}/sionna_tiles"
SIONNA_OPTIX_LIB="/usr/lib/x86_64-linux-gnu/libnvoptix.so.595.71.05"
if [ -f "$SIONNA_OPTIX_LIB" ] && [ -x "${REPO_ROOT}/sionna_env/bin/python" ]; then
    echo "  live mode: real-time RT on built-in city scenes (Munich/Etoile)"
    for combo in "munich:0" "munich:5" "etoile:0"; do
        sc="${combo%:*}"; cars="${combo#*:}"
        bash "${SCRIPT_DIR}/run_sionna_live.sh" real_tile \
            --scene-name "$sc" --add-cars "$cars" \
            --cell-size-m 20 --freq-mhz 2400 --max-depth 2 \
            --output "${LOG_DIR}/sionna_tiles/live_${sc}_cars${cars}.json" \
            > "${LOG_DIR}/sionna_tiles/live_${sc}.log" 2>&1 || \
            echo "  [!] live ${sc} failed (см. log)"
    done
    echo "  [+] live Sionna scenes → ${LOG_DIR}/sionna_tiles/live_*.json"
else
    echo "  cached mode: slicing pre-computed iris_runway radio map (no GPU/OptiX)"
    for ij in "0,0" "2,4" "4,8" "1,12"; do
        i="${ij%,*}"; j="${ij#*,}"
        "$VENV" "${SCRIPT_DIR}/sionna_real_tile.py" \
            --mode cached --tile-i "$i" --tile-j "$j" --tile-size-m 50 \
            --output "${LOG_DIR}/sionna_tiles/tile_${i}_${j}.json" \
            > /dev/null 2>&1 || true
    done
    echo "  [+] 4 cached Sionna tiles → ${LOG_DIR}/sionna_tiles/"
fi

# ---------------------------------------------------------------------------
echo "==> [12/14] Admin Dashboard (live tabs) :${ADMIN_PORT}"
launch admin_dashboard \
    "$VENV" "${SCRIPT_DIR}/admin_web_server.py" \
    --host 127.0.0.1 --port "$ADMIN_PORT" \
    --issgr-url "http://127.0.0.1:${ISSGR_PORT_A}" \
    --onboard-db "$ONBOARD_DB" \
    --sync-stats-url "http://127.0.0.1:${SYNC_STATS_PORT}/stats"

wait_port "$ADMIN_PORT" "Admin Dashboard" 10

# ---------------------------------------------------------------------------
echo "==> [13/14] Browser auto-open"
if [ "$BAS_DEMO_NO_BROWSER" = "0" ] && command -v xdg-open >/dev/null 2>&1; then
    (xdg-open "http://127.0.0.1:${ADMIN_PORT}/" >/dev/null 2>&1) &
    PIDS+=($!)
    echo "  [+] xdg-open http://127.0.0.1:${ADMIN_PORT}/"
elif command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe Start-Process "http://127.0.0.1:${ADMIN_PORT}/" >/dev/null 2>&1 || true
    echo "  [+] (WSL) powershell.exe Start-Process http://127.0.0.1:${ADMIN_PORT}/"
else
    echo "  [!] no browser launcher (open manually: http://127.0.0.1:${ADMIN_PORT}/)"
fi

# ---------------------------------------------------------------------------
echo
echo "==> [14/14] All modules launched. Demo running ${BAS_DEMO_DURATION}s..."
echo
cat <<ROUTE
  ┌────────────────────────────────────────────────────────────────────┐
  │  МАРШРУТ ОСМОТРА (пока стенд жив — открывайте в браузере):          │
  ├────────────────────────────────────────────────────────────────────┤
  │  1. http://127.0.0.1:${ADMIN_PORT}/            Admin: вкладка «Обзор»      │
  │       → сколько модулей живо, endpoint health                       │
  │  2. http://127.0.0.1:${ADMIN_PORT}/  вкладки ИССГР / Бортовая БД / 20×20 │
  │       → объекты двойника, метрики борта, tile-карта                 │
  │  3. http://127.0.0.1:${ISSGR_PORT_A}/docs          ИССГР Swagger API        │
  │       → как АСУ-клиент читает обстановку                            │
  │  4. http://127.0.0.1:${SYNC_STATS_PORT}/stats          Multicast sync счётчики │
  │       → L1/L2/HEARTBEAT пакеты узел→узел                            │
  │  5. ${LOG_DIR}/sionna_tiles/
  │       → результат реального RT-расчёта (Munich live или cached)     │
  └────────────────────────────────────────────────────────────────────┘
  Что есть что — docs/MODULE_MAP.md · связки — docs/SCENARIOS.md

  Live KPIs (poll every 10s):
ROUTE
echo

print_status() {
    local t="$1"
    local n_uavs n_obstacles n_uavs_b onboard_rows sync_l1 sync_hb alerts
    n_uavs=$(curl -sf "http://127.0.0.1:${ISSGR_PORT_A}/collections/uavs/items?limit=100" 2>/dev/null \
        | "$VENV" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('features',[])))" 2>/dev/null || echo "?")
    n_obstacles=$(curl -sf "http://127.0.0.1:${ISSGR_PORT_A}/collections/obstacles/items?limit=100" 2>/dev/null \
        | "$VENV" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('features',[])))" 2>/dev/null || echo "?")
    n_uavs_b=$(curl -sf "http://127.0.0.1:${ISSGR_PORT_B}/collections/uavs/items?limit=100" 2>/dev/null \
        | "$VENV" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('features',[])))" 2>/dev/null || echo "?")
    sync_l1=$(curl -sf "http://127.0.0.1:${SYNC_STATS_PORT}/stats" 2>/dev/null \
        | "$VENV" -c "import json,sys; d=json.load(sys.stdin); print(d['totals']['L1'])" 2>/dev/null || echo "?")
    sync_hb=$(curl -sf "http://127.0.0.1:${SYNC_STATS_PORT}/stats" 2>/dev/null \
        | "$VENV" -c "import json,sys; d=json.load(sys.stdin); print(d['totals']['HEARTBEAT'])" 2>/dev/null || echo "?")
    if [ -f "$ONBOARD_DB" ]; then
        onboard_rows=$("$VENV" -c "import sqlite3; c=sqlite3.connect('$ONBOARD_DB'); print(sum(r[0] for r in c.execute('select count(*) from uav_state union all select count(*) from sensor_readings union all select count(*) from composite_state')))" 2>/dev/null || echo "?")
    else
        onboard_rows="?"
    fi
    if [ -f "${LOG_DIR}/cyber/defense_alerts.jsonl" ]; then
        alerts=$(wc -l < "${LOG_DIR}/cyber/defense_alerts.jsonl")
    else
        alerts=0
    fi
    printf "    [t+%3ds] node-A: uavs=%s obs=%s | node-B: uavs=%s | sync: HB=%s L1=%s | onboard_rows=%s | cyber_alerts=%s\n" \
        "$t" "$n_uavs" "$n_obstacles" "$n_uavs_b" "$sync_hb" "$sync_l1" "$onboard_rows" "$alerts"
}

T_START=$(date +%s)
while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - T_START))
    if [ "$ELAPSED" -ge "$BAS_DEMO_DURATION" ]; then
        break
    fi
    print_status "$ELAPSED"
    sleep 10
done

print_status "$BAS_DEMO_DURATION"

echo
echo "==========================================================================
  Demo complete. Artifacts:
    ${LOG_DIR}/
      ├── *.log                    (per-module stdout)
      ├── airsim_{pose,spawn}.jsonl
      ├── sync_{publisher,subscriber}_packets.tsv
      ├── onboard.db               (SQLite, persistent)
      ├── jsonfdm_bridge.jsonl
      ├── cyber/defense_alerts.jsonl
      ├── cyber/scheduled.log
      └── sionna_tiles/tile_*.json
  Inspect ИССГР state:
    curl http://127.0.0.1:${ISSGR_PORT_A}/digital_twin | jq
  Open dashboard later:
    ./scripts/admin_web_server.py --port ${ADMIN_PORT} \\
        --issgr-url http://127.0.0.1:${ISSGR_PORT_A} \\
        --onboard-db ${ONBOARD_DB}
=========================================================================="
