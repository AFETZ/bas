#!/usr/bin/env bash
# Stage 3 ИССГР Multicast Sync demo — two-node simulation.
#
# Поднимает два ИССГР API instances на разных портах (8770 = "node-A",
# 8771 = "node-B"), publisher на node-A → multicast → subscriber на node-B
# → POST в local ИССГР API node-B. Это имитирует sync БД между двумя АСУ
# узлами через multicast UDP.
#
# В background:
#   ISSGR node-A (port 8770)  — source-of-truth, sysid=1 UAV
#   ISSGR node-B (port 8771)  — replica, начинается пустым
#   sync_publisher    → multicast 239.10.10.10:5500 каждую секунду
#   sync_subscriber   → POST к node-B на каждый received packet
#
# После 30с node-B должен иметь обновлённый UAV state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PORT_A="${BAS_ISSGR_NODE_A_PORT:-8770}"
PORT_B="${BAS_ISSGR_NODE_B_PORT:-8771}"
MCAST_GROUP="${BAS_ISSGR_MCAST_GROUP:-239.10.10.10}"
MCAST_PORT="${BAS_ISSGR_MCAST_PORT:-5500}"
SYNC_INTERVAL="${BAS_ISSGR_SYNC_INTERVAL:-1.0}"
DURATION="${BAS_ISSGR_SYNC_DURATION:-45}"

BAS_RUN_ID="${BAS_RUN_ID:-stage_3_issgr_sync_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${BAS_RUN_ID}"
mkdir -p "$LOG_DIR"

PIDS=()
cleanup() {
    echo "[sync-demo] cleanup"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

VENV="${REPO_ROOT}/.venv/bin/python"
[ -x "$VENV" ] || { echo "venv missing"; exit 1; }

echo "==> RUN_ID=${BAS_RUN_ID}"
echo "==> Multicast: ${MCAST_GROUP}:${MCAST_PORT}"
echo "==> Node A (source): http://127.0.0.1:${PORT_A}"
echo "==> Node B (replica): http://127.0.0.1:${PORT_B}"
echo "==> Sync interval: ${SYNC_INTERVAL}s, duration ${DURATION}s"

# 1. ISSGR node-A (source)
"$VENV" "${SCRIPT_DIR}/issgr_api_server.py" --port "$PORT_A" \
    > "${LOG_DIR}/node_a.log" 2>&1 &
PIDS+=($!)
echo "  node-A pid=${PIDS[-1]}"

# 2. ISSGR node-B (replica, без seed чтобы видеть как наполняется через sync)
"$VENV" "${SCRIPT_DIR}/issgr_api_server.py" --port "$PORT_B" --no-seed \
    > "${LOG_DIR}/node_b.log" 2>&1 &
PIDS+=($!)
echo "  node-B pid=${PIDS[-1]}"

sleep 4

# 3. Seed node-A с одним UAV для демонстрации position L1.
echo "[sync-demo] seeding node-A с одним UAV..."
"$VENV" - <<EOF
import json, urllib.request, uuid
url = "http://127.0.0.1:${PORT_A}/collections/uavs/items"
payload = {
    "id": {"domain": "bas", "system": "fizulin-rig",
           "object_uuid": "00000000-0000-0000-0000-000000000100"},
    "name": "Iris-Sync-Demo", "sysid": 1,
    "issgr_class": "operational_situation.uav.rotary_wing",
    "pose": {
        "latitude_deg": -35.363262, "longitude_deg": 149.165237,
        "altitude_m": 15.0, "heading_deg": 90.0,
    },
    "armed": True, "flight_mode": "AUTO",
    "battery_v": 12.4, "velocity_ned": [3.0, 0.5, 0.0],
}
req = urllib.request.Request(
    url, data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
print(urllib.request.urlopen(req, timeout=3.0).read().decode())
EOF

# 4. Publisher subscribed на node-A.
"$VENV" "${SCRIPT_DIR}/issgr_sync_publisher.py" \
    --issgr-url "http://127.0.0.1:${PORT_A}" \
    --group "$MCAST_GROUP" --port "$MCAST_PORT" \
    --interval "$SYNC_INTERVAL" \
    --max-seconds "$DURATION" \
    --log-file "${LOG_DIR}/pub_packets.tsv" \
    > "${LOG_DIR}/publisher.log" 2>&1 &
PIDS+=($!)
echo "  publisher pid=${PIDS[-1]} (node-A → multicast)"

# 5. Subscriber → POST в node-B.
"$VENV" "${SCRIPT_DIR}/issgr_sync_subscriber.py" \
    --issgr-url "http://127.0.0.1:${PORT_B}" \
    --group "$MCAST_GROUP" --port "$MCAST_PORT" \
    --max-seconds "$DURATION" \
    --log-file "${LOG_DIR}/sub_packets.tsv" \
    > "${LOG_DIR}/subscriber.log" 2>&1 &
PIDS+=($!)
echo "  subscriber pid=${PIDS[-1]} (multicast → node-B POST)"

cat <<INFO

==========================================================================
 ИССГР Multicast Sync demo (длится ${DURATION}с)
--------------------------------------------------------------------------
 Артефакты в ${LOG_DIR}/:
   node_a.log, node_b.log          — ISSGR API stdout каждого узла
   publisher.log, subscriber.log    — sync процессы
   pub_packets.tsv, sub_packets.tsv — каждый packet (hex + decoded fields)

 Live проверки:
   curl -s http://127.0.0.1:${PORT_A}/stats   # source (должен быть UAV)
   curl -s http://127.0.0.1:${PORT_B}/stats   # replica (наполняется через sync)
   curl -s http://127.0.0.1:${PORT_B}/collections/uavs/items | jq .
==========================================================================

INFO

# Wait for publisher to finish.
wait "${PIDS[2]}" 2>/dev/null || true

sleep 2

echo
echo "==> Final state:"
echo "--- node-A stats ---"
curl -s "http://127.0.0.1:${PORT_A}/stats" || true
echo
echo "--- node-B stats ---"
curl -s "http://127.0.0.1:${PORT_B}/stats" || true
echo
echo
echo "--- node-B synced UAVs (через multicast) ---"
curl -s "http://127.0.0.1:${PORT_B}/collections/uavs/items" \
    | "$VENV" -c "
import sys, json
d = json.load(sys.stdin)
print(f'n={d[\"numberReturned\"]}')
for f in d['features'][:5]:
    p = f['properties']
    g = f['geometry']
    print(f'  - {p[\"name\"]:25s}  sysid={p[\"sysid\"]}  alt={p[\"altitude_m\"]:5.1f}м  '
          f'pos=({g[\"coordinates\"][0]:.5f},{g[\"coordinates\"][1]:.5f})  '
          f'sync_source={p[\"properties\"].get(\"sync_source\",\"(direct)\")}')
" 2>&1 || true
echo
echo "Log files:"
echo "  ${LOG_DIR}/publisher.log"
echo "  ${LOG_DIR}/subscriber.log"
echo "  ${LOG_DIR}/pub_packets.tsv (lines: $(wc -l < ${LOG_DIR}/pub_packets.tsv 2>/dev/null || echo 0))"
echo "  ${LOG_DIR}/sub_packets.tsv (lines: $(wc -l < ${LOG_DIR}/sub_packets.tsv 2>/dev/null || echo 0))"
