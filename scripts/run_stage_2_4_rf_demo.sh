#!/usr/bin/env bash
# Live Stage 2.4 RF/LOS demo: Gazebo obstacles + Web GCS RF graph.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway_rf_demo.sdf}"
export BAS_GCS_RF_DEMO="${BAS_GCS_RF_DEMO:-1}"
export BAS_RF_CHANNEL_PATH="${BAS_RF_CHANNEL_PATH:-/tmp/bas_stage24_rf.json}"
export BAS_SIONNA_CHANNEL_PATH="${BAS_SIONNA_CHANNEL_PATH:-$BAS_RF_CHANNEL_PATH}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
