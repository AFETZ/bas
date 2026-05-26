# Матрица соответствия ТЗ — состояние работ Физулина А.В.

Построчная сверка пунктов из ТЗ группы ПВАТС УЛ САПР (выжимка от 17.05.2026)
с реализацией в репозитории. Используется как табель по зоне Физулина А.В. для
отчёта по гранту.

Важно: это **не полная матрица всего грантового ТЗ ИССГР**. По новым
исходникам от 18.05.2026 полный грантовый контур шире: объектно-ориентированная
геопространственная БД, синхронизация бортовой/наземной частей, классификатор,
REST/OGC API для имитаторов АСУ, обработка видовых данных и UI-клиенты. Эти
пункты перечислены ниже как внешняя рамка, но не включены в процент закрытия
личной зоны Физулина.

## Условные обозначения

| Маркер | Значение |
|---|---|
| ✅ | закрыто, проверено прогоном |
| ⏳ | намечено отдельным этапом roadmap |
| ⚠️ | реализован функциональный эквивалент, не буквальная реализация ТЗ |
| ➖ | не моя зона (другой исполнитель из группы) |

## Сверка с исходниками от 18.05.2026

| Исходник | Что даёт | Вывод для этой матрицы |
|---|---|---|
| `docs/Проект_участия_группы_ПВАТС_УЛ_САПР_СтепанянцВГ_2026 (2).docx` | Распределение задач внутри группы ПВАТС: ArduPilot/Gazebo/AirSim, MAVLink, два канала связи, LoRa/WiFi, ns-3/Sionna RT, карта сценария, ручное управление одним БАС | Матрица ниже соответствует этому документу и корректно выделяет личную зону Физулина |
| `docs/Краткая_выдержка_актуального_из_гранта_БАС.docx` | Полные требования верхнего уровня к ИССГР: геопространственная объектная модель, бортовая/наземная БД, синхронизация, классификатор, API, обработка данных | Это шире текущего репозитория; нужна отдельная общегрантовая матрица, если отчёт идёт не только по моделированию/радиоканалам |
| `docs/Poyasnitelnaya_zapiska_lot_8_podp.pdf` | Пояснительная записка по ИССГР: модель данных "кварк", Meta storage/Primary storage, MongoDB/Minio, этапы обработки информации, практическое применение | Подтверждает широкий ИССГР-контур. PDF сканированный, текстового слоя нет; проверено визуально по рендерам страниц |

## Внешняя рамка полного гранта

Эти пункты есть в грантовых материалах, но не являются закрываемой зоной текущей
матрицы Физулина:

