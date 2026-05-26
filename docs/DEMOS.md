# Demo Wrappers Catalogue

Каталог всех `scripts/run_stage_*.sh` обёрток. Каждая запускает свой стек с
определённым набором env переменных. Делитесь wrapper'ом, и человек получит
тот же стенд в одну команду.

## Acceptance smoke (CI-grade)

### `run_stage_1_5_1.sh`

Headless mission AUTO через ns-3 control channel.

```bash
sudo bash scripts/run_stage_1_5_1.sh wifi_good       # baseline профиль
sudo bash scripts/run_stage_1_5_1.sh degraded_lora   # с outage + loss
```

Артефакты: `logs/<run>/events.jsonl`, `logs/<run>/ns3_events.jsonl`, `report.md`.

### `run_stage_1_5_2_mission.sh`

То же + payload канал с RTP/H.264 видео.

```bash
sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
sudo env BAS_GAZEBO_GUI=1 BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

Доп. артефакты: `video_rx.mp4` (16 MB), `video_tx.jsonl`, `video_rx.jsonl`.

### `run_stage_1_6_compare.sh`

WiFi vs LoRa-degraded сравнение. Запускает 1.5.2 на обоих профилях, потом
analyzer пишет `comparison.md` + `comparison.csv` бок о бок.

```bash
sudo bash scripts/run_stage_1_6_compare.sh
```

### `run_stage_1_7_lora_serial.sh`

LoRa через Serial Port — без IP в радио-петле. PHY-калибровано под SX1276
(SF7/BW125, data_rate=5470 bps, airtime ~50 мс, PER=0.01 за Augustin et al.).

```bash
sudo bash scripts/run_stage_1_7_lora_serial.sh
```

Артефакты: `events.jsonl` с `flow_id=lora_gcs_tx, lora_uav_tx`.

### `run_stage_1_8_mavros.sh`

ROS2/MAVROS бэкенд как альтернативный путь управления. Не использует ns-3
(MAVROS подключается напрямую к SITL TCP). `MAV_CMD_MISSION_START`
триггерит реальный полёт.

```bash
sudo bash scripts/run_stage_1_8_mavros.sh baseline_wifi
```

Артефакт: 7/7 waypoints, distance 253м, max_alt 30м.

## Demo с Web GCS

### `run_stage_2_4_mavproxy_gcs.sh`

Основная Web GCS точка входа. Принимает mode:

```bash
sudo bash scripts/run_stage_2_4_mavproxy_gcs.sh ui            # Web UI на :8765
sudo bash scripts/run_stage_2_4_mavproxy_gcs.sh interactive   # MAVProxy CLI
sudo bash scripts/run_stage_2_4_mavproxy_gcs.sh smoke         # scripted
```

Все остальные `run_stage_2_4_*_demo.sh` — это **wrappers** над этим базовым,
которые ставят правильные env переменные перед вызовом.

### `run_stage_2_4_rf_demo.sh`

Web GCS + obstacles в Gazebo (hangar + tower) + live RF panel.

```bash
sudo bash scripts/run_stage_2_4_rf_demo.sh
```

Env по умолчанию:
- `BAS_GAZEBO_GUI=1` — Gazebo окно через WSLg
- `BAS_GAZEBO_WORLD=iris_runway_rf_demo.sdf`
- `BAS_GCS_RF_DEMO=1`
- `BAS_RF_CHANNEL_PATH=/tmp/bas_stage24_rf.json`

### `run_stage_2_4_fpv_gcs.sh`

Web GCS + FPV-окно (live MJPEG с борта Gazebo). Без obstacles.

```bash
sudo bash scripts/run_stage_2_4_fpv_gcs.sh
```

Env: `BAS_GCS_FPV=1`, `BAS_GAZEBO_WORLD=iris_runway.sdf` (стандартная с
gimbal camera).

### `run_stage_2_4_fpv_rf_demo.sh`

**Комбо**: FPV + RF одновременно. Самый показательный демо для оператора:
картинка с борта + RSSI график + видны препятствия на карте.

```bash
sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh
```

Env комбо:
- `BAS_GCS_FPV=1`
- `BAS_GCS_RF_DEMO=1`
- `BAS_SIONNA_TARGET_FLOW=both` — Sionna хук деформирует и control, и payload
- `BAS_GAZEBO_WORLD=iris_runway_rf_demo.sdf` (он же содержит iris_with_gimbal)

### `run_stage_2_4_rt_online_demo.sh`

FPV + RF + **live Sionna RT** PathSolver на каждую UAV pose. RF model теперь
реальный ray-tracing вместо geometric или offline lookup.

```bash
sudo bash scripts/run_stage_2_4_rt_online_demo.sh
```

Дополнительно:
- `BAS_SIONNA_RT_ONLINE=1` — публиковать через PathSolver
- `BAS_RT_CHANNEL_PATH=/tmp/bas_stage24_rt.json` (отдельно от UI rf JSON)
- `BAS_RT_SCENE_PATH=scene/iris_runway.xml` (Mitsuba)
- `BAS_RT_MAX_DEPTH=2` (ray reflection depth)

Требует `sionna_env/` venv с Sionna 1.x + Mitsuba 3.x.

### `run_stage_2_4_qgc_demo.sh`

Web GCS + QGroundControl одновременно через `bluenviron/mavp2p` MAVLink router.

```bash
sudo bash scripts/run_stage_2_4_qgc_demo.sh
```

После старта в Windows QGC: **Application Settings → Comm Links → Add → UDP**,
порт 14560, server addr = WSL eth0 IP (распечатывается в консоли). Подключиться.

### `run_stage_2_4_multi_uav_demo.sh`

2 ArduCopter SITL экземпляра + 2 iris в Gazebo + mavp2p multi-router.

```bash
sudo bash scripts/run_stage_2_4_multi_uav_demo.sh
```

Env: `BAS_GCS_MULTI_UAV=1`, `BAS_GAZEBO_WORLD=iris_runway_multi.sdf` (содержит
`iris_with_ardupilot` + `iris_with_ardupilot_uav2`).

### `run_stage_2_4_auto_demo.sh`

Headless auto recording: stack + Playwright + ffmpeg + scripted trajectory.

```bash
sudo bash scripts/run_stage_2_4_auto_demo.sh
sudo env BAS_AUTO_DEMO_STACK=run_stage_2_4_multi_uav_demo.sh \
     bash scripts/run_stage_2_4_auto_demo.sh   # выбрать другой базовый stack
