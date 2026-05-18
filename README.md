# Среда моделирования БАС - первый прототип

Минимально реализуемый стенд по архитектуре из аналитического документа Физулина А.В.: воспроизводимый прогон сценария БАС с полётным контуром, сетевыми деградациями, потоком данных и единым журналом.

## Ядро (этап 1)

```
+--------------+   +------------------+   +-------------+   +--------+
|  scenario    |   |   Gazebo Sim     |   |  ArduPilot  |   |  ns-3  |
| orchestrator |-->|    Harmonic      |<->|    SITL     |<->|  RT    |
|  (Python)    |   | (физика, сцена)  |   |  (полёт)    |   |  (сеть)|
+------+-------+   +--------+---------+   +------+------+   +---+----+
       |                    |                    |              |
       v                    v                    v              v
       +-----------------> JSONL единый журнал <-----------------+
                                  |
                                  v
                            +-----------+
                            | analyzer  |
                            | (метрики, |
                            |  отчёт)   |
                            +-----------+
```

Подробности — в [docs/architecture.md](docs/architecture.md) (исходник в `База знаний / Первичный_анализ_переработанный.docx`).

## Состав

| Каталог | Назначение |
|---|---|
| `configs/scenarios/` | YAML-сценарии (миссия + сетевой профиль + seed) |
| `configs/network_profiles/` | Параметры ns-3 для WiFi и LoRa-подобного канала |
| `configs/missions/` | Маршруты БАС (waypoints) |
| `orchestrator/` | Python-оркестратор: подъём компонентов, run_id, единый журнал |
| `analyzer/` | Python-анализатор: PDR, throughput, delay, jitter, отчёт |
| `ns3/scenarios/` | C++ сценарий ns-3 (TapBridge, два канала) |
| `ardupilot/` | Параметры и миссии для ArduPilot SITL |
| `gazebo/worlds/` | SDF-миры для Gazebo Harmonic |
| `docker/` | Dockerfile'ы для каждого компонента |
| `logs/` | Журналы прогонов (gitignored) |
| `scripts/` | Bootstrap-скрипты (install_docker.sh и др.) |

## Быстрый старт (когда Docker уже стоит)

```bash
# собрать образы
docker compose build

# прогнать baseline-сценарий (WiFi-канал, без деградации)
./scripts/run_scenario.sh baseline_wifi

# прогнать деградированный сценарий (узкополосный канал)
./scripts/run_scenario.sh degraded_lora

# построить отчёт
python -m analyzer logs/<run_id>
```

## Состояние компонентов

| Компонент | Этап 1 | Статус |
|---|---|---|
| Оркестратор + единый JSONL-журнал | да | **работает** (stub + --real) |
| Анализатор метрик | да | **работает** (PDR/delay/jitter/outage/landed/waypoints) |
| Конфиги (YAML) | да | сценарии + сетевые профили + миссии |
| ArduPilot SITL контейнер | да | **собран** (bas/ardupilot-sitl:dev) |
| Gazebo Harmonic контейнер | да | **собран** (bas/gazebo-harmonic:dev) |
| ns-3 + TapBridge + 2 канала | да | **работает** (TapBridge UseLocal, RateErrorModel, outage, JSONL) |
| Host bridges + TAP/veth/netns | да | **готов** (`scripts/setup_radio_net.sh`) |
| Реальный полёт через MAVLink (host network) | да | **работает** (этап 1.4) |
| Shadow GCS в bas-ctrl-far netns через ns-3 | да | **работает** (этап 1.5.0) |
| Mission через ns-3, profile `wifi_good` | да | **работает** v0.7 (AUTO + UDP bridge) |
| Mission через ns-3, profile `degraded_lora` | да | **работает** v0.7 (250ms delay + 2% loss + outage) |
| Видеопоток камеры Gazebo через payload-канал | да | 1.5.2.a/b/c: камера, метрики, MP4, корреляция outage ↔ video gaps |
| Сравнительный отчёт WiFi vs LoRa | да | этап 1.6 (`bas-analyzer-compare`, side-by-side markdown + CSV) |
| Sionna RT (офлайн радиокарты) | нет | этап 2 |
| AirSim / Cosys-AirSim | нет | этап 2 |
| Несколько БАС / рой | нет | этап 2 |

## Этап 1.5: радио-петля SITL ↔ ns-3 (v0.7)

Реализован полный mission run из netns `bas-ctrl-far` через ns-3 канал в SITL+Gazebo,
размещённые в shared netns `bas-uav` (Kubernetes pause-container pattern).

Транспорт: MAVLink UDP через `mavbridge` (socat-sidecar в shared netns) обходит TCP
head-of-line blocking; mission upload протокол укреплён COUNT-burst'ом и adaptive
silence-limit'ом для устойчивости к 250ms+2% loss каналу.