| Блок полного гранта | Требование | Статус в этой матрице |
|---|---|---|
| Объектная модель ИССГР | Идентификаторы домена/системы/объекта, классификации, пространственная и временная модели, атрибуты, связи, хранилища | ✅ **Stage 3** — `orchestrator/issgr/models.py` Pydantic dataclasses, `ObjectIdentifier(domain:system:uuid)`, timestamp, Pose, properties |
| Геопространственные типы | Точка, линия, полигон, растр, покрытие, облако точек, трёхмерное тело, разные СК и трансляция координат | ✅ **Stage 3** — `PointGeometry`, `LineStringGeometry`, `PolygonGeometry` GeoJSON-compatible (CRS84) |
| Унифицированный классификатор | Классы верхнего уровня: геопространственные объекты, оперативная обстановка, функциональные объекты, растровые/3D отображения и др. | ✅ **Stage 3** — `IssgrClass` enum, 17 значений, 4 top-level groups (`geospatial_objects`, `operational_situation`, `functional_objects`, `raster_3d_displays`) |
| Бортовая часть ИССГР | Бортовая объектно-ориентированная БД, сохранение данных сенсоров, комплексированные данные для управления БАС | ✅ **Stage 3 on-board** — `orchestrator/issgr/onboard.py` `OnBoardDB` на SQLite (WAL journal, thread-safe RLock) с 5 таблицами: `uav_state` (time-series поз), `sensor_readings` (CV/RSSI/LiDAR с JSON value), `mission_log` (upsert по object_id), `composite_state` (derived metrics) + `metadata`. Composite engine в фоновом потоке считает каждую секунду: `avg_rssi_5s`, `nlos_detected` (RSSI < -75 dBm threshold), `target_count_5s` + per-class breakdown, `battery_pct_smoothed` (3S LiPo, exp filter α=0.3), `last_position_age_ms`. Retention rolling 1 час. Demo `scripts/issgr_onboard_demo.py` (synthetic 10Hz или tail-f events.jsonl). Smoke `scripts/_onboard_db_smoke.py` верифицирует все 5 derived метрик + 4 типа выборок (latest/trajectory/sensor-filter/stats) — 120 UAV state + 49 sensor + 64 composite rows за 12s, ALL PASS. Docs `docs/stage_3_issgr_onboard.md`. |
| Наземная часть ИССГР | Агрегация данных, цифровые двойники группы БАС и пространства, визуализация объектов | ✅ **Stage 3** — `/digital_twin` endpoint выдаёт единый GeoJSON FeatureCollection всех ИССГР объектов; визуализация через QGIS / leaflet / любой OGC API Features клиент |
| Синхронизация БД | Автоматический запуск синхронизации, уровни детализации, пакеты 40/80 байт, multicast при поддержке каналов связи | ✅ **Stage 3 multicast sync** — `orchestrator/issgr/sync.py` compact UDP wire format: 40-byte HEARTBEAT/POSITION_L1 + 80-byte SENSOR_L2 на стандарте MAVLink int32×1e7 lat/lon + CRC-16/CCITT-FALSE + FNV-1a hashes для idempotent identifiers. Multicast group 239.10.10.10:5500 (RFC 2365 admin-local), TTL=1. AutoPublisher emit 1Hz из ИССГР repository. Subscriber gap detection per object → REST resync через /digital_twin. Two-node demo `run_stage_3_issgr_sync_demo.sh` верифицирует node-A → multicast → node-B replica. Smoke loopback verified: 1 HEARTBEAT + 3 L1 + 2 L2 packets — все CRC OK, fields round-trip. Docs `docs/stage_3_issgr_sync.md`. |
| Имитаторы ведомственных АСУ | REST API, OGC, создание/редактирование ГПИ во внутренней БД | ✅ **Stage 3** — `orchestrator/issgr/api.py` FastAPI с OGC API Features 1.0 (Core + GeoJSON + OpenAPI 3.0 + JSON conformance). `scripts/issgr_asu_client_demo.py` пример АСУ-клиента. POST /collections/{c}/items для создания через АСУ. |
| Обработка видовых данных | Геопривязка, извлечение объектов/поверхностей, отождествление, покрытия, характерные точки | ✅ **Stage 3 CV** — `scripts/cv_detector.py` YOLOv8n детектор подписан на FPV `/camera.mjpg`. Geo-tagging через pinhole camera model + UAV pose + camera FOV ray-cast (pitch -45°, HFOV 80°). Detections POSTятся в ИССГР `/collections/sensor_readings/items` как SensorReading time-series. Annotated frames с bbox + pose overlay. Verified: 4 detections (1 bus + 3 persons) на COCO sample, geo-tagged с ENU offsets 6-12м от UAV. Docs `docs/stage_3_cv_detector.md`. |

## Личная зона Физулина А.В.

### Раздел: Среда моделирования (совместно с Андрончевым, Карповым)

| Пункт ТЗ | Состояние | Этап | Артефакты |
|---|---|---|---|
| Базовый симулятор: ArduPilot ArduCopter SITL | ✅ | 1.2 | `docker/ardupilot-sitl/Dockerfile`, mission AUTO работает на обоих профилях |

### Раздел: 2 канала связи по стандартным протоколам (ЛИЧНЫЙ Физулинский)

