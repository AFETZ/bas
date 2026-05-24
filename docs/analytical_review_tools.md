# Аналитический обзор инструментов среды моделирования БАС

> Исполнитель grant-deliverable: коллектив ПВАТС УЛ САПР
> (Степанянц В.Г., Карпов А.К., Маргарян А.Г., Андрончев А.Д.,
> Федотенков А.А., Физулин А.В.).
> Сборка обзора выполнена по факту реализации в `bas-prototype` на дату
> **2026-05-24**.

## 1. Контекст и цель обзора

Среда моделирования БАС (Беспилотных Авиационных Систем) должна
объединять физическое моделирование полёта, радиоканалов, сенсоров,
группового поведения и информационно-справочной системы (ИССГР) в
единый закрытый контур.

Цель данного обзора — зафиксировать **выбранный технологический стек**
для прототипа, **рассмотренные альтернативы**, обоснование выбора,
лицензионные риски и направления развития. Документ служит:

  * входным артефактом для отчёта по гранту (пункт ТЗ "Аналитический
    обзор инструментов");
  * базой для onboarding новых участников коллектива;
  * входной точкой для discussion при расширении среды (например, на
    карты >20×20 км или на гетерогенные платформы Fixed-Wing/VTOL).

## 2. Сводная таблица выбранного стека

| Категория | Инструмент | Версия | Лицензия | Зрелость | Роль в bas-prototype |
|---|---|---|---|---|---|
| Симулятор автопилота (SITL) | **ArduPilot ArduCopter** | 4.5+ | GPL-3.0 | Production (10+ лет) | Базовый SITL multicopter, MAVLink ground truth |
| Физика + 3D мир | **Gazebo Garden / Harmonic** | g11+ | Apache-2.0 | Production (Open Robotics) | Multicopter dynamics, ландшафт, препятствия |
| 3D рендеринг (опц.) | **Cosys-AirSim 3.3.0** + UE5 | 3.3.0 / 5.4 | MIT (AirSim) + UE EULA | Active fork | High-fidelity камера/сенсоры, RTX GPU rendering |
| Network simulator | **ns-3** | 3.43+ | GPL-2.0 | Reference de-facto | Wi-Fi/LoRa link emulation, propagation loss |
| RF ray-tracing | **NVIDIA Sionna RT** | 0.18+ | Apache-2.0 | Active research (NVIDIA) | LoS/NLoS, multipath, RSSI estimate из Mitsuba meshes |
| MAVLink библиотека | **pymavlink / MAVProxy** | 2.4 / 1.8 | LGPL-3.0 / GPL-3.0 | Reference | Управление SITL, mission upload, RC override |
| MAVLink ↔ ROS bridge | **MAVROS** (ROS 2 Humble) | 2.6+ | BSD-3 + GPL components | Production | mavlink_router, MAVROS-style topic bridge |
| Сценарный orchestrator | **bas-orchestrator** (own) | 0.1.0 | proprietary | Stage 1-3 closed | Состояние сцены, events.jsonl, ИССГР bridge |
| ИССГР объектная модель | **Pydantic 2** | 2.13 | MIT | Production | JSON schema, валидация, ObjectIdentifier |
| ИССГР REST API | **FastAPI + uvicorn** | 0.136 | MIT | Production | OGC API Features 1.0, /digital_twin |
| On-board persistence | **SQLite (stdlib)** | 3.40+ | Public domain | Reference de-facto | WAL journal, time-series, retention |
| Multicast sync wire format | own (40/80B UDP packets) | — | proprietary | Stage 3 closed | RFC 2365 admin-local, FNV-1a + CRC16/CCITT-FALSE |
| Компьютерное зрение | **Ultralytics YOLOv8n** | 8.4 | AGPL-3.0 | Production | Real-time COCO detection, geo-tagging |
| CV pipeline | **OpenCV** | 4.13 | Apache-2.0 | Reference de-facto | I/O, image preprocessing |
| Web GCS UI | vanilla JS + Leaflet | latest | BSD-2 (Leaflet) | Production | Live карта, WASD управление, RF панель |
| Real-time bus | **WebSocket** (websockets 16.0) | 16.0 | BSD-3 | Reference | live telemetry → UI |
| Контейнеризация | **Docker / docker-compose** | 24+ | Apache-2.0 | Reference de-facto | ArduPilot, Gazebo, MAVROS, ns-3 контейнеры |
| CI/CD | **GitHub Actions** | hosted | proprietary (free tier) | Reference | smoke tests, lint, packaging |
| Хост | **WSL2 / Ubuntu 22.04** | — | MIT (WSL) / разное | Production | Dev + simulation окружение |

## 3. Разбор по категориям

### 3.1. Симулятор автопилота — ArduPilot SITL

**Что выбрано:** ArduPilot ArduCopter в SITL режиме (`sim_vehicle.py`).

**Почему:** ArduPilot — единственный open-source автопилот с
**production-grade SITL и одинаковой кодовой базой** между симулятором
и реальным железом (Pixhawk/Cube). Это даёт уверенность, что
поведение в симе совпадёт с полётом на хардваре. MAVLink де-факто
стандарт связи с GCS, поддержан всеми ground-station ПО (QGC,
MissionPlanner, MAVProxy).

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **PX4** | Также production-grade и open-source, конкурирует с ArduPilot. Отказались т.к. (1) ArduCopter имеет более зрелый Copter mode и AUTO mission stack; (2) российские проекты БАС чаще ориентируются на ArduPilot из-за более простого hardware bring-up; (3) PX4 имеет более жёсткую привязку к ROS 2 которая нам пока не нужна. |
| **PilotsLab/Closed-source SITL** | Лицензионные и export-control риски, отсутствие исходного кода. |
| **Custom 6DOF dynamics** | Изобретение велосипеда; нет community support, нет ground-truth для validation. |

**Зрелость:** релизы каждые 6 месяцев, обширное community (CubePilot,
ArduPilot.org Discord, тысячи commit-ов). GPL-3.0 — не блокирует
коммерческое использование при правильной декомпозиции (orchestrator
держим как отдельный proprietary).

**Использование в bas-prototype:**
`docker/ardupilot-sitl/Dockerfile` (Ubuntu 22.04 base) собирает
ArduCopter из исходников; `scripts/run_stage_*_demo.sh` запускают
`sim_vehicle.py -v ArduCopter --no-mavproxy` и подключают MAVProxy
отдельным процессом для cleaner separation.

**Ограничения:** SITL — только software-in-the-loop, не учитывает
hardware-specific квирки IMU/EKF; для production verification нужен
HIL/HITL стенд.

### 3.2. Физика и 3D мир — Gazebo

**Что выбрано:** Gazebo (Garden / Harmonic, не Classic) — современная
ветка с gz-sim engine.

**Почему:** Gazebo — единственный широко-поддерживаемый open-source
симулятор робототехники с **physics + sensors + rendering в одном
бинаре**. ArduPilot имеет официальный SITL-Gazebo plugin
(`ardupilot_gazebo`), который читает servo PWM и публикует sensor
данные обратно. Для multi-UAV сценариев Gazebo проще масштабируется
чем альтернативы (`<plugin name="ArduPilotPlugin">` × N).

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **Gazebo Classic 11** | Deprecated с 2025 (EOL), миграция всё равно нужна. Garden — current LTS. |
| **AirSim только** | Unreal Engine = closed-source + Windows-first; для headless CI неподходит. Используем как **сверху** Gazebo для high-fidelity камеры (см. 3.3). |
| **Webots** | Cyberbotics open-sourced в 2018, активно развивается. Отказались из-за слабой ArduPilot интеграции (нет officially supported plugin). |
| **CoppeliaSim (V-REP)** | Free для образования, но коммерческая лицензия для production; экосистема ROS значительно слабее. |
| **Pavlov VR / Custom UE worlds** | Завязка на UE EULA + не headless. |

**Использование:** `gazebo/worlds/iris_runway_urban.sdf` —
6 multi-storey зданий, 3 дороги, 12 деревьев, 4 уличных фонаря,
2 автомобиля, RF obstacles. SDF format декларативный, легко
редактируется и diff-ится в git. Контейнер `docker/gazebo/Dockerfile`
запускает headless gzserver.

**Лицензия:** Apache-2.0 — permissive, бизнес-friendly.

### 3.3. 3D рендеринг и высокоточные камеры — Cosys-AirSim

**Что выбрано:** Cosys-AirSim 3.3.0 (форк AirSim коллективом
COSYS-LAB) поверх Unreal Engine 5.4.

**Почему:** Оригинальный Microsoft AirSim архивирован в 2022.
Cosys-AirSim — единственный активный fork с регулярными релизами,
sustaining поддержку UE5 (current), новые сенсоры (Lidar, GPU echo
sounder, semantic segmentation). Используем как **rendering overlay**
поверх Gazebo physics, когда нужны photorealistic камера/Lidar.

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **Microsoft AirSim 1.8.1** | Архивирован, нет UE5 поддержки, broken на современных GPU. |
| **Isaac Sim (NVIDIA)** | Production-grade, но Omniverse — proprietary + 50+ GB установка + USD scene format не overlaps с SDF/MAVLink workflow. |
| **CARLA** | Car-focused, для UAV неподходит. |
| **Native UE5 + custom plugin** | 6+ месяцев разработки, не оправдано. Cosys-AirSim уже реализует то что нужно. |
| **Только Gazebo cameras** | OK для basic CV, но качество текстур и освещения существенно ниже UE5; для real-mission training датасетов недостаточно. |

**Использование в bas-prototype:** `scripts/airsim_{client,
stub_server, bridge}.py` + `run_stage_2_2_airsim_overlay.sh` с 4 mode
(stub/linux/windows/off). На Windows-mode rendering идёт через DZN/D3D12
на RTX 5070 Ti с 209 scene objects, simGetImage даёт real PNG 256×144
с 7 camera angles. Bridge переводит pose из ArduPilot SITL в
`simSetVehiclePose` AirSim.

**Лицензия:** AirSim — MIT (permissive), UE5 — EULA (royalty 5% при
revenue >$1M; для R&D и grant — free). Для production-deployment
требуется UE-лицензия / переход на permissive renderer.

### 3.4. Network simulator — ns-3

**Что выбрано:** ns-3 (network simulator 3) v3.43+.

**Почему:** ns-3 — академически признанный де-факто стандарт для
network research. Имеет реализации Wi-Fi (802.11 a/b/g/n/ac/ax),
LoRa (через external `lorawan` module), LTE, 5G NR. Embedding в
orchestrator через TAP-bridge даёт **shared physical layer** между
SITL и Gazebo: control plane (MAVLink) и payload (камера, ИССГР sync)
идут через emulated radio.

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **OMNeT++ + INET** | Сравнимая зрелость, но GPL+commercial dual-license; LoRa support слабее. |
| **Mininet / Mininet-WiFi** | Container-based, легче но нет realistic PHY/MAC; не годится для propagation modeling. |
| **GNURadio + USRP** | Real SDR, дорого ($1000+/node), не симуляция. |
| **MATLAB Communications Toolbox** | Proprietary, плохо embedding в headless CI. |
| **Sionna sys (без RT)** | Sionna покрывает PHY, но MAC/network stack нужен отдельно — ns-3 дополняет. |

**Использование:** `docker/ns3/Dockerfile` собирает ns-3, embedded
скрипты симулируют LoS/NLoS, packet loss, delay; результаты эмиттятся
через JSON polling для Web GCS RF панели.

**Лицензия:** GPL-2.0 — для linking с proprietary orchestrator
используем process-boundary (не shared library).

### 3.5. RF ray-tracing — NVIDIA Sionna RT

**Что выбрано:** Sionna 0.18+ (Sionna Ray Tracing extension) поверх
Mitsuba 3 + Dr.Jit.

**Почему:** Sionna RT — единственный open-source ray-tracer для
**мобильной связи** с GPU-accelerated coverage maps, multipath,
material-aware reflection coefficients (concrete, brick, metal,
glass). NVIDIA backing → быстрое развитие, GPU support через CUDA
13.x. Apache-2.0 лицензия снимает GPL risks.

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **Wireless InSite (Remcom)** | Industry-standard, но closed-source, лицензия ~$50k+/seat. |
| **Altair Feko / WinProp** | Аналогично commercial, не вариант для open R&D. |
| **CloudRF (online API)** | Free tier ограничен, paywall для production. |
| **Custom raytracer** | Изобретение велосипеда — Sionna уже даёт production quality. |
| **ns-3 propagation models (LogDistance, Cost231)** | Empirical, не учитывают geometry; используем как fallback в ns-3 modules. |

**Использование:** `requirements_sionna.txt` в изолированном venv
(TensorFlow 2.18 имеет жёсткие numpy<2 ограничения). Online Sionna RT
publisher эмиттит coverage maps по mesh-моделям препятствий из
Gazebo. Для real-time режима results кешируются и интерполируются.

**Ограничения:** GPU-only (CUDA), CPU fallback существенно медленнее.
На WSL2 — CUDA через `nvidia-smi` passthrough с RTX 5070 Ti.

### 3.6. MAVLink и наземные станции

**Что выбрано:** pymavlink 2.4 (parsing + dialect generation),
MAVProxy 1.8 (interactive CLI GCS), MAVROS 2.6 (ROS 2 bridge).

**Почему:**
  * **pymavlink** — официальная reference implementation от ArduPilot/PX4 для Python; авто-генерация message classes из XML dialect.
  * **MAVProxy** — single-process headless GCS, удобен для headless CI smokes (`scripts/_smoke_*.sh`).
  * **MAVROS** — bridge MAVLink ↔ ROS 2 topics, для будущих ROS-based modules (например MoveIt планирование).

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **QGroundControl (Qt GUI)** | Тяжёлый, для CI headless неподходит. Используем как **бортовой GUI** через QGC SITL bridge для manual debug. |
| **MissionPlanner** | Windows-only (.NET WPF), не годится для Linux CI. |
| **Custom MAVLink stack** | Pymavlink покрывает 100% наших нужд. |

**Использование:** `scripts/mavproxy_stage_2_4_driver.py` запускает
MAVProxy с custom command pipeline — Web GCS пишет в stdin строки
типа `mode GUIDED`, `arm throttle`, `takeoff 10`, `velocity 1 0 0`.
Это даёт **single source of truth** через MAVProxy и убирает
race conditions от прямого pymavlink.

### 3.7. ИССГР объектная модель и API

**Что выбрано:** Pydantic 2.13 + FastAPI 0.136 + uvicorn.

**Почему:**
  * **Pydantic 2** — rust-backed validation, в 5-50× быстрее v1; auto JSON schema generation покрывает требование ОГРСИ "JSON schema объектной модели".
  * **FastAPI** — modern async REST framework с автоматической OpenAPI 3.0 генерацией; точно матчит OGC API Features 1.0 (Core + GeoJSON + JSON conformance class).
  * **OGC API Features 1.0** — современная замена WFS/WMS, единственный международный стандарт для geographic features через REST/GeoJSON.

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **Django + DRF + django-rest-framework-gis** | Тяжёлый, sync-only, OGC поддержка через сторонние пакеты с заброшенными релизами. |
| **GeoServer + Java** | Production-grade, но Java-stack не вписывается в Python orchestrator; OGC API Features в GeoServer всё ещё experimental. |
| **PostgreSQL+PostGIS + REST gateway** | Overkill для prototype; на этом этапе in-memory + SQLite достаточно. PostGIS — следующий шаг для production. |
| **GraphQL** | Не покрывает OGC, не индустриальный стандарт для GIS. |

**Использование:** `orchestrator/issgr/api.py` поднимает 6 collections
(uavs, gcs, obstacles, missions, sensor_readings, digital_twin),
автоматически генерит OpenAPI на `/openapi.json`, conformance на
`/conformance`. Pydantic схемы (`models.py`) — single source of truth
для всех endpoint'ов и встроенной валидации.

### 3.8. On-board persistence — SQLite

**Что выбрано:** SQLite через python stdlib `sqlite3`, WAL journal
mode, single-file DB.

**Почему:**
  * **Embedded** — zero-config, переживает power-cut UAV.
  * **WAL mode** — concurrent reader + writer без блокировок;
    composite engine читает в фоне пока append'ит main thread.
  * **stdlib** — нет внешних dependencies, работает на любом
    Linux companion-computer (Raspberry Pi / Jetson Nano).

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **PostgreSQL** | Standalone server, overkill для on-board (требует ≥200 MB RAM). |
| **DuckDB** | Analytical, не optimized для high-frequency single-row insert. |
| **RocksDB / LevelDB** | KV store, теряет SQL queryability для composite metrics. |
| **Custom flat file (parquet, msgpack)** | Нет ACID, нет concurrent read. |
| **Time-series DB (InfluxDB, TimescaleDB)** | Standalone server requirement, не fit embedded. |

**Использование:** `orchestrator/issgr/onboard.OnBoardDB` — 5 таблиц,
retention rolling 1ч, composite engine в daemon-thread считает
`avg_rssi_5s`, `nlos_detected`, `target_count_5s`,
`battery_pct_smoothed` и persistит для time-series аналитики.
Verified roundtrip через `sqlite3` CLI.

### 3.9. Multicast sync — custom wire format

**Что выбрано:** UDP multicast (RFC 2365 admin-local
239.10.10.10:5500) + custom 40/80-byte binary wire format с CRC-16/CCITT-FALSE
и FNV-1a hashes.

**Почему:** ТЗ explicitly требует "пакеты 40/80 байт" — типичный
constraint для tactical data links (NATO STANAG 4677 family). Custom
format даёт полный контроль над bandwidth и предсказуемый
deterministic encoding.

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **MAVLink v2** | Подходящий header (8B), но payload max 255 байт, нет multicast профиля для multi-node sync. |
| **Protobuf / FlatBuffers** | Schema flexibility — overkill, packet size непредсказуем (varint), пересекает 40/80B. |
| **MQTT-SN over multicast** | Pub/sub overhead — топики + QoS handshake, не fit 40B budget. |
| **DDS (Data Distribution Service)** | OMG-standard, но Fast-DDS / Cyclone DDS требуют ≥30B header alone; ROS 2 backend, слишком тяжёлый для UAV companion. |
| **CoAP** | RFC 7252, headers занимают 4-12B + options, 40B realistic только для tiniest payloads. |

**Использование:** `orchestrator/issgr/sync.py` —
`encode_position_l1` / `encode_sensor_l2` собирают packets через
`struct.pack` с big-endian; `MulticastPublisher` / `MulticastSubscriber`
работают через `socket` stdlib. Verified: 6/6 packets round-trip с
CRC OK на loopback.

### 3.10. Компьютерное зрение — YOLOv8

**Что выбрано:** Ultralytics YOLOv8n (nano variant) + OpenCV 4.13.

**Почему:**
  * **YOLOv8** — current SOTA real-time object detector от Ultralytics; pre-trained на COCO (80 classes — включает person, car, truck, bicycle что подходит для surveillance scenarios).
  * **n (nano) variant** — 3.2M parameters, работает 30+ FPS на CPU; suitable для companion-computer без GPU.
  * **Easy ONNX export** — для deployment на edge (Jetson, Rockchip NPU).

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **YOLOv5** | Тот же maintainer, но v8 быстрее и accurate. |
| **MMDetection / Detectron2** | Research frameworks, не optimized для real-time inference. |
| **NVIDIA TAO toolkit + custom training** | Требует labeled dataset; для prototype используем COCO pre-trained. |
| **OpenCV DNN с MobileNet-SSD** | Меньше точность, deprecated detector zoo. |

**Использование:** `scripts/cv_detector.py` подписывается на FPV
`/camera.mjpg`, прогоняет каждый frame через YOLOv8n, делает
geo-tagging через pinhole camera model + UAV pose + camera FOV
ray-cast, POSTит detections в ИССГР `/collections/sensor_readings/items`
как `SensorReading(sensor_type='camera_object_detection')`.

**Лицензия:** Ultralytics YOLOv8 — **AGPL-3.0**, что требует disclosure
исходного кода при network-deployed inference. Для production
deployment нужно либо (a) приобрести Ultralytics Enterprise license,
либо (b) переключиться на permissive детектор (RT-DETR, YOLO-NAS
Apache-2.0). В prototype работаем under AGPL.

### 3.11. Web GCS — vanilla JS + Leaflet

**Что выбрано:** Plain JavaScript ES2020 + Leaflet 1.9 + WebSocket
для live telemetry, без фреймворков (React/Vue/Svelte).

**Почему:** Web GCS — 1500 строк UI с понятной single-purpose
функциональностью; React/Vue добавляют 100+ KB bundle и build step
без сопоставимого ROI. Leaflet — индустриальный стандарт OSS для tile
maps, BSD-2 лицензия, нет dependencies.

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **React + Mapbox GL** | Mapbox GL — proprietary tokens, vendor lock-in. |
| **OpenLayers** | Сравним с Leaflet, но API сложнее, для нашего use case не оправдано. |
| **Cesium (3D)** | Heavy 3D rendering, для tactical map поверх Yandex/OSM tiles избыточно. |
| **QGC web edition** | Не существует — QGC только desktop Qt. |
| **Server-rendered HTML + HTMX** | WebSocket real-time hard через server-rendered. |

**Использование:** `web/gcs/` — single-page app, `app.js` держит state,
WebSocket subscription к orchestrator events, отрисовка UAV positions
на Leaflet + RF панель (LOS/NLoS, RSSI, packet loss, delay), кнопки
GUIDED/ARM/TAKEOFF/LAND.

### 3.12. Контейнеризация и CI/CD

**Что выбрано:** Docker + docker-compose, GitHub Actions hosted
runners.

**Почему:** Docker — industry standard, изолирует сложные ROS/Gazebo
dependencies от хост-системы. docker-compose даёт декларативную
многоконтейнерную orchestration (orchestrator + SITL + Gazebo + ns-3 +
MAVROS).

**Альтернативы рассмотрены:**

| Кандидат | Почему отказались |
|---|---|
| **Podman** | Совместим с Docker, но Windows/WSL2 support через `podman-machine` нестабилен; для воспроизводимости отбросили. |
| **Kubernetes / k3s** | Overkill для single-host dev окружения. |
| **Native install + apt** | Не воспроизводимо, конфликты между ROS 2 Humble (Ubuntu 22.04) и newer Sionna requirements. |
| **GitLab CI** | Свой runner pool требует maintenance; для open prototype GitHub Actions free tier достаточен. |

**Использование:** 5 Dockerfile (`docker/{ardupilot-sitl,gazebo,
mavros,ns3,video}/`), все на `ubuntu:22.04` baseline кроме MAVROS
(`ros:humble-ros-base-jammy`) и video (`debian:bookworm-slim`).
GitHub Actions workflow запускает linting + smoke import-test + docs
generation.

## 4. Сводка лицензионных рисков

| Компонент | Лицензия | Риск | Митигация |
|---|---|---|---|
| ArduPilot | GPL-3.0 | Только если **линкуем** SITL код в proprietary бинарь | Process boundary: orchestrator общается через MAVLink, не shared lib |
| Gazebo | Apache-2.0 | Нет | — |
| Cosys-AirSim | MIT | Нет | — |
| Unreal Engine 5 | EULA royalty 5% при revenue >$1M | Бизнес-риск для commercial deployment | Для R&D free; альтернатива — Godot/Ogre3D backend |
| ns-3 | GPL-2.0 | Только при linking в proprietary | Process boundary, не shared lib |
| Sionna RT | Apache-2.0 | Нет | — |
| pymavlink | LGPL-3.0 | Dynamic linking OK | Используем как pip package, OK |
| MAVProxy | GPL-3.0 | Только linking | Используем как subprocess, OK |
| MAVROS | BSD-3 + GPL parts | Mixed | Process boundary |
| Pydantic / FastAPI / uvicorn | MIT | Нет | — |
| SQLite | Public domain | Нет | — |
| Leaflet | BSD-2 | Нет | — |
| **Ultralytics YOLOv8** | **AGPL-3.0** | ⚠️ Network deployment требует disclosure | (a) buy Enterprise license, (b) переключиться на YOLO-NAS / RT-DETR (Apache-2.0) |
| OpenCV | Apache-2.0 | Нет | — |
| Docker | Apache-2.0 | Нет | — |

**Общий вывод:** при правильной декомпозиции (process boundaries для
всех GPL компонентов) и замене Ultralytics на permissive детектор
весь стек становится distributable under Apache-2.0 / MIT / BSD —
suitable для commercial spin-off из grant.

## 5. Зрелость и community signals

| Компонент | GitHub stars | Last release | Commits/мес | Зрелость |
|---|---|---|---|---|
| ArduPilot | 10k+ | 4.5.6 (2025) | 100+ | Production |
| Gazebo Garden | 1.5k+ | 9.1 (2025) | 50+ | Production |
| Cosys-AirSim | 200+ | 3.3.0 (2025) | 10-20 | Active fork |
| ns-3 | — (gitlab) | 3.43 (2025) | 40+ | Production |
| Sionna | 800+ | 0.19 (2025) | 30+ | Active research |
| pymavlink | 400+ | 2.4.49 (2025) | 5-10 | Stable |
| MAVProxy | 300+ | 1.8.74 (2025) | 10+ | Stable |
| FastAPI | 80k+ | 0.136 (2025) | 200+ | Production |
| Pydantic | 23k+ | 2.13 (2025) | 100+ | Production |
| Ultralytics | 36k+ | 8.4 (2025) | 200+ | Production |

## 6. Рекомендации для следующих этапов

1. **Replace YOLOv8 на permissive detector** (RT-DETR / YOLO-NAS) если
   планируется commercial deployment или integration в продукт с
   ограничениями на AGPL.
2. **Перенести on-board SQLite на embedded PostgreSQL / SQLCipher**
   для encryption at rest, если данные UAV содержат classified
   information.
3. **Добавить Fast-DDS / Cyclone DDS** как opt-in transport — нужен
   для ROS 2 integration future tasks и не закрывает naше custom 40/80B
   sync.
4. **Sionna RT GPU farm** — для maps >20×20 км (опц. зона) нужно
   distributed ray-tracing на multi-GPU; рассмотреть Ray + multi-host
   Sionna deployment.
5. **AirSim → Open3D / Godot 4 backend** для long-term избегания UE
   royalty risk; Open3D имеет ray-tracing + headless rendering на CPU/GPU.
6. **HIL стенд** — переход SITL → HIL с реальным Pixhawk 6X / CubePilot
   для финальной верификации перед field-test.
7. **MISP / STIX export** для ИССГР — если sensor readings нужно
   sharing с внешними системами OSINT/cyber.

## 7. Заключение

Текущий выбор инструментов закрывает 100% обязательных пунктов ТЗ
(`docs/tz_compliance.md`: 8/8 ИССГР, 5/5 каналов связи, 5/5 ns-3 /
Sionna, 2/2 карт 3D, 1/1 MAVROS, 2/2 моделирования). Стек —
индустриальный де-факто стандарт в области open-source UAV
simulation, что снижает onboarding-cost новых участников коллектива
и гарантирует переносимость артефактов между академическими и
коммерческими проектами.

**Critical-path риски:** UE5 royalty (только при scaling commercial),
YOLOv8 AGPL (только при network deployment), GPU dependency для
Sionna RT (mitigated через WSL2 nvidia-smi passthrough).

Все остальные компоненты — permissive licensed, process-isolated,
suitable для long-term sustaining и расширения на гетерогенные
платформы (Fixed-Wing, VTOL, ground unmanned).

## Приложение А: точечные референсы

| Ссылка | Назначение |
|---|---|
| [ArduPilot SITL docs](https://ardupilot.org/dev/docs/sitl-simulator-software-in-the-loop.html) | Setup SITL |
| [Gazebo Garden tutorials](https://gazebosim.org/docs/garden/tutorials) | SDF format, plugins |
| [Cosys-AirSim repo](https://github.com/Cosys-Lab/Cosys-AirSim) | AirSim fork |
| [ns-3 manual](https://www.nsnam.org/docs/release/3.43/manual/html/) | Network simulation |
| [Sionna RT paper](https://arxiv.org/abs/2303.11103) | Differentiable ray tracing |
| [MAVLink common dialect](https://mavlink.io/en/messages/common.html) | Message reference |
| [OGC API Features 1.0](https://docs.ogc.org/is/17-069r4/17-069r4.html) | REST geospatial standard |
| [RFC 2365](https://datatracker.ietf.org/doc/html/rfc2365) | Admin-local multicast |
| [Pydantic 2 docs](https://docs.pydantic.dev/) | Validation framework |
| [FastAPI docs](https://fastapi.tiangolo.com/) | Async REST framework |
| [Ultralytics YOLO](https://docs.ultralytics.com/) | YOLOv8 reference |
| [Leaflet JS](https://leafletjs.com/) | Tile map library |
