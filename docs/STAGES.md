# Stages — каталог этапов разработки

Хронологический список всех stages с краткой ссылкой на artifact и подробный plan doc.

## Phase 1 — базовая инфраструктура

### Stage 1.0–1.4 — skeleton

Docker stack, Gazebo + ArduPilot SITL через `ardupilot_gazebo` plugin, базовый
MAVLink через socat, ns-3 TapBridge.

Артефакты: `docker/*.Dockerfile`, `docker-compose.shared-netns.yml`,
`scripts/setup_radio_net.sh`, `scripts/_start_build.sh`.

### Stage 1.5.0 — Shadow GCS

Orchestrator работает в `bas-ctrl-far` netns через `ip netns exec`, MAVLink
идёт через ns-3 control channel вместо прямого UDP.

Wrapper: `scripts/run_stage_1_5_0.sh`.

### Stage 1.5.1 — AUTO mission через ns-3

Полная mission AUTO с `simple_route.yaml` (7 waypoints, 250м distance, 30м
altitude). Два profile: `baseline_wifi` и `degraded_lora` (с outage 120-123c,
160-163c).

Wrapper: `scripts/run_stage_1_5_1.sh wifi_good|degraded_lora`.

[stage_1_5_1_known_issues.md](stage_1_5_1_known_issues.md) — WSL2 race conditions.

### Stage 1.5.2 — RTP/H.264 payload

Второй ns-3 канал для видео. GstCameraPlugin в Gazebo iris_with_gimbal →
RTP H.264 → ns-3 payload → mp4 на приёмнике. Outage correlation: видео
теряется когда канал в outage window.

Plan: [stage_1_5_2_plan.md](stage_1_5_2_plan.md).
Wrapper: `scripts/run_stage_1_5_2_mission.sh wifi_good|degraded_lora`.

### Stage 1.6 — comparison report

Запускает 1.5.2 на обоих профилях, генерирует side-by-side
`comparison.md` + `comparison.csv` с PDR, loss, jitter, goodput.

Wrapper: `scripts/run_stage_1_6_compare.sh`.

### Stage 1.7 — LoRa через Serial Port

Без IP в радио-петле. PHY-калиброванный `lora_serial.cc` под Semtech SX1276
(SF7/BW125: data_rate=5470 bps, airtime ~50мс, PER=0.01). Full-duplex
PTY-stream от host pymavlink до SITL TCP 5760 через двa socat-bridge.

Legacy signetlabdei/lorawan baseline сохранён в `ns3/scenarios/lora_serial_lorawan.cc`.

Plan: [stage_1_7_lora_serial_plan.md](stage_1_7_lora_serial_plan.md).
Wrapper: `scripts/run_stage_1_7_lora_serial.sh`.

### Stage 1.8 — MAVROS backend

ROS2/MAVROS как альтернатива pymavlink. `bas/mavros:dev` контейнер с rclpy
bridge node, который вызывает service calls вместо MAVLink commands:
`/mavros/cmd/arming`, `/mavros/set_mode`, `/mavros/mission/push`,
`/mavros/cmd/command` (force-arm + MISSION_START).

С commit `22e8622` real полёт работает (575 samples, 7/7 waypoints, 253м).

Plan: [stage_1_8_mavros_plan.md](stage_1_8_mavros_plan.md).
Wrapper: `scripts/run_stage_1_8_mavros.sh baseline_wifi`.

## Phase 2 — расширения

### Stage 2.1 — Sionna RT

Offline radio map pre-computation + dynamic JSON hook для ns-3 live polling.

**Stage 2.1.a — smoke**: Sionna RT работает на встроенной сцене.
**Stage 2.1.b — scene exporter**: `scripts/export_scene_to_sionna.py` создаёт
Mitsuba 3 XML с runway + obstacles + ITU-R materials.
**Stage 2.1.c — radio map**: `RadioMapSolver` → `radio_maps/iris_runway.npz`
(80×30 cells, 65% coverage).
**Stage 2.1.d — dynamic JSON hook**: ns-3 `two_channel.cc` polls
`/tmp/sionna_channel.json` каждые 100мс, обновляет `RateErrorModel.ErrorRate`.
**Stage 2.1.e — online RT** (commit `3c5f4fd`): `sionna_channel_publisher.py
--rt-online` делает live `PathSolver` call на каждое UAV position update
(~42-55мс/call на CPU).

Plan: [stage_2_1_sionna_plan.md](stage_2_1_sionna_plan.md).
Wrappers:
- `scripts/run_stage_2_1_sionna.sh` (offline radio map)
- `scripts/run_stage_2_4_rt_online_demo.sh` (online RT)

### Stage 2.2 — AirSim overlay (Cosys-AirSim)

**Choice**: Cosys-AirSim (KU Leuven, активный fork) вместо deprecated
Microsoft AirSim. UE5.5 + native ROS2 + GPU-LiDAR/RADAR + Linux precompiled
binaries.