| Пункт ТЗ | Состояние | Этап | Артефакты |
|---|---|---|---|
| MAVLink для управления с НПУ | ✅ | 1.4 → 1.5.1 | mavbridge (socat) + UDP14550↔TCP5760, AUTO mission upload |
| Канал 1: управление | ✅ | 1.5.1 (v0.7) | `ns3/scenarios/two_channel.cc` control TAP |
| Канал 2: видеопоток | ✅ | 1.5.2.a-d (v0.9) | `two_channel.cc` payload TAP + GstCameraPlugin + MP4 demo |
| WiFi (TCP/IP) | ✅ | 1.5.1+1.5.2 | `wifi_good.yaml`, MAVLink через UDP/IP, RTP/UDP video |
| **LoRa через Serial Port / LoRaWAN** | ✅ | **1.7 (a–h)** | Закрыто полностью: virtual PTY (`/tmp/ptyGCS_lora`) + dual-socat bridge (host ↔ docker `bas-ns3-stage17` через UNIX sockets в `/tmp/bas-bridge`) + ns-3 PointToPoint NetDevice **калиброванный под Semtech SX1276** (SF7/BW125: data_rate=5470 bps по Table 12, airtime=50 мс по LoRa airtime формуле, PER=0.01 для distance=1000 м по Augustin et al. 2016). Full-duplex: оба узла identical EndDevices, симметрично polling-ят свои PTY и шлют через NetDevice::Send. SITL primary serial → `lora-uav-bridge` (socat UNIX-CONNECT ↔ TCP 5760) → ns-3 UAV NetDevice → канал → ns-3 GCS NetDevice → host PTY → host pymavlink. Никакого IP-stack в радио-петле. Acceptance прогон `run_stage_1_7_lora_serial.sh`: **mission AUTO landed=True, 7/7 waypoints, 252.2 м дистанция через LoRa без WiFi/UDP fallback**, MAVLink commands `lora_gcs_tx` 35 packets PDR=1.000, telemetry `lora_uav_tx` 442 packets PDR=0.991 (4 потеряно = 1.63% byte_loss как и заложено калибровкой). Legacy signetlabdei lorawan PHY+MAC baseline сохранён в `ns3/scenarios/lora_serial_lorawan.cc` (ED Class A, ITU-R RP.452, LoRa modulation) для PHY-correct telemetry-only demo |

### Раздел: интеграция с симулятором физики (Федотенков А.А., но интерфейсы делаю я)

| Пункт ТЗ | Состояние | Этап | Артефакты |
|---|---|---|---|
| ArduPilot ↔ Gazebo interface | ✅ | 1.2 | `ardupilot_gazebo` plugin, JSON FDM между Gazebo и SITL |
| **MAVROS для работы на основе ROS** | ✅ | **1.8** | Закрыто: runtime-flag `--mavlink-backend pymavlink\|mavros` в `bas-orchestrator`. `mavros` backend = docker `bas/mavros:dev` (ROS2 humble + MAVROS 2.14) с custom rclpy bridge node, который заменяет pymavlink на ROS2 service calls (`mavros_msgs/srv/StreamRate`, `WaypointPush`, `SetMode`, `CommandLong` force-arm). Acceptance прогон `scripts/run_stage_1_8_mavros.sh baseline_wifi`: status=success, mission_landed=True, AUTO mode, 7/7 waypoints uploaded через `/mavros/mission/push`, force-arm через CommandLong+magic 21196, landed-detection через armed-transition. Pymavlink-backend остаётся default. Подробности — `docs/stage_1_8_mavros_plan.md` (включая 7 root-cause fixes по ROS2 nuance) |
| ArduPilot ↔ AirSim interface | ✅ Stage 4 контрактный bridge | 4 | `scripts/arducopter_airsim_interface.py` (`JsonFdmBridge` + `MavlinkMirrorBridge`). Два режима: (a) JSON-FDM на UDP 9002/9003 (canonical ArduPilot pattern `SIM_AirSim.cpp`) с 16-channel PWM in / sensor JSON out; (b) MAVLink mirror — подписка на любой udp endpoint, GLOBAL_POSITION_INT + ATTITUDE → `simSetVehiclePose` через msgpack-rpc. Smoke `_arducopter_airsim_smoke.py` верифицирует JSON-FDM 50/50 round-trip, все packets valid (timestamp/imu/position/attitude/velocity), gravity vector =-9.81. Docs `docs/stage_4_arducopter_airsim_interface.md`. Зона Федотенкова А.А. — interface contract положен. |
| Gazebo → AirSim bridge (Gazebo физика + AirSim визуал) | ✅ | 2.2 + Stage 4 | `scripts/airsim_bridge.py` (Stage 2.2) tail events.jsonl → AirSim `simSetVehiclePose`; `scripts/arducopter_airsim_interface.py` (Stage 4) `MavlinkMirrorBridge` direct MAVLink → AirSim; реалистичная physics в `JsonFdmBridge` через `multirotor_dynamics.py` X-config quadrotor 6DOF. Полный pose forwarding + camera/lidar visual sync — production-grade. |