```

Выход: `logs/<run>/demo_report.md` + `video/web_gcs.webm` + `video/fpv.mjpeg.mp4` +
14 screenshots в `screenshots/`.

## AirSim overlay

### `run_stage_2_2_airsim_overlay.sh`

```bash
# Stub mode (CI, без UE5)
sudo bash scripts/run_stage_2_2_airsim_overlay.sh

# Linux UE5 nullrhi (real API, image empty без GPU)
sudo env BAS_AIRSIM_MODE=linux \
     bash scripts/run_stage_2_2_airsim_overlay.sh

# Windows native UE5 (REAL GPU rendering, real PNG images)
sudo env BAS_AIRSIM_MODE=windows \
     bash scripts/run_stage_2_2_airsim_overlay.sh

# Manually управляемый external AirSim
sudo env BAS_AIRSIM_MODE=off BAS_AIRSIM_HOST=<ip> \
     bash scripts/run_stage_2_2_airsim_overlay.sh
```

Артефакты:
- `airsim_pose_forward.jsonl` — что bridge отправил в AirSim
- `airsim_stub_pose.jsonl` (stub mode) — что stub получил
- `airsim_blocks.log` или `airsim_blocks_win.log` — UE5 stdout/stderr
- `airsim_camera/frame_NNN.bin` — захваченные кадры (только Windows mode)

## Stage 4 sim bridges

### `run_stage_4_sim_bridges_demo.sh`

Контрактные bridge'и для зоны ArduPilot/Gazebo/AirSim:
- MAVLink fanout router: один MAVLink source → GCS/Gazebo/AirSim/file sinks.
- ArduPilot ↔ AirSim JSON-FDM: PWM in → X-config 6DOF dynamics → IMU/GPS out.
- MAVLink mirror: `GLOBAL_POSITION_INT` + `ATTITUDE` → `simSetVehiclePose`.

```bash
# CI smoke: router smoke + JSON-FDM physics smoke
bash scripts/run_stage_4_sim_bridges_demo.sh smoke