Артефакты:
- `scripts/airsim_client.py` — минимальный msgpack-rpc client (250 LOC)
- `scripts/airsim_stub_server.py` — API stub для headless CI
- `scripts/airsim_bridge.py` — Gazebo→AirSim pose forwarder
- `scripts/run_stage_2_2_airsim_overlay.sh` — 4 mode: stub/linux/windows/off
- Официальный `cosysairsim==3.3.0` pip package

**Modes**:
- `stub` (default) — CI smoke, без UE5
- `linux` — Cosys-AirSim Linux build (637 MB), `-nullrhi` (pose API ok)
- `windows` — Cosys-AirSim Windows build (556 MB), **real GPU rendering**,
  bridge через WSL interop + Windows host IP

Plan: [stage_2_2_airsim_overlay.md](stage_2_2_airsim_overlay.md).

### Stage 2.3 — Multi-UAV MVP

2 ArduCopter SITL экземпляра (`-I0 sysid=1, -I1 sysid=2`) + 2 iris в Gazebo
на разных fdm_port (9002/9012, через локальную копию iris_with_ardupilot_uav2
модели) + единый `bluenviron/mavp2p` MAVLink router (multiplex `tcpc:5760 +
tcpc:5770 → udps:14550`).

Pattern из `arthurrichards77/ardupilot_sitl_docker` + `Intelligent-Quads/iq_tutorials`.

Wrapper: `scripts/run_stage_2_4_multi_uav_demo.sh`.

### Stage 2.4 — Web GCS + ручное управление

Browser-based UI операторской консоли с управлением через MAVProxy stdin.

**Контракт**: команды идут только через MAVProxy stdin (acceptance path),
прямой pymavlink не используется как flight-command source.

Артефакты:
- `scripts/gcs_web_ui_server.py` — HTTP server (stdlib ThreadingHTTPServer)
- `scripts/mavproxy_stage_2_4_driver.py` — MAVProxy subprocess wrapper
- `web/gcs/{index.html,app.js,styles.css}` — UI

**Управление**:
- Buttons: GUIDED / ARM / FORCE / TAKEOFF / LAND / DISARM / STOP / GO TO
- WASD/ЦФЫВ/Arrow/IJKL — горизонтальный velocity
- Space — climb, Ctrl — descend
- Escape — STOP
- F — toggle FPV overlay

Plan: [stage_2_4_manual_gcs.md](stage_2_4_manual_gcs.md).
Wrappers:
- `scripts/run_stage_2_4_mavproxy_gcs.sh ui|interactive|smoke` (base)
- `scripts/run_stage_2_4_rf_demo.sh` (+ obstacles + RF panel)
- `scripts/run_stage_2_4_fpv_gcs.sh` (+ FPV overlay)
- `scripts/run_stage_2_4_fpv_rf_demo.sh` (комбо)

### Stage 2.4 QGroundControl bridge

bluenviron/mavp2p MAVLink router (`tcpc:5760 + udps:14550 + udps:14560`)
вместо socat mavbridge. Host-side socat UDP relay 14560 → bas-uav:14560
позволяет Windows QGC подключиться.

Plan: [stage_2_4_qgc_setup.md](stage_2_4_qgc_setup.md).
Wrapper: `scripts/run_stage_2_4_qgc_demo.sh`.

### Stage 2.4 Auto Demo Recorder

Playwright + ffmpeg + scripted trajectory → grant-ready demo video.

Артефакты:
- `scripts/auto_demo_recorder.py` (Playwright orchestration, 540 LOC)
- `scripts/run_stage_2_4_auto_demo.sh` (wrapper)

Output: `demo_report.md` + `video/web_gcs.webm` + `video/fpv.mjpeg.mp4` +
14 screenshots в `screenshots/`.

Verified: 10/10 trajectory steps, NLOS-кадр поймал RSSI=−87.9 dBm, loss=62%.

## Резюме

| Stage | Commit | Status |
|---|---|---|
| 1.0–1.4 | early | ✅ |
| 1.5.0 | early | ✅ |
| 1.5.1 | `1d05d71` etc | ✅ |
| 1.5.2 | `528b33b`, ... | ✅ |
| 1.6 | early | ✅ |
| 1.7 | `8cd990a`, ... | ✅ |
| 1.8 | `415bcd7`, `22e8622` (MISSION_START fix) | ✅ |
| 2.1 | `4cf7f32`, ..., `3c5f4fd` (online RT) | ✅ |
| 2.2 | `b84b007`, `f2fc0a4`, `108835e` (Cosys-AirSim real GPU) | ✅ |
| 2.3 | `4996526` (multi-UAV MVP) | ✅ |
| 2.4 | `c0599d1`, `4592227` (WASD/FPV fixes), `5bb0469` (vertical velocity) | ✅ |
| 2.4 QGC | `fc87b61` | ✅ |
| 2.4 Auto Demo | `2621447` | ✅ |
| ns-3 sionnaTargetFlow | `766ff47` | ✅ |

См. [CHANGELOG.md](../CHANGELOG.md) для полной хронологии.