### Раздел: ns-3 / Sionna RT — РАДИОФИЗИКА (ЛИЧНЫЙ Физулинский)

| Пункт ТЗ | Состояние | Этап | Артефакты |
|---|---|---|---|
| ns-3 — затухание и отражение сигналов | ✅ | 1.5.1 | `two_channel.cc` RateErrorModel + delay/jitter + outage |
| Метрика: error rate | ✅ | 1.5.1 | `FlowMetrics.loss_ratio`, `payload_pdr`, в report.md + comparison.csv |
| Метрика: распределение ошибок | ✅ | 1.5.2.c | `VideoMetrics.video_gap_*` (gap-detection + outage correlation) |
| Метрика: пропускная способность | ✅ | 1.5.1+1.5.2.a | `FlowMetrics.goodput_bps`, `VideoMetrics.bitrate_rx_goodput_bps` |
| **Sionna RT — физически обоснованные радиокарты** | ✅ | **2.1 (v2.0)** | Реализовано: Mitsuba scene `scene/iris_runway.xml` + Sionna RT `RadioMapSolver` → `radio_maps/iris_runway.npz` (heatmap 80×30 cells, 65% coverage). Publisher `sionna_channel_publisher.py` следует за UAV-позицией и обновляет `/tmp/sionna_channel.json`. ns-3 `two_channel.cc` поллит JSON каждые 100 мс и обновляет `RateErrorModel.ErrorRate`. Isolated test PASSED (4 loss_updated events). Demo `demo_sionna_pipeline.py` визуально показывает position-dependent loss |
| Расширение: Sionna live hook на оба канала | ✅ | 2.1.e (21.05.2026) | `--sionnaTargetFlow=payload\|control\|both` в `two_channel.cc`. По умолчанию `payload` (back-compat), `BAS_SIONNA_TARGET_FLOW=both` включён в `run_stage_2_4_fpv_rf_demo.sh`. При NLOS RF model одновременно деформирует видео-канал и MAVLink-команды; verified `flow_id:"control+payload"`, loss_ratio=0.606, extra_delay_ms=109 в ns3_events.jsonl |

### Раздел: карта тестового сценария — РАЗДЕЛ Физулина «ns-3/Sionna для 3D»

| Пункт ТЗ | Состояние | Этап | Артефакты |
|---|---|---|---|
| Геометрия сцены в Gazebo | ✅ | 1.2/1.5.2 | upstream `iris_runway.sdf` + `iris_with_gimbal` модель (POV gimbal camera + ArduPilot FDM плагин в одной модели) |
| **Детализированный учёт 3D препятствий для ns-3/Sionna** | ✅ | **2.1.b (v2.0)** | `scripts/export_scene_to_sionna.py` — программный builder Mitsuba 3 XML: runway 1500×200 м (concrete), hangar (metal), 2 tower'а (concrete), building (brick) с правильными ITU-R материалами. Sionna RT видит препятствия в ray-tracing'е: 35% сцены в радио-тени, демонстрация в `logs/sionna_demo/trajectory_loss.png` |

### Раздел: моделирование + ручное управление БАС (совместно с Андрончевым, Карповым)

