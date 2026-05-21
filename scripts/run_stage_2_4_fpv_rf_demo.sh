#!/usr/bin/env bash
# Stage 2.4 FPV + RF демо: Web GCS с live first-person камерой ИЛ препятствиями.
#
# Это объединение run_stage_2_4_fpv_gcs.sh и run_stage_2_4_rf_demo.sh:
#  * Gazebo мир `iris_runway_rf_demo.sdf` уже содержит iris_with_gimbal (с
#    камерой) И препятствия (ангар 20x32x18, башня 9x9x24) — оба ресурса в
#    одном файле, не нужно отдельный fpv+rf мир.
#  * Web GCS показывает: Local NED карта с обводкой зданий и LOS/NLOS pill,
#    live RSSI/loss/delay графики, FPV overlay поверх карты.
#  * Live RF JSON (loss, delay, RSSI) пишется в /tmp/bas_stage24_rf.json для
#    ns-3 dynamic channel hook (см. 2.1.d), и параллельно потребляется UI
#    как источник RF панели.
#  * FPV picture-in-picture рендерится в правом верхнем углу карты; F toggle,
#    ⤢ развернуть на весь блок.
#
# Управление:
#   WASD/ЦФЫВ или Arrow keys или IJKL — горизонтальный velocity
#   Space — подъём (climb)
#   Ctrl  — снижение (descend)
#   Escape — STOP
#   F — toggle FPV overlay
#
# Идея демо: облетать ангар, смотреть как RSSI падает при NLOS (заход за
# здание), визуально подтверждать picture с борта дрона.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
# iris_runway_rf_demo.sdf содержит и камеру, и obstacles — единый мир для
# совмещённого демо. RF channel JSON tee'ится в /tmp для ns-3 поллинга и UI.
export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway_rf_demo.sdf}"
export BAS_GCS_RF_DEMO="${BAS_GCS_RF_DEMO:-1}"
export BAS_RF_CHANNEL_PATH="${BAS_RF_CHANNEL_PATH:-/tmp/bas_stage24_rf.json}"
export BAS_SIONNA_CHANNEL_PATH="${BAS_SIONNA_CHANNEL_PATH:-$BAS_RF_CHANNEL_PATH}"
export BAS_GCS_FPV="${BAS_GCS_FPV:-1}"
export BAS_FPV_MJPEG_PORT="${BAS_FPV_MJPEG_PORT:-8766}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
