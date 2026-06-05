#!/usr/bin/env bash
# Run a Stage 2.4 RT-online RF-stress route selected by Sionna pre-scan.
#
# This is not the synthetic RSSI-target consistency check. Targets are used only
# to choose coordinates; packet decisions use live Sionna RSSI from the route.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${BAS_RUN_ID:-stage24_rf_stress_route_audit_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"

export BAS_RUN_ID="$RUN_ID"
export BAS_AUTO_DEMO_STACK="${BAS_AUTO_DEMO_STACK:-run_stage_2_4_rt_online_demo.sh}"
export BAS_AUTO_DEMO_DURATION_BUDGET_S="${BAS_AUTO_DEMO_DURATION_BUDGET_S:-2600}"
export BAS_STAGE24_PACKET_AUDIT=1
export BAS_STAGE24_PACKET_AUDIT_SEED="${BAS_STAGE24_PACKET_AUDIT_SEED:-1337}"
export BAS_STAGE24_PACKET_AUDIT_CSV="${BAS_STAGE24_PACKET_AUDIT_CSV:-${LOG_DIR}/stage24_route_rssi_packets_raw.csv}"
export BAS_RT_HISTORY_PATH="${BAS_RT_HISTORY_PATH:-${LOG_DIR}/sionna_rt_history.jsonl}"
export BAS_SIONNA_RT_ONLINE=1
export BAS_SIONNA_TARGET_FLOW="${BAS_SIONNA_TARGET_FLOW:-both}"
export BAS_SIONNA_REQUIRE_GPU="${BAS_SIONNA_REQUIRE_GPU:-1}"
export BAS_MITSUBA_VARIANT="${BAS_MITSUBA_VARIANT:-cuda_ad_mono_polarized}"
export MITSUBA_VARIANT="$BAS_MITSUBA_VARIANT"
export BAS_RT_TX_POS="${BAS_RT_TX_POS:-0,-600,1.5}"
export BAS_RT_MAX_DEPTH="${BAS_RT_MAX_DEPTH:-2}"
export BAS_REQUIRE_NS3_PAYLOAD="${BAS_REQUIRE_NS3_PAYLOAD:-1}"
export BAS_PAYLOAD_BYPASS="${BAS_PAYLOAD_BYPASS:-0}"
export BAS_GCS_FPV="${BAS_GCS_FPV:-1}"
export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-0}"
export NS3_DURATION="${NS3_DURATION:-2800}"
export BAS_GCS_UI_GOTO_SPEED="${BAS_GCS_UI_GOTO_SPEED:-6.0}"
export BAS_GCS_UI_GOTO_TOLERANCE="${BAS_GCS_UI_GOTO_TOLERANCE:-10.0}"

PRESCAN_MODE="${BAS_RF_STRESS_PRESCAN_MODE:-live}"
PRESCAN_TARGETS="${BAS_RF_STRESS_TARGETS:--80,-78,-76,-74,-72}"
PRESCAN_GRID_STEP_M="${BAS_RF_STRESS_PRESCAN_GRID_STEP_M:-60}"
PRESCAN_ALTITUDE_M="${BAS_RF_STRESS_ALTITUDE_M:-10}"
PRESCAN_HOLD_S="${BAS_RF_STRESS_HOLD_S:-220}"
PRESCAN_NORTH_MIN="${BAS_RF_STRESS_NORTH_MIN:-0}"
PRESCAN_NORTH_MAX="${BAS_RF_STRESS_NORTH_MAX:-0}"
PRESCAN_EAST_MIN="${BAS_RF_STRESS_EAST_MIN:--240}"
PRESCAN_EAST_MAX="${BAS_RF_STRESS_EAST_MAX:-240}"
PRESCAN_ROUTE_ORDER="${BAS_RF_STRESS_ROUTE_ORDER:-target_desc}"
PRESCAN_REACH_TOL_M="${BAS_RF_STRESS_REACH_TOL_M:-10}"
PRESCAN_REACH_TIMEOUT_S="${BAS_RF_STRESS_REACH_TIMEOUT_S:-160}"
PRESCAN_NO_COVERAGE_FLOOR_DB="${BAS_RF_STRESS_NO_COVERAGE_FLOOR_DB:--120}"
PRESCAN_FLOOR_AVOID_RADIUS_M="${BAS_RF_STRESS_FLOOR_AVOID_RADIUS_M:-45}"
PRESCAN_POINTS="${REPO_ROOT}/data/processed/stage24_rf_stress_prescan_points.csv"
PRESCAN_TRAJECTORY="${REPO_ROOT}/data/processed/stage24_rf_stress_waypoints.json"
PRESCAN_MAP_PREFIX="${REPO_ROOT}/figures/stage24_rf_stress_prescan_map"
PRESCAN_REPORT="${REPO_ROOT}/reports/stage24_rf_stress_prescan.md"

mkdir -p "$LOG_DIR" "${REPO_ROOT}/data/processed" "${REPO_ROOT}/figures" "${REPO_ROOT}/reports"

echo "==> RUN_ID=${RUN_ID}"
echo "==> LOG_DIR=${LOG_DIR}"
echo "==> stack=${BAS_AUTO_DEMO_STACK}"
echo "==> pre-scan mode=${PRESCAN_MODE}"
echo "==> pre-scan targets=${PRESCAN_TARGETS}"
echo "==> RT TX/GCS position=${BAS_RT_TX_POS}"
echo "==> Sionna target flow=${BAS_SIONNA_TARGET_FLOW}"
echo "==> require ns-3 payload=${BAS_REQUIRE_NS3_PAYLOAD}"
echo "==> payload bypass=${BAS_PAYLOAD_BYPASS}"
echo "==> goto speed/tolerance=${BAS_GCS_UI_GOTO_SPEED} m/s / ${BAS_GCS_UI_GOTO_TOLERANCE} m"
echo "==> packet audit raw=${BAS_STAGE24_PACKET_AUDIT_CSV}"