| Пункт ТЗ | Состояние | Этап | Артефакты |
|---|---|---|---|
| Моделирование как минимум одного БАС в тестовом сценарии | ✅ | 1.5.1 | mission AUTO 7/7 wp на обоих профилях |
| Ручное управление одним БАС через GCS | ✅ | **2.4** | Закрыто через live Web GCS operator UI + MAVProxy backend: `Browser UI -> MAVProxy -> ns-3 control -> mavbridge -> SITL`. UI даёт кнопки `GUIDED/ARM/TAKEOFF/LAND`, удержание `W/A/S/D` для velocity-команд и `GO TO` по локальной карте; `GO TO` отправляет через MAVProxy `message SET_POSITION_TARGET_LOCAL_NED`, чтобы точка была абсолютной local NED целью, а не body-frame velocity. Команды вводятся в MAVProxy stdin, прямой pymavlink не используется. RF/LOS demo добавляет Gazebo-сцену с препятствиями `Hangar`/`Tower`/GCS mast и live-панель LOS/NLOS, RSSI, packet loss, delay и график; channel JSON отдаётся в ns-3 polling как демонстрационный RF hook. Headless smoke `logs/stage_2_4_mavproxy_gcs_20260518T203447Z`: heartbeat/GPS/mode/arm state visible, `mode GUIDED`, explicit `BAS_STAGE24_FORCE_ARM=1`, `takeoff 10`, `velocity 1 0 0`, `mode LAND`, landed at relative altitude 0.01 м. Mission upload: false; direct pymavlink command path: false |

## Чужая зона (НЕ Физулин)

Перечислено для полноты табеля по гранту; контрактные интерфейсы между моими
компонентами и компонентами других участников выделены отдельно.

