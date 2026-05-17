# Регрессия камеры/mission AUTO после v0.9 — open issue

**Status:** BLOCKED. После rebuild gazebo image (apt update протащил
`gz-harmonic 1.0.0-1~jammy` / `libgz-sim8 8.11.0-1~jammy`) SITL+Gazebo
FDM связь сломалась. Mission AUTO не стартует.

## Симптомы

`sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_2_1_sionna.sh`:

- Gazebo Sim Server v8.11.0 стартует.
- ArduPilotPlugin **загружается** (видно с `-v 4`, в production -v3 не пишет):
  ```
  [Dbg] Loaded system [ArduPilotPlugin] for entity [15]
  [Dbg] Computed IMU topic to be: world/iris_runway/.../imu
  ```
- В bas-uav netns plugin bind'ит `127.0.0.1:9002` (виден в
  `ip netns exec bas-uav ss -unlp` как `users:(("ruby",pid=...))`,
  ruby это gz sim launcher).
- SITL стартует, открывает `JSON control interface set to 127.0.0.1:9002`,
  ждёт connection on serial 5760.
- mavbridge socat подключается, SITL `Connection on serial port 5760`.
- SITL шлёт servos на UDP 9002 → **Plugin не отвечает sensors на 9003**:
  ```
  sitl.log:  No JSON sensor message received, resending servos
             No JSON sensor message received, resending servos  (x200)
  ```
- orchestrator: HEARTBEAT timeout (120/300с), mission не стартует.
- video_rx.mp4 = 587 байт (только MP4 header), video_tx.jsonl = 1 запись (meta).
- report.md статус=unknown, landed=False.

## Что работало раньше (v0.9, eb93999, 16 May 2026)

Тот же docker-compose, тот же scripts/run_stage_1_5_2_mission.sh,
camera mode давала video_rx.mp4 ~16 MB и mission landed. См. commit
`eb93999` ("v0.9 stage 1.5.2.b: real Gazebo camera").

## Что изменилось между v0.9 и now

| Артефакт | v0.9 (16 May) | Сейчас (17 May) |
|---|---|---|
| `ardupilot_gazebo` HEAD | `082a0fe Iris: improve collisions` (April 2026) | **тот же `082a0fe`** — не менялся |
| `docker/gazebo/Dockerfile` | минимальный | добавлены `gstreamer1.0-plugins-{base,good,bad,ugly}`, `gstreamer1.0-libav`, `gstreamer1.0-tools`, `libdebuginfod1` (для GstCameraPlugin streaming) |
| `gz-harmonic` apt пакет | ~`8.10.x` (предположительно) | `8.11.0-1~jammy` |
| `libgz-sim8` apt пакет | старая версия | `8.11.0-1~jammy` |
| OSRF apt репо | тот же `packages.osrfoundation.org/gazebo/ubuntu-stable` | тот же |

Итог: между билдами вышел `gz-sim 8.11.0` который **не совместим** с
текущим master ardupilot_gazebo по FDM пути.

## Что попробовано (не помогло)

1. **`BAS_VIDEO_CAMERA_STRICT=0`** (commit `e4576db`) — позволил mission стартовать
   даже если camera RTP не сразу. Mission всё равно падает на HEARTBEAT.
2. **`gz-sim --no-cache rebuild`** ardupilot_gazebo (попытка ABI rebuild) — без эффекта.
3. **`sed lock_step=1 → 0`** в iris_with_gimbal SDF (попытка обойти deadlock в
   gz-sim 8.11) — без эффекта.

## Hypothesis (не verified)

`gz-sim 8.11.0` изменил semantics `Simulator::Update` callback или
`SystemPostUpdate` interface, и `ArduPilotPlugin::PostUpdate` либо никогда
не вызывается, либо вызывается без physics-step state. Plugin получает
servos на UDP 9002, но не имеет valid IMU/GPS snapshot для отправки.

В changelog gz-sim 8.11 могут быть breaking changes (см. `gazebosim/gz-sim`
GitHub releases). Это **upstream regression**, не наш код.

## Что предлагаю как путь вперёд (для Codex / следующей сессии)

### Вариант A: pin gz-harmonic apt version

Найти последний known-working `libgz-sim8` (вероятно 8.10.x) в OSRF apt
репозитории, и pin в Dockerfile:
```dockerfile
RUN apt-get install -y \
    libgz-sim8=8.10.0-1~jammy \
    gz-harmonic=...   # точная version
```

Сложность: OSRF apt репозитории обычно держат только latest. Возможно
нужен `archive.osrfoundation.org` или Wayback download.

### Вариант B: tcpdump debug

Запустить tcpdump в bas-uav netns на lo, увидеть actual UDP трафик:
```bash
ip netns exec bas-uav tcpdump -i lo -nn -X udp port 9002 or udp port 9003
```

Если SITL шлёт но Plugin не отвечает — confirm hypothesis #1.
Если Plugin отвечает но SITL не получает — другая проблема.

### Вариант C: альтернативный ArduPilot SITL frame

Попробовать `--frame=gazebo-iris` или `--frame=quad` или `--frame=copter`,
с другими FDM портами. См. ArduPilot SITL command-line.

### Вариант D: запустить через `gazebo classic` (Gazebo 11)

Старая Gazebo 11 имела стабильный ardupilot_gazebo plugin path. Это
архитектурный пересмотр — `bas/gazebo-classic:dev` image.

## Что у нас УЖЕ работает несмотря на эту регрессию

- **Sionna RT synthetic** (`scripts/run_stage_2_1_synthetic.sh`) — НЕ зависит
  от SITL/Gazebo. Полный proof of concept ray-traced radio chain.
  Артефакт: `logs/stage_2_1_synthetic_20260517T151013Z/sionna_overview.png`.
- **LoRa serial PTY bridge** (этап 1.7.a-1.7.f). Полный stack: ns-3 lorawan
  + dual-socat + orchestrator serial endpoint + analyzer LoraSerialMetrics.
  Не зависит от Gazebo физики.
- **ns-3 lorawan PHY+MAC simulation** работает изолированно.

## Команда для текущего демо (камера / mission НЕ работает)

```bash
# Sionna synthetic (работает гарантированно, ~1 минута):
sionna_env/bin/python scripts/demo_sionna_pipeline.py --save-plot
xdg-open logs/sionna_demo/trajectory_loss.png

# Mission AUTO через ns-3 в stub-режиме (без Gazebo):
bas-orchestrator baseline_wifi
```

Mission через SITL+Gazebo+ns-3+camera **сейчас не работает** до решения
gz-sim 8.11 regression.
