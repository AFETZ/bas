#!/usr/bin/env bash
# Stage 2.4 Multi-UAV demo: 2 ArduCopter SITL экземпляра + 2 iris_with_ardupilot
# модели в Gazebo + единый mavp2p router для MAVProxy GCS через ns-3.
#
# Architecture (proof-of-concept):
#   Gazebo iris_runway_multi.sdf
#     iris_uav1 (model://iris_with_ardupilot)        ← fdm 9002 → SITL -I0 :5760
#     iris_uav2 (model://iris_with_ardupilot_uav2)   ← fdm 9012 → SITL -I1 :5770
#                                                       sysid=2 (override)
#
#   bas-uav netns:
#     SITL1 (-I0, sysid=1) на TCP 5760
#     SITL2 (-I1, sysid=2) на TCP 5770
#     mavp2p tcpc:5760 + tcpc:5770 + udps:14550   ← multiplex обоих в один UDP
#       ↓
#     ns-3 control channel
#       ↓
#     MAVProxy GCS в bas-ctrl-far netns видит ОБЕ системы (sysid 1 и 2)
#
# Reference patterns:
#   * https://github.com/arthurrichards77/ardupilot_sitl_docker (stack generator)
#   * https://github.com/Intelligent-Quads/iq_tutorials/blob/master/docs/swarming_ardupilot.md
#   * https://github.com/radarku/sitl-swarm
#   * MAVLink sysid-based system discrimination — стандартный multi-vehicle pattern
#
# Это **MVP** — единый ns-3 канал на оба UAV, общие радио-условия. Расширение
# до per-UAV ns-3 каналов оставлено в roadmap. Web GCS UI пока отображает
# только UAV1 на карте (выбор drone через UI — отдельный backlog).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway_multi.sdf}"
# Multi-UAV → активируем compose profile "multi" чтобы поднять sitl2 +
# mavrouter-multi. Wrapper переключает default mavbridge → mavrouter-multi.
export BAS_GCS_MULTI_UAV="${BAS_GCS_MULTI_UAV:-1}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