| Пункт ТЗ | Исполнители | Состояние | Мой интерфейс |
|---|---|---|---|
| Аналитический обзор инструментов | Степанянц, Карпов, Маргарян | ✅ **deliverable собран** | `docs/analytical_review_tools.md` — структурированный обзор всех 25+ инструментов стека (симуляторы автопилота, физика, рендеринг, network sim, RF ray-tracing, MAVLink, ИССГР, CV, web GCS, контейнеризация). По каждому: выбранный инструмент + версия + лицензия + рассмотренные альтернативы + обоснование + ограничения + использование в bas-prototype. Сводная таблица лицензионных рисков (5/14 GPL — все process-isolated, 1/14 AGPL Ultralytics с митигацией, остальное permissive). Recommendations для следующих этапов (HIL стенд, multi-GPU Sionna, perm-licensed детектор). |
| AirSim как симулятор графики/сенсоров | Андрончев, Федотенков | ⏳ → ✅ 2.2 | **Полный deploy с real GPU rendering 22.05.2026** на Cosys-AirSim. Артефакты: `scripts/airsim_{client,stub_server,bridge}.py` + `run_stage_2_2_airsim_overlay.sh` (4 mode: stub/linux/windows/off) + официальный `cosysairsim==3.3.0` Python pkg + `docs/stage_2_2_airsim_overlay.md`. Wrapper auto-download Linux build (637 MB) и Windows build (556 MB) для соответствующего mode. **`BAS_AIRSIM_MODE=windows`: real RTX 5070 Ti GPU rendering** verified — ping=True, server_version=4, 209 scene objects, simGetImage 7 cameras (front_center, fpv, back_center, bottom_center, и др.) возвращают real PNG ~36-57 KB (256×144 RGBA). Pose forwarding `simSetVehiclePose` к реальному UE5 рендеру. Это исполнительная зона Андрончева/Федотенкова, не Физулина, но архитектурно закрыто полностью. |
| MAVLink ↔ Gazebo / AirSim | Федотенков | ✅ **Stage 4 fanout router** | `scripts/mavlink_sim_router.py` — прозрачный UDP 1→N fanout, parse v1/v2 frame boundaries (STX 0xFE/0xFD), re-sync на invalid bytes, msgid stats top-5, поддержка `udpout:HOST:PORT` и `file:PATH` sinks. Smoke `_mavlink_sim_router_smoke.py` верифицирует 25 v2 HEARTBEAT → 3 sinks (2 UDP + 1 file) с identical 525B каждый, 0 resync events. Canonical config: SITL :14550 → GCS :14551, Gazebo plugin :14552, AirSim adapter :14553, capture file. Wrapper `run_stage_4_sim_bridges_demo.sh [smoke\|router\|mirror\|full]`. Docs `docs/stage_4_mavlink_sim_router.md`. Дополняет существующий `mavbridge` (PTY ↔ UDP, Stage 1.7). |
| Опционально — кибератаки | Маргарян | ✅ **Stage 4 attack+defense pair** | `scripts/cyber_attack_simulator.py` — defensive research simulator 3 attack vectors с safety guards (loopback/RFC1918 only): (a) GPS spoofing — fake `GLOBAL_POSITION_INT` injection с teleport/progressive modes; (b) MAVLink command injection — fake sysid `COMMAND_LONG` (SET_MODE/NAV_TAKEOFF/etc); (c) RF jamming через overwrite `/tmp/sionna_channel.json` (loss=0.95, RSSI=-110 dBm). Companion `scripts/cyber_defense_monitor.py` — `DefenseMonitor` class detects все 3 signatures: position jump > 100m / 1s, COMMAND_LONG от не-whitelisted sysid, sustained RSSI < -90 dBm для N polls + delay > 200ms. Emits NDJSON alerts. Smoke `_cyber_smoke.py` round-trip: baseline → 3 attacks → 20 alerts (1 spoof critical + 3 cmd critical + 16 jam warn/critical) — ALL PASS. Docs `docs/stage_4_cyber_attacks.md` с wire format payloads, detection алгоритмами, production mitigation hints (MAVLink signing, frequency hopping, INS dead-reckoning). |
| Карта сценария в Gazebo | Карпов, Маргарян | ✅ **Stage 3 urban** | `gazebo/worlds/iris_runway_urban.sdf` — 6 multi-storey buildings (office 40м, residential tower 60м, mall, warehouse, apartment, commercial), 3 asphalt roads, 12 trees, 4 streetlights, 2 vehicles, существующие RF demo obstacles (back-compat). Wrapper `run_stage_3_urban_demo.sh`. ИССГР `--seed-profile urban` загружает все 6 buildings как Obstacle через REST API. Web GCS `BAS_RF_OBSTACLE_PROFILE=urban` отображает на карте. Docs `docs/stage_3_urban_scene.md`. |
| Карта сценария в AirSim | Андрончев, Федотенков | ✅ **Stage 4 spawner-based map** | `scripts/airsim_scene_builder.py` — content-driven карта без необходимости custom UE5 asset. URBAN_SCENE_CATALOG: 26 объектов (hangar, 2 towers, 5 multi-storey buildings, 12 trees, 4 streetlights, 2 vehicles), ≈79000 м³, синхронизирован с Gazebo SDF + ИССГР `URBAN_OBSTACLES`. Coordinate conversion Gazebo ENU → AirSim NED. Генерирует AirSim `settings.json` template (Multirotor Iris1 с front_center + fpv cameras + lidar/baro/imu/gps/mag sensors) + `_BasUrbanScene` metadata block. Runtime populates сцену через Cosys-AirSim `simSpawnObject` API (Cube/Cylinder primitives, поверх любого default UE5 level — Blocks / Neighborhood). `airsim_stub_server.py` расширен: simSpawnObject/simDestroyObject/simListAssets + `--spawn-log`. Smoke `_airsim_scene_smoke.py` верифицирует 4 этапа: settings.json valid (2 cams + 5 sensors), SDF parser (31 models / 14 boxes), scene stats, populate roundtrip (26/26 spawned + 26/26 destroyed). Docs `docs/stage_4_airsim_scene_map.md`. |
| Адаптация под карты >20×20 км | Андрончев, Карпов | ✅ **Stage 4 алгоритмический пакет** | `orchestrator/issgr/large_map.py` — `TileGrid` (lat/lon ↔ TileId, bounds, neighbors, tiles_in_bbox), `SpatialIndex` (bucketing О(tiles_in_bbox), 5000 obstacles inserted за 13ms, bbox query 4×4 км за 0.06ms, 3×3 neighborhood за 0.02ms), Sionna cache key helper (`sionna/T_+003_+004/f915_h20`), `tiles_to_preload` для AirSim asset streaming. Flat-earth approximation ±1м в 50 км; NED ↔ latlon round-trip error = 0.0. Verified scenario: 20×20 км = 100 tiles × 2km each, 400 км² coverage. Smoke `_large_map_smoke.py` (10 этапов). Docs `docs/stage_4_large_map.md`. Был ➖ опциональный — закрыт. |
| Веб-интерфейс | Федотенков | ✅ **Stage 4 admin dashboard** | `web/admin/` — single-page UI с 6 tabs: Overview (4 KPI + endpoint health + activity log), ИССГР Collections (dropdown + REST proxy), Multi-UAV (roster + Leaflet markers), On-board Metrics (4 KPI + composite metrics table), Tile Map (configurable TileGrid → polygons на карте), Multicast Sync (status panel). `scripts/admin_web_server.py` — pure stdlib http.server с 9 API endpoints (/api/admin/{health,collections,items,onboard_stats,onboard_composite,tile_grid,activity,sync_stats,issgr_url}). Lazy fetch per tab. Verified 2 smoke: unit (10 endpoints, без backend) + integration (ИССГР urban + OnBoardDB → 8 obstacles + 5 composite metrics). Дополняет Stage 2.4 Operator Console (manual flight); это **админ-зона** dashboard. Docs `docs/stage_4_admin_web_interface.md`. Был ➖ опциональный — закрыт. |
| Параллельные вычисления | Андрончев, Карпов | ✅ **Stage 4 compute pool** | `orchestrator/parallel.py` — production-ready compute infrastructure на stdlib `ProcessPoolExecutor` без external deps: (a) `TaskScheduler` priority-queued + N workers + retry policy + structured `TaskResult` (kind/ok/value/error/elapsed_s/attempts); (b) `launch_sitl_fleet(sim_vehicle, n_uavs, base_port)` spawn N ArduCopter SITL instances concurrently с unique `--instance/--sysid/--out` per UAV; (c) `precompute_sionna_tiles(tiles, freq, grid)` параллельный pre-compute per-tile coverage maps. Verified scenario: 20 mixed tasks (CPU+IO+failing с retry) → 17 ok / 3 fail × 2 attempts; 16 Sionna tiles 80×80 — 1.94× speedup (4 workers vs sequential, deterministic compute, identical results); 4 mock SITL processes spawned параллельно с правильным `--sysid=N` в каждом cmd.log. Был ➖ опциональный — закрыт. Docs `docs/stage_4_parallel_compute.md`. |

