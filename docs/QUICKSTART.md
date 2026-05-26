# Quick Start — список рабочих команд

Все команды требуют `sudo` потому что они трогают сетевые namespace и Docker.
Параметры через env переменные (`sudo env VAR=val bash ...`).

## Самые показательные демо

### 🎬 Auto demo — Playwright + ffmpeg recording (рекомендую)

```bash
sudo bash scripts/run_stage_2_4_auto_demo.sh
```

**Что происходит**: запускает FPV+RF stack в background, открывает headless
Chrome через Playwright, выполняет 10-шаговую траекторию полёта (TAKEOFF →
GOTO к ангару LOS → GOTO за ангар NLOS → возврат → LAND), параллельно пишет
видео Web GCS (.webm) и FPV stream (.mp4), снимает screenshots в ключевые
моменты, генерирует `demo_report.md` с timeline.

**Выход**:
```
logs/<run>/demo_report.md          ← Markdown с timeline + ссылками
logs/<run>/video/web_gcs.webm      ← Playwright capture
logs/<run>/video/fpv.mjpeg.mp4     ← ffmpeg capture
logs/<run>/screenshots/*.png       ← Web GCS в каждой waypoint
logs/<run>/screenshots/*_fpv.jpg   ← FPV-кадры
```

### 🎥 FPV + RF live demo (ручное управление)

```bash
sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh
```

Открыть `http://127.0.0.1:8765/`. Управление:

| Клавиша | Действие |
|---|---|
| W/A/S/D или ↑↓←→ или IJKL | горизонтальный velocity |
| **Space** | подъём (climb) |
| **Ctrl** | снижение (descend) |
| Escape | STOP |
| **F** | toggle FPV overlay |
| `⤢` button | развернуть FPV на весь блок |

На карте: дрон-маркер, target (если GOTO активен), ангар + башня
(препятствия). RF panel показывает LOS/NLOS pill, RSSI график, loss/delay.

### 📡 Online Sionna RT — live ray tracing

```bash
sudo bash scripts/run_stage_2_4_rt_online_demo.sh
```

То же что FPV+RF, но Sionna RT делает **real PathSolver call на каждую
позицию UAV** (вместо offline lookup). ns-3 деформирует **control + payload**
синхронно — за зданием падают оба канала.

### 🎮 QGroundControl + Web GCS одновременно

```bash
sudo bash scripts/run_stage_2_4_qgc_demo.sh
```

Запускает mavp2p MAVLink router вместо mavbridge. В Windows QGroundControl
**Application Settings → Comm Links → Add → UDP**:
- Port: `14560`
- Server: WSL eth0 IP (распечатается в консоли)

Подключиться — Heartbeat появится сразу. Web GCS остаётся работать на 8765.

### 🛩️ Multi-UAV (2 SITL + 2 iris)

```bash
sudo bash scripts/run_stage_2_4_multi_uav_demo.sh
```

2 ArduCopter SITL (`-I0 sysid=1, -I1 sysid=2`), 2 iris модели в Gazebo на
разных fdm_port (9002/9012), единый mavp2p router. MAVProxy видит обе
системы.

### 🌍 Cosys-AirSim overlay (real GPU rendering)

Сначала одноразовый firewall rule в Windows admin PowerShell:
```powershell
New-NetFirewallRule -DisplayName "CosysAirSim 41451" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 41451
```

```bash
sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh
```

Wrapper:
1. Скачает `Blocks_packaged_Windows_55_33.zip` (556 MB) → `/mnt/c/Users/$USER/cosys-airsim/`
2. Распакует через PowerShell
3. Создаст AirSim settings.json в Windows-side Documents/AirSim
4. Запустит Blocks.exe через cmd.exe interop
5. Подключит bridge на Windows host IP (Hyper-V vEthernet)

`simGetImage` теперь возвращает **real PNG-кадры** от UE5 + NVIDIA GPU
(verified 7 cameras: front_center, fpv, back_center, и др.).