```bash
# baseline wifi (~5ms delay)
sudo bash scripts/run_stage_1_5_1_mission.sh wifi_good

# degraded LoRa-подобный (250ms delay, 2% loss, 2× outage по 3s)
sudo bash scripts/run_stage_1_5_1_mission.sh degraded_lora
```

Отчёт по прогону — `logs/<run_id>/report.md`. Подробности про root causes и решения —
[docs/stage_1_5_1_known_issues.md](docs/stage_1_5_1_known_issues.md).

## Этап 1.5.2 (v0.8.1+): RTP-видео через payload-канал

Под-этап smoke реализован: H.264 RTP-поток (sender → ns-3 payload TAP → receiver)
работает на обоих профилях. Анализатор автоматически добавляет секцию
«Видеопоток» в `report.md`, если в прогоне есть `video_tx.jsonl` /
`video_rx.jsonl`: FPS, frame loss, RFC 3550 jitter, bitrate goodput,
приблизительная e2e latency и корреляция payload outage ↔ video gaps.

```bash
sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
sudo bash scripts/run_stage_1_5_2_mission.sh degraded_lora
```

По умолчанию источник видео — `videotestsrc` (smoke). Для проверки реальной
бортовой камеры Gazebo используется режим `BAS_VIDEO_SOURCE=camera`; он
включает штатную gimbal POV camera на модели `iris_with_gimbal` (upstream
ardupilot_gazebo), снимающую сцену с борта дрона — в кадре видны лопасти
ротора и тень БАС на runway, как и положено бортовой камере:

```bash
sudo service docker start
docker compose build gazebo video
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

Этот режим включает штатный `GstCameraPlugin` через Gazebo topic
`/world/iris_runway/model/iris_with_gimbal/.../pitch_link/sensor/camera/image/enable_streaming`,
затем retap'ит H.264 RTP с `127.0.0.1:5600` в payload канал ns-3. Стабильность
FDM (Gazebo ↔ SITL UDP JSON FDM) обеспечивается пиннингом gz-sim8 8.10.0 в
`docker/gazebo/Dockerfile` — без него под gz-sim 8.11 plugin update loop
ломается. Mission AUTO + Gazebo POV camera проверены в одном прогоне
(landed=True, video_rx.mp4 ≈16 МБ за 192 c полёта).

По умолчанию прогон headless: окно Gazebo не открывается, а принятый
видеопоток сохраняется в `logs/<run_id>/video_rx.mp4`. Для визуального демо
через WSLg можно открыть GUI:

```bash
sudo env BAS_VIDEO_SOURCE=camera BAS_GAZEBO_GUI=1 bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

## Этап 1.6 (v1.0): сравнительный отчёт WiFi vs LoRa

Один скрипт прогоняет оба профиля и собирает side-by-side сравнение всех
ключевых метрик (полётный контур, control / payload PDR и задержки, видео
FPS / frame loss / e2e latency, outage correlation), плюс flat CSV для
импорта в Excel/pandas:

```bash
sudo bash scripts/run_stage_1_6_compare.sh
# Перезапустит wifi_good + degraded_lora через scripts/run_stage_1_5_2_mission.sh
# и сложит в logs/stage_1_6_<UTC>/:
#   comparison.md     — markdown side-by-side
#   comparison.csv    — metric/wifi/lora/delta
#   wifi_good/        — симлинк на исходный run-dir
#   degraded_lora/    — симлинк на исходный run-dir
```

Если уже есть свежие прогоны и нужен только пересбор отчёта:

```bash
sudo env STAGE16_SKIP_RUNS=1 bash scripts/run_stage_1_6_compare.sh
# Использует последние существующие logs/stage_1_5_2_mission_*
```

Прямой вызов сравнения:

```bash
.venv/bin/bas-analyzer-compare \
    logs/stage_1_5_2_mission_wifi_good_<ts>/ \
    logs/stage_1_5_2_mission_degraded_lora_<ts>/ \
    --label-a wifi_good --label-b degraded_lora \
    --out-dir logs/stage_1_6_manual/
```

## Этап 2.1 (v2.0): Sionna RT — physically-justified radio model

Закрыты пункты ТЗ Физулина: «ns-3/Sionna RT интеграция» + «карта тестового
сценария для 3D-препятствий».

```bash
# 1. Setup Sionna в WSL (один раз, ~3.5 GB)
bash scripts/setup_sionna.sh

# 2. Сгенерировать Mitsuba scene + radio map (один раз)
sionna_env/bin/python scripts/export_scene_to_sionna.py
sionna_env/bin/python scripts/compute_radio_map.py --save-png

# 3. Запустить mission с динамическим Sionna-каналом
sudo bash scripts/run_stage_2_1_sionna.sh

# Visual demo без mission (synthetic UAV trajectory):
sionna_env/bin/python scripts/demo_sionna_pipeline.py --save-plot
# → logs/sionna_demo/trajectory_loss.png
```