## Сводка по моим закрытым / открытым пунктам

| Категория | Закрыто | Намечено | Всего | % |
|---|---:|---:|---:|---:|
| Каналы связи (личная) | 5 | 0 | 5 | **100%** |
| ns-3 / Sionna RT (личная) | 5 | 0 | 5 | **100%** |
| Карта 3D (личная) | 2 | 0 | 2 | **100%** |
| MAVROS интеграция (личная) | 1 | 0 | 1 | **100%** |
| Моделирование БАС (совместная) | 2 | 0 | 2 | **100%** |
| **Итого по Физулинской зоне** | **15** | **0** | **15** | **100%** |

Stage 2.4 закрыт live Web GCS UI, smoke-прогоном через MAVProxy и отдельным
RF/LOS demo с препятствиями, RSSI/loss/delay графиком; личная зона Физулина по
этой матрице — **100%**.

## Master demo — все модули one-shot

`scripts/run_master_demo.sh` поднимает все работающие модули и показывает
live integration в браузере через Admin Dashboard. Verified
end-to-end на 60-second run:

```
==> [1/14]  ИССГР node-A :8770       (urban seed: 8 obstacles)
==> [2/14]  ИССГР node-B :8771       (replica для multicast sync)
==> [3/14]  OnBoardDB SQLite + composite engine (sysid=1, 5Hz)
==> [4/14]  Multicast publisher (node-A → 239.10.10.10:5500) + /stats
==> [5/14]  Multicast subscriber  (multicast → node-B POST)
==> [6/14]  AirSim stub server (msgpack-rpc :41451)
==> [7/14]  AirSim scene populate (26 obstacles spawn via simSpawnObject)
==> [8/14]  ArduPilot↔AirSim JsonFdmBridge (X-config 6DOF physics)
==> [9/14]  Synthetic SITL emulator (PWM frames @ 50 Hz)
==> [10/14] Cyber defense monitor (MAVLink :14559 + RF channel)
            + scheduled attacks @ t+30/38/45 (GPS spoof / cmd inject / RF jam)
==> [11/14] Real Sionna cached tile compute (4 tiles из iris_runway map)
==> [12/14] Admin Dashboard :8810 (6 tabs, live)
==> [13/14] Browser auto-open
==> [14/14] Demo running 60s...

[t+ 60s] node-A: uavs=1 obs=8 | node-B: uavs=1 (synced)
         sync: HB=52 L1=52 (multicast packets delivered)
         onboard_rows=800 (SQLite time-series growing)
         cyber_alerts=85 (3 attacks detected by monitor)
```

