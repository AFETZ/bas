#!/usr/bin/env bash
# Run the real Stage 2.4 RT-online route and build packet-level RSSI audit artifacts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${BAS_RUN_ID:-stage24_route_rssi_packet_audit_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"

export BAS_RUN_ID="$RUN_ID"
export BAS_AUTO_DEMO_STACK="${BAS_AUTO_DEMO_STACK:-run_stage_2_4_rt_online_demo.sh}"
export BAS_STAGE24_PACKET_AUDIT=1
export BAS_STAGE24_PACKET_AUDIT_SEED="${BAS_STAGE24_PACKET_AUDIT_SEED:-1337}"
export BAS_STAGE24_PACKET_AUDIT_CSV="${BAS_STAGE24_PACKET_AUDIT_CSV:-${LOG_DIR}/stage24_route_rssi_packets_raw.csv}"
export BAS_RT_HISTORY_PATH="${BAS_RT_HISTORY_PATH:-${LOG_DIR}/sionna_rt_history.jsonl}"
export BAS_SIONNA_RT_ONLINE=1
export BAS_SIONNA_TARGET_FLOW="${BAS_SIONNA_TARGET_FLOW:-both}"
export BAS_SIONNA_REQUIRE_GPU="${BAS_SIONNA_REQUIRE_GPU:-1}"
export BAS_MITSUBA_VARIANT="${BAS_MITSUBA_VARIANT:-cuda_ad_mono_polarized}"
export MITSUBA_VARIANT="$BAS_MITSUBA_VARIANT"
export BAS_REQUIRE_NS3_PAYLOAD="${BAS_REQUIRE_NS3_PAYLOAD:-1}"
export BAS_PAYLOAD_BYPASS="${BAS_PAYLOAD_BYPASS:-0}"
export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-0}"
export NS3_DURATION="${NS3_DURATION:-480}"

mkdir -p "$LOG_DIR"

echo "==> RUN_ID=${RUN_ID}"
echo "==> LOG_DIR=${LOG_DIR}"
echo "==> stack=${BAS_AUTO_DEMO_STACK}"
echo "==> Sionna target flow=${BAS_SIONNA_TARGET_FLOW}"
echo "==> Mitsuba variant=${BAS_MITSUBA_VARIANT}"
echo "==> require ns-3 payload=${BAS_REQUIRE_NS3_PAYLOAD}"
echo "==> packet audit raw=${BAS_STAGE24_PACKET_AUDIT_CSV}"

set +e
bash "${REPO_ROOT}/scripts/run_stage_2_4_auto_demo.sh"
RUN_RC=$?
set -e

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/analyze_stage24_route_rssi_packet_audit.py" \
    --run-dir "$LOG_DIR" \
    --packet-csv "$BAS_STAGE24_PACKET_AUDIT_CSV" \
    --sionna-history "$BAS_RT_HISTORY_PATH" \
    --command "BAS_RUN_ID=${RUN_ID} BAS_AUTO_DEMO_STACK=${BAS_AUTO_DEMO_STACK} BAS_STAGE24_PACKET_AUDIT=1 BAS_SIONNA_RT_ONLINE=1 BAS_SIONNA_TARGET_FLOW=${BAS_SIONNA_TARGET_FLOW} BAS_REQUIRE_NS3_PAYLOAD=${BAS_REQUIRE_NS3_PAYLOAD} BAS_SIONNA_REQUIRE_GPU=${BAS_SIONNA_REQUIRE_GPU} BAS_MITSUBA_VARIANT=${BAS_MITSUBA_VARIANT} bash scripts/run_stage24_route_rssi_packet_audit.sh"

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/analyze_stage24_payload_path.py" \
    --run-dir "$LOG_DIR" \
    --packet-csv "$BAS_STAGE24_PACKET_AUDIT_CSV"

echo
echo "==> route audit run rc=${RUN_RC}"
echo "==> raw packets: ${BAS_STAGE24_PACKET_AUDIT_CSV}"
echo "==> processed packets: ${REPO_ROOT}/data/processed/stage24_route_rssi_packets.csv"
echo "==> bins: ${REPO_ROOT}/data/processed/stage24_route_rssi_bins.csv"
echo "==> figures: ${REPO_ROOT}/figures/stage24_route_rssi_{packet_trace,map,loss_bins}.{png,pdf,svg}"
echo "==> reports: ${REPO_ROOT}/reports/stage24_route_rssi_packet_{audit,summary}.md"
echo "==> payload reports: ${REPO_ROOT}/reports/stage24_payload_{path_audit,metrics_summary}.md"

exit "$RUN_RC"
