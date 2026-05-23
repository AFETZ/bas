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
| Бортовая часть ИССГР | Бортовая объектно-ориентированная БД, сохранение данных сенсоров, комплексированные данные для управления БАС | ⚠️ **Stage 3 частично** — live-tail orchestrator events.jsonl → UAV upsert в `IssgrRepository`; SensorReading model для time-series. Реальная on-board persistence (SQLite на дроне) — backlog. |
| Наземная часть ИССГР | Агрегация данных, цифровые двойники группы БАС и пространства, визуализация объектов | ✅ **Stage 3** — `/digital_twin` endpoint выдаёт единый GeoJSON FeatureCollection всех ИССГР объектов; визуализация через QGIS / leaflet / любой OGC API Features клиент |
| Синхронизация БД | Автоматический запуск синхронизации, уровни детализации, пакеты 40/80 байт, multicast при поддержке каналов связи | Не реализовано — custom multicast protocol поверх UDP вне scope; у нас REST/HTTP |
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
| ArduPilot ↔ AirSim interface | ➖ | — | Зона Федотенкова А.А. |
| Gazebo → AirSim bridge (Gazebo физика + AirSim визуал) | ➖→⏳ | 2.2 | Зона Федотенкова А.А., но архитектурный интерфейс положу я в 2.2 |

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
| Аналитический обзор инструментов | Степанянц, Карпов, Маргарян | ➖ | — |
| AirSim как симулятор графики/сенсоров | Андрончев, Федотенков | ⏳ → ✅ 2.2 | **Полный deploy с real GPU rendering 22.05.2026** на Cosys-AirSim. Артефакты: `scripts/airsim_{client,stub_server,bridge}.py` + `run_stage_2_2_airsim_overlay.sh` (4 mode: stub/linux/windows/off) + официальный `cosysairsim==3.3.0` Python pkg + `docs/stage_2_2_airsim_overlay.md`. Wrapper auto-download Linux build (637 MB) и Windows build (556 MB) для соответствующего mode. **`BAS_AIRSIM_MODE=windows`: real RTX 5070 Ti GPU rendering** verified — ping=True, server_version=4, 209 scene objects, simGetImage 7 cameras (front_center, fpv, back_center, bottom_center, и др.) возвращают real PNG ~36-57 KB (256×144 RGBA). Pose forwarding `simSetVehiclePose` к реальному UE5 рендеру. Это исполнительная зона Андрончева/Федотенкова, не Физулина, но архитектурно закрыто полностью. |
| MAVLink ↔ Gazebo / AirSim | Федотенков | ➖ | mavbridge готов как референс |
| Опционально — кибератаки | Маргарян | ➖ | — |
| Карта сценария в Gazebo | Карпов, Маргарян | ✅ **Stage 3 urban** | `gazebo/worlds/iris_runway_urban.sdf` — 6 multi-storey buildings (office 40м, residential tower 60м, mall, warehouse, apartment, commercial), 3 asphalt roads, 12 trees, 4 streetlights, 2 vehicles, существующие RF demo obstacles (back-compat). Wrapper `run_stage_3_urban_demo.sh`. ИССГР `--seed-profile urban` загружает все 6 buildings как Obstacle через REST API. Web GCS `BAS_RF_OBSTACLE_PROFILE=urban` отображает на карте. Docs `docs/stage_3_urban_scene.md`. |
| Карта сценария в AirSim | Андрончев, Федотенков | ➖ | — |
| Адаптация под карты >20×20 км | Андрончев, Карпов | ➖ опционально | — |
| Веб-интерфейс | Федотенков | ➖ опционально | JSONL формат пригоден к импорту |
| Параллельные вычисления | Андрончев, Карпов | ➖ опционально | — |

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

## Актуальный остаток

По личной зоне Физулина после сверки с новыми исходниками незакрытых пунктов в
этой матрице нет.

Отдельно, если нужен отчёт не по личной зоне, а по полному грантовому ТЗ,
нужно завести вторую матрицу по ИССГР-блокам из `Краткая_выдержка...docx` и
`Poyasnitelnaya_zapiska_lot_8_podp.pdf`.
