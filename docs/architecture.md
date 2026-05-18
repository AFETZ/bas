# Архитектура первого прототипа

Краткая выжимка из `Первичный_анализ_переработанный.docx` с уточнениями по реализации.

## Контуры

1. **Сценарный оркестратор** (`orchestrator/`).
   Создаёт `run_id`, читает конфиг, поднимает компоненты, держит главный цикл, гасит компоненты по завершению, пишет события в общий журнал.

2. **Полётный контур.**
   - Реально: Gazebo Sim Harmonic ↔ ardupilot_gazebo plugin ↔ ArduPilot SITL.
   - На этапе 1 (stub): `orchestrator.components.StubGazeboArduPilot` эмулирует движение по waypoints без физики.

3. **Сетевой контур.**
   - Реально: ns-3 в режиме реального времени + TapBridge + два TAP-моста (control, payload). RateErrorModel + DelayJitterEstimator. Outage'и через `Simulator::Schedule`.
   - На этапе 1 (stub): `orchestrator.components.StubNs3Channel` эмулирует доставку пакетов согласно профилю (delay, jitter, loss, outage).

4. **Поток полезной нагрузки.**
   - Реально: ffmpeg/GStreamer стрим камеры Gazebo → отдельный TAP → ns-3 → приёмник в host.
   - На этапе 1: stub отправляет пакеты с частотой 30 Hz и размером 1200 байт.

5. **Логирование.**
   - Единый JSONL `logs/<run_id>/events.jsonl`. Схема событий описана в `orchestrator/src/orchestrator/logger.py` (EVENT_TYPES).
   - ns-3 пишет в тот же каталог `ns3_events.jsonl` (этап 2).

6. **Анализатор.**
   - `analyzer/` читает JSONL, считает PDR, задержку, jitter, goodput, проверяет успешность миссии. Сохраняет markdown-отчёт.

## Схема событий журнала

Соответствует таблице 5 архитектурного документа.

| event_type | основные поля |
|---|---|
| `run_start` | run_id, scenario_id, config_hash, seed, versions, control_profile, payload_profile, mission |
| `run_end` | run_id, scenario_id |
| `scenario` | sim_time, status (success / timeout / failure), reason |
| `flight` | sim_time, vehicle_id, position{x,y,z}, velocity_mps, flight_mode, mission_state, waypoint_index |
| `control_telemetry` | (зарезервировано на этап 2 - событие команды/телеметрии в host-приложении) |
| `network` | sim_time, flow_id, packet_id, tx_time, rx_time, drop_reason, delay, jitter, throughput_bps, outage_state |
| `payload` | sim_time, flow_id, payload_id, capture_time, send_time, receive_time, size_bytes, drop_reason |
| `sync` | sim_time, gazebo_time, ns3_time, real_time_factor, position_desync_m |
| `component` | component, phase (start / stop), произвольные поля |

## Карта файлов под зону ответственности Физулина А.В.

Точные формулировки из ТЗ (см. также `docs/tz_compliance.md` для матрицы
соответствия). Физулин А.В. отвечает за:

1. **Два канала связи по стандартным протоколам (управление + видеопоток):**
   - **WiFi (TCP/IP)** — закрыт в этапе 1 (v0.7 mission + v0.9 video).
     → `configs/network_profiles/wifi_good.yaml`
     → `ns3/scenarios/two_channel.cc` (UDP MAVLink + RTP H.264 через TapBridge)
   - **LoRa через Serial Port / LoRaWAN** — намечено в этапе **1.7**.
     Требуется **буквальная** реализация: virtual PTY (pseudo-terminal),
     MAVLink-байтстрим через ns-3 SerialChannel, frame size ≤256B, baud-rate
     pacing (9600-115200), без IP-stack.
     → `ns3/scenarios/lora_serial.cc` (новый)
     → `scripts/run_stage_1_7_lora_serial.sh` (новый)
   Stub-режим: `orchestrator.components.StubNs3Channel`.

2. **MAVROS / ROS2 интерфейс к ArduPilot SITL (обязательный по ТЗ):**
   Намечено в этапе **1.8**. Runtime-переключение между backend'ами:
   `--mavlink-backend pymavlink` (текущий) и `--mavlink-backend mavros`.
   MAVROS-нода в `bas-ctrl-far` netns, rosbag-логи как дополнение к JSONL.
   Текущая `pymavlink`-реализация **не ломается** — оба пути остаются доступны.

3. **ns-3 / Sionna RT (error rate, распределение ошибок, пропускная способность):**
   - **ns-3** — закрыт в этапе 1 (TapBridge + RateErrorModel + outage + delay/jitter
     measurements в `analyzer/src/analyzer/metrics.py`).
   - **Sionna RT (обязательный по ТЗ)** — намечено в этапе **2.1**.
     Офлайн-расчёт радиокарт по 3D-сцене Gazebo → таблица
     `path_loss(x,y,z)`, `delay(x,y,z)` → подаётся как dynamic parameter
     в новый `SionnaErrorModel` в ns-3 (table-lookup по позиции UAV из
     Gazebo). Заменяет статичный `RateErrorModel` на physically-justified.

4. **Карта тестового сценария в ns-3/Sionna RT для 3D-препятствий
   (обязательный по ТЗ):**
   В составе 2.1: экспорт `iris_runway` SDF → glTF/PLY для Sionna scene
   (iris + runway + здания/деревья как препятствия).
   → `gazebo/worlds/basic.sdf`, `iris_runway.sdf` уже определяют геометрию;
     2.1.b добавит экспорт-скрипт `scripts/export_scene_to_sionna.py`.

