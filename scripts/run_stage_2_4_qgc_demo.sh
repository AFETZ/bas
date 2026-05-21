#!/usr/bin/env bash
# Stage 2.4 QGroundControl demo: Web GCS (наш) И QGroundControl (Windows)
# одновременно подключены к одному SITL через mavp2p MAVLink router.
#
# Архитектура (стандартный паттерн ardupilot-sitl-docker + mavlink-router,
# адаптированный под наш netns + ns-3 layout):
#
#   QGC (Windows)
#      ↓ UDP 14560
#   [host netns] socat UDP4-LISTEN:14560 → UDP4:10.10.0.2:14560
#      ↓
#   [bas-uav netns] mavp2p
#      tcpc:127.0.0.1:5760  ← один TCP к SITL (single-client решён)
#      udps:0.0.0.0:14550   ← UDP server для MAVProxy GCS через ns-3
#      udps:0.0.0.0:14560   ← UDP server для QGC host relay
#      ↓ TCP 5760
#   SITL ArduCopter
#
# QGC на Windows:
#   Application Settings → Comm Links → Add → UDP
#     Port:        14560
#     Server addr: <WSL eth0 IP>   (распечатывается при старте)
#   Connect — heartbeat появится сразу.
#
# Web GCS и MAVProxy продолжают работать через тот же mavp2p (UDP 14550 ←
# через ns-3 ← MAVProxy в bas-ctrl-far). Команды от обоих GCS не конфликтуют
# благодаря MAVLink system/component ID разделению (QGC и MAVProxy имеют
# разные sysid'ы).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
export BAS_GCS_QGC="${BAS_GCS_QGC:-1}"
export BAS_QGC_HOST_PORT="${BAS_QGC_HOST_PORT:-14560}"
export BAS_QGC_UAV_PORT="${BAS_QGC_UAV_PORT:-14560}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
