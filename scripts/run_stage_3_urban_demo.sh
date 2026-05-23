#!/usr/bin/env bash
# Stage 3 Urban Gazebo demo — расширенная карта тестового сценария.
#
# Поднимает Stage 2.4 FPV+RF+QGC/MAVProxy stack с миром
# iris_runway_urban.sdf вместо стандартного RF demo. Сцена содержит:
#   * 5 multi-storey buildings (office tower 40м, residential tower 60м,
#     warehouse, mall, apartment, commercial)
#   * 3 asphalt roads (main avenue + 2 cross streets)
#   * 12 trees (cylinder trunk + sphere/cone canopy)
#   * 4 streetlights (с emissive lamps)
#   * 2 parked vehicles (cars)
#   * существующие RF demo obstacles (Hangar + Tower + GCS mast) для
#     back-compat с RF panel
#
# Использование:
#   sudo bash scripts/run_stage_3_urban_demo.sh
#   sudo env BAS_GAZEBO_GUI=1 bash scripts/run_stage_3_urban_demo.sh   # видеть UE
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway_urban.sdf}"
export BAS_GCS_RF_DEMO="${BAS_GCS_RF_DEMO:-1}"
export BAS_RF_CHANNEL_PATH="${BAS_RF_CHANNEL_PATH:-/tmp/bas_stage24_rf.json}"
export BAS_SIONNA_CHANNEL_PATH="${BAS_SIONNA_CHANNEL_PATH:-$BAS_RF_CHANNEL_PATH}"
export BAS_SIONNA_TARGET_FLOW="${BAS_SIONNA_TARGET_FLOW:-both}"
export BAS_GCS_FPV="${BAS_GCS_FPV:-1}"
export BAS_FPV_MJPEG_PORT="${BAS_FPV_MJPEG_PORT:-8766}"

# Используем urban-aware RF obstacle список в Web GCS.
export BAS_RF_OBSTACLE_PROFILE="${BAS_RF_OBSTACLE_PROFILE:-urban}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