PRESCAN_COMMON=(
    "${REPO_ROOT}/scripts/prescan_stage24_rf_stress_route.py"
    --radio-map "${REPO_ROOT}/radio_maps/iris_runway.npz"
    --rt-scene "${REPO_ROOT}/scene/iris_runway.xml"
    --rt-tx "$BAS_RT_TX_POS"
    --rt-max-depth "$BAS_RT_MAX_DEPTH"
    "--targets=${PRESCAN_TARGETS}"
    --altitude-m "$PRESCAN_ALTITUDE_M"
    --north-min "$PRESCAN_NORTH_MIN"
    --north-max "$PRESCAN_NORTH_MAX"
    --east-min "$PRESCAN_EAST_MIN"
    --east-max "$PRESCAN_EAST_MAX"
    --grid-step-m "$PRESCAN_GRID_STEP_M"
    --hold-s "$PRESCAN_HOLD_S"
    --reach-tol-m "$PRESCAN_REACH_TOL_M"
    --reach-timeout-s "$PRESCAN_REACH_TIMEOUT_S"
    --route-order "$PRESCAN_ROUTE_ORDER"
    --no-coverage-floor-db "$PRESCAN_NO_COVERAGE_FLOOR_DB"
    --floor-avoid-radius-m "$PRESCAN_FLOOR_AVOID_RADIUS_M"
    --points-out "$PRESCAN_POINTS"
    --trajectory-out "$PRESCAN_TRAJECTORY"
    --map-prefix "$PRESCAN_MAP_PREFIX"
    --report-out "$PRESCAN_REPORT"
)

case "$PRESCAN_MODE" in
    live)
        PRESCAN_CMD=(
            bash "${REPO_ROOT}/scripts/run_sionna_live.sh" --
            "${REPO_ROOT}/sionna_env/bin/python" "${PRESCAN_COMMON[@]}"
            --live-rt
            --mitsuba-variant "$BAS_MITSUBA_VARIANT"
        )
        if [ "$BAS_SIONNA_REQUIRE_GPU" = "1" ]; then
            PRESCAN_CMD+=(--require-gpu)
        fi
        ;;
    cached)
        PRESCAN_CMD=("${REPO_ROOT}/.venv/bin/python" "${PRESCAN_COMMON[@]}")
        ;;
    *)
        echo "Unknown BAS_RF_STRESS_PRESCAN_MODE=${PRESCAN_MODE}; expected live or cached" >&2
        exit 2
        ;;
esac

echo "[rf-stress] pre-scan"
"${PRESCAN_CMD[@]}" 2>&1 | tee "${LOG_DIR}/rf_stress_prescan.log"

export BAS_AUTO_DEMO_TRAJECTORY="$PRESCAN_TRAJECTORY"
echo "==> BAS_AUTO_DEMO_TRAJECTORY=${BAS_AUTO_DEMO_TRAJECTORY}"

set +e
bash "${REPO_ROOT}/scripts/run_stage_2_4_auto_demo.sh"
RUN_RC=$?
set -e

ROUTE_COMMAND=(
    "BAS_RUN_ID=${RUN_ID}"
    "BAS_AUTO_DEMO_STACK=${BAS_AUTO_DEMO_STACK}"
    "BAS_AUTO_DEMO_TRAJECTORY=${BAS_AUTO_DEMO_TRAJECTORY}"
    "BAS_STAGE24_PACKET_AUDIT=1"
    "BAS_SIONNA_RT_ONLINE=1"
    "BAS_RT_TX_POS=${BAS_RT_TX_POS}"
    "BAS_SIONNA_TARGET_FLOW=${BAS_SIONNA_TARGET_FLOW}"
    "BAS_REQUIRE_NS3_PAYLOAD=${BAS_REQUIRE_NS3_PAYLOAD}"
    "BAS_PAYLOAD_BYPASS=${BAS_PAYLOAD_BYPASS}"
    "BAS_SIONNA_REQUIRE_GPU=${BAS_SIONNA_REQUIRE_GPU}"
    "BAS_MITSUBA_VARIANT=${BAS_MITSUBA_VARIANT}"
    "bash scripts/run_stage24_rf_stress_route_audit.sh"
)

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/analyze_stage24_route_rssi_packet_audit.py" \
    --run-dir "$LOG_DIR" \
    --packet-csv "$BAS_STAGE24_PACKET_AUDIT_CSV" \
    --sionna-history "$BAS_RT_HISTORY_PATH" \
    --min-packets-per-bin 300 \
    --command "${ROUTE_COMMAND[*]}"

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/analyze_stage24_payload_path.py" \
    --run-dir "$LOG_DIR" \
    --packet-csv "$BAS_STAGE24_PACKET_AUDIT_CSV"

echo
echo "==> RF-stress route rc=${RUN_RC}"
echo "==> pre-scan report: ${PRESCAN_REPORT}"
echo "==> pre-scan waypoints: ${PRESCAN_TRAJECTORY}"
echo "==> raw packets: ${BAS_STAGE24_PACKET_AUDIT_CSV}"
echo "==> route packets: ${REPO_ROOT}/data/processed/stage24_route_rssi_packets.csv"
echo "==> route bins: ${REPO_ROOT}/data/processed/stage24_route_rssi_bins.csv"
echo "==> route figures: ${REPO_ROOT}/figures/stage24_route_rssi_{packet_trace,map,loss_bins}.{png,pdf,svg}"
echo "==> payload reports: ${REPO_ROOT}/reports/stage24_payload_{path_audit,metrics_summary}.md"

exit "$RUN_RC"