См. [docs/stage_2_1_sionna_plan.md](docs/stage_2_1_sionna_plan.md) для деталей.

## Этап 1.7 (1.7.a–1.7.h): LoRa через Serial Port — буквальная реализация ТЗ

Полностью закрыто буквально по ТЗ «LoRa через Serial Port для MAVLink»:
mission AUTO с landed=True идёт **без какого-либо IP-stack в радиопетле**.
Все MAVLink commands (MISSION_COUNT, MISSION_ITEM, ARM, MISSION_START) и
telemetry (HEARTBEAT, GPS_RAW_INT, ATTITUDE, GLOBAL_POSITION_INT, MISSION_CURRENT)
ходят через виртуальный Serial Port + LoRa PHY-калиброванный канал в ns-3.

```
  host orchestrator (pymavlink serial:/tmp/ptyGCS_lora:57600)
       ↕ host socat: /tmp/ptyGCS_lora ↔ /tmp/bas-bridge/lora-gcs.sock
       ↕ ns-3 контейнер: container-side socat UNIX-CONNECT ↔ PTY
       ↕ ns-3 GCS NetDevice (PointToPoint) ← PollAndSend ← /tmp/ptyGCS_lora
       ↕ ns-3 LoRa channel — SX1276 калиброван:
           data_rate=5470 bps (SF7/BW125 по Semtech datasheet Table 12)
           airtime=50ms (по LoRa airtime формуле для 64-байт payload)
           PER=0.01 для distance=1000m (Augustin et al. 2016, log-distance n=3.76)
       ↕ ns-3 UAV NetDevice (симметрично) → /tmp/ptyUAV_lora
       ↕ ns-3 контейнер: container-side socat PTY ↔ UNIX-LISTEN
       ↕ bas-lora-uav-bridge (alpine/socat в bas-uav netns)
       ↕ SITL primary serial TCP 5760
```

Параметры SF/BW/distance настраиваются через env (SX1276 PHY-калибровка
пересчитывается автоматически по LoRa airtime формуле):

```bash
sudo bash scripts/run_stage_1_7_lora_serial.sh
# или с настройками канала:
sudo env BAS_LORA_SF=9 BAS_LORA_DISTANCE_M=3000 bash scripts/run_stage_1_7_lora_serial.sh
```

**Acceptance прогон** (`logs/stage_1_7_lora_serial_lora_serial_20260518T170631Z`):
- `report.md`: status=success, **landed=True**, **7/7 waypoints**, 252.2 м, 30.0 м max altitude
- `lora_gcs_tx` (orchestrator → SITL): 35 packets, **PDR=1.000** — все mission команды доставлены
- `lora_uav_tx` (SITL → orchestrator): 442 packets, PDR=0.991, 1.63% byte_loss (точно как заложено калибровкой 1% PER для 1000м)

Артефакты в `logs/stage_1_7_lora_serial_*/`:

- `report.md` — flight, network flows, LoRa serial канал секция
- `events.jsonl` — MAVLink telemetry + mission events
- `ns3_events.jsonl` — события `ns3:lorawan` (pty_read, pty_write, network)
- `orchestrator_stdout.log` — orchestrator + mission upload trace

**Полностью PHY-correct legacy baseline** на signetlabdei/lorawan ED Class A
(ITU-R RP.452 path loss, LoRa modulation) сохранён в
[`ns3/scenarios/lora_serial_lorawan.cc`](ns3/scenarios/lora_serial_lorawan.cc)
для PHY-точных telemetry-only демонстраций. Class A half-duplex недостаточен
для bi-directional mission upload, поэтому acceptance путь использует
PHY-калиброванный PointToPoint (1.7.h).

См. [docs/stage_1_7_lora_serial_plan.md](docs/stage_1_7_lora_serial_plan.md)
и [docs/tz_compliance.md](docs/tz_compliance.md).

## Дальнейшие шаги (после сверки с ТЗ от 17.05.2026)

См. полный roadmap в [docs/roadmap.md](docs/roadmap.md) и матрицу
соответствия ТЗ в [docs/tz_compliance.md](docs/tz_compliance.md).

1. **1.8 ROS2/MAVROS bridge** — runtime-переключение `--mavlink-backend pymavlink|mavros`,
   текущий pymavlink-код остаётся
2. **2.4 Ручное управление через QGroundControl/MAVProxy**
3. **2.3 Multi-UAV / рой**
4. **2.2 AirSim как overlay над Gazebo физикой** (совместно с Федотенковым)
