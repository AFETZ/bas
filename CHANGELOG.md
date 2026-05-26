# Changelog

История релизов по этапам. Формат основан на [Keep a Changelog](https://keepachangelog.com/),
семантика версий — по этапам ТЗ (не SemVer).

## [Unreleased]

### Stage 4 JSON-FDM real SITL verification
- `scripts/multirotor_dynamics.py`: X-config 6DOF dynamics, ground-contact
  IMU support force, IMU white noise + bias drift, true-vs-measured state.
- `scripts/arducopter_airsim_interface.py`: parses real ArduPilot binary
  `servo_packet_16`, uses `frame_rate/frame_count` sim-time, publishes
  GPS/quaternion/IMU sensor JSON.
- `_real_sitl_e2e_smoke.py`: real `arducopter --model json`, `HEARTBEAT`,
  valid `GLOBAL_POSITION_INT`, `STABILIZE → ARM`, RC throttle takeoff >0.5m.
- Debug one-off helpers organized under `scripts/debug/`.

### Stage 3/4 docs refresh
- README, QUICKSTART, DEMOS, STAGES, ARCHITECTURE, INSTALL, TROUBLESHOOTING,
  LIMITATIONS, roadmap and ТЗ matrix now describe Stage 3/4 current state.

### Documentation overhaul
- Полный комплект docs: README + INSTALL + QUICKSTART + ARCHITECTURE +
  DEMOS + STAGES + TROUBLESHOOTING + CONTRIBUTING
- Bootstrap скрипт `scripts/bootstrap.sh` для one-command setup
- Makefile с общими targets
- GitHub Actions CI: lint, syntax, headless smoke

## Stage 2.x — extensions (May 2026)

### Stage 2.4 Auto Demo (`2621447`)
- Playwright + ffmpeg recorder с scripted trajectory
- Output: `demo_report.md` + видео + 14 screenshots
- Verified: 10/10 steps, NLOS RSSI=−87.9 dBm captured

### Stage 2.4 QGC bridge (`fc87b61`)
- bluenviron/mavp2p MAVLink router (`tcpc:5760 + udps:14550 + udps:14560`)
- Host-side socat UDP 14560 → bas-uav:14560 для Windows QGC
- 100+ MAVLink v2 frames/5с verified

### Stage 2.4 Multi-UAV MVP (`4996526`)
- 2 ArduCopter SITL (`-I0/-I1`, sysid 1/2)
- 2 iris моделей в `iris_runway_multi.sdf` (fdm 9002/9012)
- mavp2p multiplex обоих → unified UDP 14550

### Stage 2.4 vertical velocity (`5bb0469`)
- Space=up, Ctrl=down, Escape=STOP (FPV Drone Simulator pattern)
- Backend `up/down` actions через `velocity 0 0 ±vz` в NED

### Stage 2.4 FPV+RF combined demo
- iris_runway_rf_demo.sdf содержит iris_with_gimbal + obstacles
- BAS_SIONNA_TARGET_FLOW=both — ns-3 деформирует и control, и payload

### Stage 2.4 FPV live stream (`c40bd0c`)
- Gazebo iris_with_gimbal GstCameraPlugin RTP H.264
- bas-fpv-mjpeg gst-launch → MJPEG TCP server 8766
- /camera.mjpg proxy в Web GCS, `<img>` overlay
- 15 fps, ~16-31 MB видео за минуту

### Stage 2.4 операторские fixes (`4592227`)
- Auto-takeoff при первом WASD (если не в воздухе)
- Force-arm fallback после 6с timeout
- Derived NED из GPS lat/lon когда LOCAL_POSITION_NED не приходит
- Toast notifications для silent failures

### Stage 2.4 RF demo
- iris_runway_rf_demo.sdf — hangar (20×32×18м), tower (9×9×24м), GCS mast
- Web UI rf_loop: geometric LOS/NLOS clipping, RSSI/loss/delay график
- Live RF JSON для ns-3 dynamic channel hook

### Stage 2.4 Manual GCS (`c0599d1`)
- Browser-based Web GCS UI
- MAVProxy CLI как single command source
- Acceptance: pymavlink direct command sender НЕ используется

### Stage 2.2 Cosys-AirSim full deploy (`108835e`, `285e518`)
- Auto-download Cosys-AirSim Linux build (637 MB) + Windows build (556 MB)
- `BAS_AIRSIM_MODE=windows` — real RTX 5070 Ti GPU rendering через WSL interop
- 7 cameras возвращают реальные PNG (256×144 RGBA)
- kisak-mesa PPA + Dozen ICD для Vulkan-over-D3D12 (не используется UE5 5.5
  из-за missing SM6 features, но установлен как infrastructure)

### Stage 2.2 architectural bridge (`b84b007`)
- `scripts/airsim_{client,stub_server,bridge}.py`
- Минимальный msgpack-rpc client (без legacy `airsim` PyPI)
- 360 pose forwards verified против real UE5

### Stage 2.1.e — online Sionna RT (`3c5f4fd`)
- `sionna_channel_publisher.py --rt-online` live PathSolver
- 42-55мс per ray-tracing call на CPU (LLVM JIT)
- ns-3 polls `/tmp/bas_stage24_rt.json` каждые 100мс

### Stage 2.x — ns-3 sionnaTargetFlow (`766ff47`)
- CLI option `--sionnaTargetFlow=payload|control|both`
- При `both` ns-3 деформирует ОБА RateErrorModel синхронно

### Stage 2.1 (`4cf7f32`–`60c0ff2`)
- Mitsuba 3 scene exporter
- Offline radio map .npz (RadioMapSolver, 80×30 cells)
- Dynamic JSON channel hook для ns-3 (100мс polling)

## Stage 1.x — основа (April-May 2026)

### Stage 1.8 MAVROS real flight (`22e8622`)
- `MAV_CMD_MISSION_START` (CommandLong 300) после ARM
- Без этого AUTO+armed без RC throttle не взлетал
- 575 samples, 7/7 waypoints, 253м distance, 30м max_alt

### Stage 1.8 close (`415bcd7`)
- ROS2 humble + MAVROS 2.14 + custom rclpy bridge
- Service calls вместо MAVLink commands
- Force-arm через CommandLong magic 21196

### Stage 1.7 LoRa Serial (`8cd990a`)
- PHY-калиброванный PointToPoint SX1276
- PTY + dual-socat bridge (host ↔ docker UNIX sockets)
- 7/7 waypoints, 252.4м через LoRa без WiFi fallback
- PDR lora_gcs_tx=1.000, lora_uav_tx≈0.99 (per Augustin et al. PER=0.01)

### Stage 1.6 — WiFi vs LoRa comparison
- Side-by-side Markdown + CSV отчёт
- analyzer `comparison.md`

### Stage 1.5.2 (`528b33b`)
- RTP/H.264 payload канал через ns-3
- Gazebo iris_with_gimbal POV camera через GstCameraPlugin
- video_rx.mp4 (~16 MB) на приёмнике
- outage correlation: video gaps mapped to outage windows
- camera regression: gz-sim 8.11 + ardupilot_gazebo FDM (`f1a872b`)

### Stage 1.5.1 — AUTO mission через ns-3
- baseline_wifi + degraded_lora profiles
- outage windows 120-123с, 160-163с
- 7/7 waypoints, 252м distance, AUTO→LAND

### Stage 1.5.0 — Shadow GCS
- Orchestrator в bas-ctrl-far netns
- MAVLink через ns-3 control channel

### Stage 1.0–1.4 — skeleton
- Docker stack (gazebo, sitl, ns3, video, mavros, mavbridge)
- ns-3 TapBridge UseLocal mode
- Базовый MAVLink через socat

## История fixes / regressions

- `4529bdc` — cosmetic regressions в 1.8 MAVROS bridge + analyzer distance
- `4592227` — Stage 2.4 WASD/marker/re-takeoff фиксы
- `8463acf` — kill_stale_ui preflight для :8765
- `b66ecd6` — .gitignore runtime artifacts + personal TZ DOCX
- `1311cff` — публикация completed BAS prototype architecture

## Backlog

Все 7 backlog-пунктов из `docs/roadmap.md` закрыты в мае 2026. Stage 3
добавил ИССГР API/sync/on-board/CV, Stage 4 добавил simulator bridges,
AirSim scene map, cyber defense, large maps, admin dashboard и parallel
compute. Остающееся вне scope — HIL/field tests, production deployment и
полный общегрантовый delivery pack по всем исполнителям.

## Лицензии сторонних компонентов

См. [docs/CONTRIBUTING.md#license](docs/CONTRIBUTING.md#license).