5. **Совместно с Андрончевым и Карповым — моделирование + ручное
   управление одним БАС:**
   - Автоматическое mission AUTO — закрыто в этапе 1 (v0.7).
   - Ручное управление через GCS — намечено в этапе **2.4** (QGroundControl
     /MAVProxy через mavbridge + ns-3 control канал).

## Этапы

| Этап | Содержание | Состояние |
|---|---|---|
| 1.0 | Skeleton: оркестратор, журнал, анализатор, stub-компоненты, конфиги | **готов** |
| 1.1 | Docker в WSL, базовые образы (ArduPilot SITL, Gazebo Harmonic, ns-3) | **готов** |
| 1.2 | ArduPilot SITL ↔ Gazebo Harmonic через ardupilot_gazebo plugin | **готов** |
| 1.3 | ns-3 `two_channel.cc` с TapBridge + два моста (control, payload) | **готов** |
| 1.4 | MAVLink команды и телеметрия через host network (без ns-3) | **готов** (v0.1) |
| 1.5.0 | Shadow GCS в bas-ctrl-far netns через ns-3 control TAP | **готов** (v0.2) |
| 1.5.1 | Полная mission через ns-3 control канал, `wifi_good` + `degraded_lora` | **готов** (v0.7) |
| 1.5.2.a | RTP H.264 видео-pipeline (videotestsrc) через ns-3 payload TAP | **готов** (v0.8) |
| 1.5.2.a-metrics | VideoMetrics в анализаторе (FPS/e2e latency/frame loss/jitter) | **готов** (v0.8.1) |
| 1.5.2.b | Реальная Gazebo-камера через GstCameraPlugin (`BAS_VIDEO_SOURCE=camera`) | **готов** |
| 1.5.2.c | Корреляция payload outage ↔ video RX gaps в `report.md` | **готов** |
| 1.5.2.d | Точная e2e latency через GstPadProbe на `udpsink:sink` / `udpsrc:src` + min-latency метрика | **готов** (v0.9) |
| 1.6 | Сравнительный отчёт WiFi vs LoRa (`bas-analyzer-compare`, side-by-side markdown + CSV) | **готов** (v1.0) |
| **1.7** | **LoRa через Serial Port (virtual PTY + ns-3 SerialChannel)** — буквальная реализация требования ТЗ Физулина | намечено |
| **1.8** | **ROS2/MAVROS bridge** с runtime-переключением `--mavlink-backend pymavlink\|mavros` | намечено |
| 2.1 | **Sionna RT** — обязательный пункт ТЗ; физически обоснованная радиокарта вместо RateErrorModel | **готов** (v2.0) |
| 2.2 | AirSim/Cosys-AirSim **как overlay над Gazebo физикой** (Gazebo→AirSim bridge для realism, не замена) | намечено |
| 2.3 | Несколько БАС / рой (multi-UAV в одной ns-3 сети) | намечено |
| 2.4 | Ручное управление через QGroundControl/MAVProxy (совместная задача с Андрончевым/Карповым) | намечено |

## Stub vs Real

`bas-orchestrator <scenario>` запускает в stub-режиме (без Docker). Это нужно для отладки оркестратора и анализатора. Stub детерминированно эмулирует прогон по тому же контракту событий.

`bas-orchestrator <scenario> --real` поднимает `docker compose up`, дожидается SITL/Gazebo и ведёт реальный mission run. С флагом `--external-compose` оркестратор не поднимает compose сам, а подключается к уже работающему стеку (используется в `run_stage_1_5_1_mission.sh` где compose стартуется снаружи, чтобы инжектировать veth в shared netns).

## Транспорт MAVLink (v0.7)

Между SITL и orchestrator'ом в `bas-ctrl-far` стоит `mavbridge` (alpine/socat) в shared netns `bas-uav`: UDP 14550 ↔ TCP 5760 (SITL). Радиоканал ns-3 переносит только UDP — это устраняет TCP head-of-line blocking при больших RTT и потерях. TCP остаётся только локально между socat и SITL внутри netns, где он стабилен.

Mission upload использует AUTO-mode: HOME + TAKEOFF + waypoints + LAND загружаются в ArduPilot одним пакетом, дальше автопилот летит сам. Протокол укреплён MISSION_COUNT-burst'ом (5×), adaptive silence-limit'ом (15s до первого request / 60s после) и anti-stale/anti-duplicate фильтрами на стороне orchestrator'а.

## Известные упрощения этапа 1

- Видеопоток в payload-канале по умолчанию синтетический (`videotestsrc`); режим `BAS_VIDEO_SOURCE=camera` подключает штатную onboard gimbal POV camera на модели `iris_with_gimbal` (upstream ardupilot_gazebo) через `GstCameraPlugin`. Стабильность FDM (Gazebo ↔ SITL UDP JSON) держится на pin'е gz-sim8 8.10.0 в `docker/gazebo/Dockerfile` — без него под gz-sim 8.11 plugin update loop теряет JSON sensor packets и mission не стартует.
- Stub-режим оставлен для отладки оркестратора и анализатора (без Docker).
- Sionna RT и AirSim — вне scope этапа 1.