Альтернативные modes:
```bash
# Headless Linux UE5 (-nullrhi, pose API ok, image API empty)
sudo env BAS_AIRSIM_MODE=linux bash scripts/run_stage_2_2_airsim_overlay.sh

# Stub-сервер (CI smoke, без UE5 binary)
sudo bash scripts/run_stage_2_2_airsim_overlay.sh

# Уже запущенный external AirSim
sudo env BAS_AIRSIM_MODE=off BAS_AIRSIM_HOST=<ip> bash scripts/run_stage_2_2_airsim_overlay.sh
```

### 🔁 Stage 4 ArduPilot JSON-FDM bridge

```bash
# Fast CI-grade bridge smoke: router + JSON-FDM physics loop
bash scripts/run_stage_4_sim_bridges_demo.sh smoke

# Real ArduCopter SITL closed-loop: wire protocol + ARM + takeoff
.venv/bin/python scripts/_real_sitl_e2e_smoke.py
```

Что проверяется во втором прогоне:
- запускается real `~/ardupilot/build/sitl/bin/arducopter --model json:127.0.0.1`;
- `JsonFdmBridge` принимает binary `servo_packet_16` PWM и отвечает sensor JSON;
- MAVLink TCP `:5760` отдаёт `HEARTBEAT`, `ATTITUDE`, valid `GLOBAL_POSITION_INT`;
- `STABILIZE → force ARM → RC throttle` приводит к climb >0.5 м и PWM > hover.

Если binary ещё нет:
```bash
bash scripts/install_ardupilot.sh
```

## Acceptance smoke тесты (для CI)

### Stage 4 — ArduPilot ↔ AirSim JSON-FDM / MAVLink bridges

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh smoke
# Router smoke + JSON-FDM smoke:
#   340 PWM frames sent, sensor responses valid,
#   climb phase >2m, yaw phase rotates vehicle