# Только fanout router, ожидает MAVLink на :14550
bash scripts/run_stage_4_sim_bridges_demo.sh router

# AirSim stub + MAVLink mirror bridge
bash scripts/run_stage_4_sim_bridges_demo.sh mirror

# SITL + router + mirror bridge + AirSim stub, если sim_vehicle.py найден
SIM_VEHICLE=~/ardupilot/Tools/autotest/sim_vehicle.py \
    bash scripts/run_stage_4_sim_bridges_demo.sh full
```

Артефакты: `logs/stage_4_sim_bridges_<ts>/router_smoke.log`,
`arducopter_airsim_smoke.log`, `router_capture.mav`, `mirror_bridge.jsonl`,
`airsim_pose.jsonl`.

### `_real_sitl_e2e_smoke.py`

Полный real ArduCopter SITL closed-loop через JSON-FDM. Это не просто
wire-smoke: скрипт запускает `arducopter --model json:127.0.0.1`, ждёт
валидный MAVLink telemetry stream, переводит Copter в `STABILIZE`, force-arm'ит
и даёт RC throttle до видимого набора высоты.

```bash
bash scripts/install_ardupilot.sh          # если arducopter binary ещё нет
.venv/bin/python scripts/_real_sitl_e2e_smoke.py
```

Ожидаемый proof: `HEARTBEAT`, valid `GLOBAL_POSITION_INT`, bridge PWM
round-trip, `ARMED: True`, takeoff delta >0.5 м, max PWM > hover.

## Manual / debug

### `run_stage_2_4_operator_ui.sh`

Только Web UI server (без orchestrator или Docker stack). Для отладки UI.

```bash
.venv/bin/python scripts/gcs_web_ui_server.py --demo
# Открыть http://127.0.0.1:8765 — увидите UI с simulated telemetry
```

### Utility scripts (под `_`)

| Script | Что |
|---|---|
| `_smoke_radio.sh` | Только ns-3 smoke без SITL |
| `_smoke_mission_setup.sh` | Только bridges + netns без полёта |
| `_inspect_last_run.sh` | Печать ключевых строк из последнего report.md |
| `_show_last_stage15.sh` | tail logs last stage 1.5.* |
| `_show_mission_15.sh` | Mission events из logs |

Новые одноразовые probes живут в `scripts/debug/`. Не используйте их как
публичные demo entrypoints, пока они не перенесены обратно в `scripts/` и не
описаны здесь.

## Структура wrapper'а (как написать свой)

```bash
#!/usr/bin/env bash
# scripts/run_stage_X_Y_my_demo.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Env по умолчанию (можно override через env при вызове)
export BAS_GAZEBO_GUI="${BAS_GAZEBO_GUI:-1}"
export BAS_STAGE24_FORCE_ARM="${BAS_STAGE24_FORCE_ARM:-1}"
export BAS_GCS_UI_PORT="${BAS_GCS_UI_PORT:-8765}"

# Mode-specific env
export BAS_GCS_FPV="${BAS_GCS_FPV:-1}"
export BAS_GCS_RF_DEMO="${BAS_GCS_RF_DEMO:-1}"
export BAS_SIONNA_RT_ONLINE="${BAS_SIONNA_RT_ONLINE:-1}"
# ... etc

# Делегируем базовому wrapper'у
exec bash "${SCRIPT_DIR}/run_stage_2_4_mavproxy_gcs.sh" ui
```
