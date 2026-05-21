#!/usr/bin/env bash
# Stage 2.4 FPV demo: Web GCS с live first-person камерой с борта Gazebo
# iris_with_gimbal (gimbal pitch_link::camera → GstCameraPlugin → RTP H.264).
#
# Поток камеры виден в браузере как picture-in-picture поверх Local NED карты
# (можно развернуть на весь блок кнопкой ⤢; toggle по клавише F).
#
# Цепочка:
#   Gazebo (iris_runway.sdf + iris_with_gimbal + GstCameraPlugin)
#     -> UDP 127.0.0.1:5600 [bas-uav netns]
#     -> bas-fpv-mjpeg gst-launch (rtp depay -> avdec_h264 -> jpegenc
#                                  -> multipartmux -> tcpserversink 8766)
#     -> /camera.mjpg TCP proxy в gcs_web_ui_server.py
#     -> <img src="/camera.mjpg"> в web/gcs/app.js
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
# iris_runway.sdf — стандартный мир со встроенной iris_with_gimbal моделью
# (gimbal pitch_link::camera + GstCameraPlugin). Это путь "видеопоток с борта"
# по букве ТЗ. RF/Sionna-демо использует другой мир без камеры; пока не
# совмещаем FPV и RF одновременно.
export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway.sdf}"
export BAS_GCS_FPV="${BAS_GCS_FPV:-1}"
export BAS_FPV_MJPEG_PORT="${BAS_FPV_MJPEG_PORT:-8766}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