.venv/bin/python scripts/_real_sitl_e2e_smoke.py
# Requires local ArduPilot binary.
# Expected proof: ARMED=True, Takeoff delta >0.5m, Max PWM > hover.
```

### Stage 1.5.2 — mission AUTO + camera + RTP video

```bash
sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
# Артефакты:
#   logs/<run>/report.md          — 7/7 wp, 252 м, AUTO→LAND
#   logs/<run>/video_rx.mp4       — ~16 MB записанного RTP/H.264
```

### Stage 1.7 — LoRa через Serial Port

```bash
sudo bash scripts/run_stage_1_7_lora_serial.sh
# PHY-calibrated PointToPoint SX1276, без IP в радиопетле
# Артефакт: lora_gcs_tx PDR=1.000, lora_uav_tx PDR≈0.99
```

### Stage 1.8 — ROS2/MAVROS backend

```bash
sudo bash scripts/run_stage_1_8_mavros.sh baseline_wifi
# Артефакт: 7/7 waypoints через mavros_msgs/srv/WaypointPush + force-arm
# 575 samples, distance 253м, max_alt 30м
```

### Stage 1.6 — WiFi vs LoRa comparison

```bash
sudo bash scripts/run_stage_1_6_compare.sh
# Запускает 1.5.2 на обоих профилях + analyzer comparison.md + comparison.csv
```

## Environment variables — что можно крутить

### Общие (всех Stage 2.4 wrappers)

| Env | Default | Что |
|---|---|---|
| `BAS_GAZEBO_GUI` | `0` (1 в RF demo) | Открыть Gazebo окно через WSLg |
| `BAS_STAGE24_FORCE_ARM` | `1` | Force-arm через CommandLong magic 21196 |
| `BAS_GCS_UI_HOST` | `127.0.0.1` | UI bind адрес |
| `BAS_GCS_UI_PORT` | `8765` | UI HTTP порт |
| `BAS_STAGE24_TAKEOFF_ALT` | `10` | Высота takeoff в метрах |

### FPV

| Env | Default | Что |
|---|---|---|
| `BAS_GCS_FPV` | `0` (1 в fpv_rf/rt_online) | Включить FPV overlay |
| `BAS_FPV_MJPEG_PORT` | `8766` | MJPEG TCP server port в bas-uav netns |
| `BAS_CAMERA_UDP_PORT` | `5600` | RTP H.264 UDP listen port от Gazebo |
| `BAS_FPV_FPS` | `15` | Target FPS |
| `BAS_FPV_QUALITY` | `70` | JPEG quality 1-100 |

### RF / Sionna

| Env | Default | Что |
|---|---|---|
| `BAS_GCS_RF_DEMO` | `0` | RF panel + obstacles |
| `BAS_RF_CHANNEL_PATH` | `/tmp/bas_stage24_rf.json` | Web UI rf_loop JSON output |
| `BAS_SIONNA_CHANNEL_PATH` | =RF | ns-3 polling source |
| `BAS_SIONNA_TARGET_FLOW` | `payload` | `payload` / `control` / **`both`** |
| `BAS_SIONNA_RT_ONLINE` | `0` | Live RT publisher вместо radio map |
| `BAS_RT_SCENE_PATH` | `scene/iris_runway.xml` | Mitsuba scene |
| `BAS_RT_MAX_DEPTH` | `2` | PathSolver reflection depth |

### QGC

| Env | Default | Что |
|---|---|---|
| `BAS_GCS_QGC` | `0` | Включить QGC mode (mavrouter вместо mavbridge) |
| `BAS_QGC_HOST_PORT` | `14560` | UDP relay порт на хосте для QGC |

### Multi-UAV

| Env | Default | Что |
|---|---|---|
| `BAS_GCS_MULTI_UAV` | `0` | 2-й SITL + iris_runway_multi.sdf |

### AirSim

| Env | Default | Что |
|---|---|---|
| `BAS_AIRSIM_MODE` | `stub` | `stub` / `linux` / `windows` / `off` |
| `BAS_AIRSIM_HOST` | `127.0.0.1` | для `off` режима — IP внешнего AirSim |
| `BAS_AIRSIM_PORT` | `41451` | msgpack-rpc port |
| `BAS_AIRSIM_CAMERA` | `front_center` | какую камеру pull |
| `BAS_AIRSIM_IMAGE_PERIOD_S` | `2` | период camera snapshot |

## Часто используемые комбинации

```bash
# 1. Полный показательный demo с auto-recording
sudo bash scripts/run_stage_2_4_auto_demo.sh

# 2. Полный stack с GUI Gazebo (для записи видео экрана)
sudo env BAS_GAZEBO_GUI=1 bash scripts/run_stage_2_4_fpv_rf_demo.sh

# 3. RF demo + live Sionna RT (тяжёлый, требует sionna_env)
sudo env BAS_GAZEBO_GUI=1 bash scripts/run_stage_2_4_rt_online_demo.sh

# 4. Multi-UAV + QGC (одновременно 2 SITL и QGC GUI)
sudo env BAS_GCS_QGC=1 BAS_GCS_MULTI_UAV=1 \
     bash scripts/run_stage_2_4_mavproxy_gcs.sh ui

# 5. AirSim overlay + Web GCS параллельно (real GPU)
sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh
```

## Утилиты

```bash
# Текущие per-run артефакты
ls -t logs/ | head -3

# Post-mortem отчёт по последнему прогону
./.venv/bin/bas-analyzer logs/$(ls -t logs/ | head -1)

# Снять весь стенд
sudo bash scripts/setup_radio_net.sh down
sudo docker compose -f docker-compose.shared-netns.yml --profile fpv --profile qgc --profile multi down -v

# Live tail событий
tail -f logs/$(ls -t logs/ | head -1)/events.jsonl | jq -c .
```
