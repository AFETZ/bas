#!/usr/bin/env bash
# Stage 2.4 Online Sionna RT demo: каждое UAV-обновление позиции триггерит
# live PathSolver call вместо lookup в pre-computed radio map (.npz). Видео
# и MAVLink-команды деформируются ns-3'ом по результатам real-time ray
# tracing — это полная replication digital-network-twin pattern из
# robpegurri/ns3-rt и paper "Ns3 meets Sionna" (arXiv 2412.20524).
#
# Цепочка:
#   Gazebo iris_with_gimbal pose
#     → events.jsonl flight (Web GCS publish)
#     → sionna_channel_publisher.py --rt-online
#         → Mitsuba scene iris_runway.xml + obstacles
#         → PathSolver(max_depth=2) на каждый UAV update
#         → loss_ratio, path_loss_db, mean_delay → /tmp/bas_stage24_rt.json
#     → ns-3 two_channel.cc polls /tmp/bas_stage24_rt.json каждые 100мс
#         → RateErrorModel + CsmaChannel delay для control+payload (both)
#     → MAVProxy через ns-3 control видит реальные multipath effects
#     → FPV видео через ns-3 payload теряет кадры на NLOS
#
# Требования:
#   - sionna_env/ venv с Sionna 1.x + Mitsuba 3.x + drjit
#   - scene/iris_runway.xml (genited через scripts/export_scene_to_sionna.py)
#   - WSL2 GPU работает (driver CUDA 12+) — но Mitsuba CUDA variant требует
#     OptiX SDK setup, без него используем LLVM (CPU) variant: ~55мс per
#     ray-tracing call в нашей сцене. Это 18Hz max, ns-3 поллит 10Hz — ОК.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_HOST="${BAS_GCS_UI_HOST:-127.0.0.1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"
# RF panel в UI рисуем как обычно (быстрый geometric LOS), а ns-3 поллит
# RT live JSON. Два разных channel models на один UAV — UI для оператора,
# RT для физики каналов.
export BAS_GAZEBO_WORLD="${BAS_GAZEBO_WORLD:-iris_runway_rf_demo.sdf}"
export BAS_GCS_RF_DEMO="${BAS_GCS_RF_DEMO:-1}"
export BAS_RF_CHANNEL_PATH="${BAS_RF_CHANNEL_PATH:-/tmp/bas_stage24_rf.json}"
# Online Sionna RT enabled — публикуется в отдельный файл, на который ns-3
# переключается автоматически (см. wrapper logic в run_stage_2_4_mavproxy_gcs.sh).
export BAS_SIONNA_RT_ONLINE="${BAS_SIONNA_RT_ONLINE:-1}"
export BAS_RT_CHANNEL_PATH="${BAS_RT_CHANNEL_PATH:-/tmp/bas_stage24_rt.json}"
export BAS_RT_TX_POS="${BAS_RT_TX_POS:-0,-60,1.5}"
export BAS_RT_MAX_DEPTH="${BAS_RT_MAX_DEPTH:-2}"
# Деформируем оба канала через RT live update — реалистичный NLOS demo.
export BAS_SIONNA_TARGET_FLOW="${BAS_SIONNA_TARGET_FLOW:-both}"
# FPV overlay по умолчанию включен — оператор видит и RT-physics, и POV.
export BAS_GCS_FPV="${BAS_GCS_FPV:-1}"
export BAS_FPV_MJPEG_PORT="${BAS_FPV_MJPEG_PORT:-8766}"

exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
