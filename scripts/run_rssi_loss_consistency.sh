#!/usr/bin/env bash
# Controlled packet-level ns-3 RSSI/loss consistency check.
#
# This is not a Gazebo/SITL mission and not hardware RF calibration. It is a
# calibration-style check that the packet drop decisions inside ns-3 follow the
# repository RSSI->loss mapping for fixed RSSI targets.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${RUN_ID:-rssi_loss_consistency_$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${REPO_ROOT}/logs/${RUN_ID}"
RAW_CSV="${LOG_DIR}/rssi_loss_consistency_per_packet.csv"
TARGETS="${TARGETS:--95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60}"
FLOWS="${FLOWS:-control,payload}"
SEEDS="${SEEDS:-5}"
PACKETS_PER_POINT="${PACKETS_PER_POINT:-1000}"
PACKET_BYTES="${PACKET_BYTES:-256}"
INTERVAL_MS="${INTERVAL_MS:-0.2}"
BASE_SEED="${BASE_SEED:-1337}"
NS3_IMAGE="${NS3_IMAGE:-bas/ns3:dev}"
CONTAINER_NAME="${CONTAINER_NAME:-bas-rssi-loss-consistency}"

mkdir -p "${LOG_DIR}" "${REPO_ROOT}/data/processed" "${REPO_ROOT}/figures" "${REPO_ROOT}/reports"

echo "==> RUN_ID=${RUN_ID}"
echo "==> LOG_DIR=${LOG_DIR}"
echo "==> targets=${TARGETS}"
echo "==> flows=${FLOWS}, seeds=${SEEDS}, packets_per_point=${PACKETS_PER_POINT}"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

docker run --rm --name "${CONTAINER_NAME}" \
    -v "${REPO_ROOT}/ns3:/work/ns3:ro" \
    -v "${REPO_ROOT}/logs:/work/logs" \
    --entrypoint bash "${NS3_IMAGE}" -lc "\
        cp /work/ns3/scenarios/rssi_loss_consistency.cc /work/ns3-src/scratch/ \
        && cd /work/ns3-src \
        && ./ns3 build > /tmp/rssi_loss_consistency_build.log 2>&1 \
        && /work/ns3-src/build/scratch/ns3.40-rssi_loss_consistency-optimized \
            --runId=${RUN_ID} \
            --outCsv=/work/logs/${RUN_ID}/rssi_loss_consistency_per_packet.csv \
            --targets='${TARGETS}' \
            --flows='${FLOWS}' \
            --seeds=${SEEDS} \
            --packetsPerPoint=${PACKETS_PER_POINT} \
            --packetBytes=${PACKET_BYTES} \
            --intervalMs=${INTERVAL_MS} \
            --baseSeed=${BASE_SEED}"

"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/analyze_rssi_loss_consistency.py" \
    --input "${RAW_CSV}" \
    --targets="${TARGETS}" \
    --flows="${FLOWS}" \
    --min-packets-per-target 1000 \
    --min-seeds 5 \
    --command "RUN_ID=${RUN_ID} TARGETS='${TARGETS}' FLOWS='${FLOWS}' SEEDS=${SEEDS} PACKETS_PER_POINT=${PACKETS_PER_POINT} PACKET_BYTES=${PACKET_BYTES} INTERVAL_MS=${INTERVAL_MS} BASE_SEED=${BASE_SEED} NS3_IMAGE=${NS3_IMAGE} bash scripts/run_rssi_loss_consistency.sh"

echo
echo "==> raw CSV: ${RAW_CSV}"
echo "==> processed per-packet: ${REPO_ROOT}/data/processed/rssi_loss_consistency_per_packet.csv"
echo "==> bins: ${REPO_ROOT}/data/processed/rssi_loss_consistency_bins.csv"
echo "==> figure: ${REPO_ROOT}/figures/rssi_loss_consistency_ieee.{png,pdf,svg}"
echo "==> summary: ${REPO_ROOT}/reports/rssi_loss_consistency_summary.md"