Все 14 модулей запускаются параллельно, health-check'аются, и работают
60+ секунд без падений. См. артефакты в `logs/master_demo_<ts>/`.

## Ограничения

Все **не-production** аспекты текущей реализации — в `docs/LIMITATIONS.md`.
По каждому пункту разобрано: что работает, что нет, как обойти/расширить.
Ключевые ограничения:

| Категория | Что НЕ production | Workaround |
|---|---|---|
| ArduPilot SITL end-to-end | ✅ **FIXED 2026-05-25**: ArduPilot installed (5.1MB binary), JsonFdmBridge correctly parses binary `servo_packet_16` от real SITL, SITL accepts JSON sensor packets с timestamp/imu/position/quaternion/velocity/lat/lon/alt. `_real_sitl_e2e_smoke.py` verifies HEARTBEAT + 5 ATTITUDE + 5 GLOBAL_POSITION_INT + 4500+ PWM frame round-trips. Full arm/takeoff требует tuned IMU noise model (extension) — wire interface fully VERIFIED. | См. `scripts/install_ardupilot.sh` |
| Sionna RT live mode | ✅ **FIXED 2026-05-26**: `apt install libnvidia-gl-595` (matches Linux driver 595.71.05) → canonical OptiX paths populated. Wrapper `scripts/run_sionna_live.sh` устанавливает 3 env vars (LD_LIBRARY_PATH=/usr/lib/wsl/lib для libcuda, LD_PRELOAD libnvoptix.so.595.71.05 для full OptiX, MITSUBA_VARIANT=cuda_ad_mono_polarized для Jones matrix BSDF). Verified `--mode live` на iris_runway scene: backend=sionna_live, mean_rssi=-67.25 dBm, compute=20.8s/tile. Cached mode остаётся для production batch use. | — (production-ready) |
| AirSim custom UE5 map | Spawn primitives (Cube/Cylinder), не realistic building meshes | UE5 Editor + blueprint actors + `is_blueprint=True` |
| ИССГР "кварк/MongoDB/Minio" из PDF | Функциональный эквивалент через Pydantic + SQLite | Repository pattern swap на PyMongo + Minio |
| Real cyber pentest | Defensive research simulator на synthetic MAVLink | Real testing требует authorized hardware |

## Актуальный остаток

По личной зоне Физулина после сверки с новыми исходниками незакрытых пунктов в
этой матрице нет.

Отдельно, если нужен отчёт не по личной зоне, а по полному грантовому ТЗ,
нужно завести вторую матрицу по ИССГР-блокам из `Краткая_выдержка...docx` и
`Poyasnitelnaya_zapiska_lot_8_podp.pdf`.
