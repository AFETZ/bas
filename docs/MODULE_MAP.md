# Карта модулей BAS-стенда

Birds-eye обзор: **что за модуль, что он показывает, чем питается, что
отдаёт, с чем работает в связке и в каком сценарии полезен.** Если читаете
проект впервые — начните отсюда, потом ныряйте в `docs/stage_*.md` за
деталями конкретного модуля.

## Легенда статуса

| Бейдж | Значение |
|---|---|
| 🟢 | production-grade — реальная реализация, verified end-to-end |
| 🟡 | работает с оговоркой — рабочее ядро, граница прописана в `LIMITATIONS.md` |
| 🔵 | reference / research — корректная модель для демонстрации, не боевое применение |

## Поток данных одной картинкой

```
  OSM/terrain ─► import_osm_scenario ─► ИССГР obstacles ─┬─────────────┐
                                         + Gazebo SDF     │             │
                                                          ▼             │ --from-osm
  ArduPilot SITL ─► MAVLink ─► ns-3 (control+payload) ─► Web GCS / Admin │ (geom+seg)
       ▲                          ▲                          ▲          ▼
       │ PWM/sensors              │ loss/delay               │ REST  airsim_scene_builder
  JsonFdmBridge              Sionna RT (RF heatmap)      ИССГР API :8770 │
       │                          │                       on-board SQLite│
  multirotor 6DOF            scene (Munich/город)         multicast sync │
       ▼                                                  large_map tiles▼
  Cosys-AirSim (UE5 визуал) ─► camera ─► CV detector ─► ИССГР sensor_readings
```

---

## 1. ИССГР — цифровой двойник (наземная + бортовая часть)

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **ИССГР REST/OGC API** | `scripts/issgr_api_server.py`, `orchestrator/issgr/api.py` | Цифровой двойник обстановки как OGC API Features 1.0 | БАС, препятствия, НПУ, миссии, sensor readings в GeoJSON | events.jsonl / POST → `/collections/*`, `/digital_twin` | admin, sync, АСУ-клиент, CV | «ведомственная АСУ видит обстановку через стандартный REST» | 🟢 |
| **Бортовая БД** | `orchestrator/issgr/onboard.py` | On-board SQLite time-series + комплексированные метрики | траектория, sensor readings, mission log, avg RSSI/NLOS/battery | UAV state → SQLite + composite engine | admin (вкладка Бортовая БД) | «борт хранит данные и считает производные для автопилота» | 🟢 |
| **Multicast sync** | `orchestrator/issgr/sync.py`, `scripts/issgr_sync_{publisher,subscriber}.py` | Синхронизация БД между узлами компактными 40/80B пакетами | счётчики L1/L2/HEARTBEAT, репликация узел→узел | ИССГР node-A → multicast 239.10.10.10 → node-B | ИССГР API, admin (Синхронизация) | «два АСУ-узла синхронизируют двойник по UDP» | 🟢 |
| **Large map** | `orchestrator/issgr/large_map.py` | TileGrid + SpatialIndex + WGS84/UTM геодезия (pyproj) для карт >20×20 км | tile-сетка, bbox-запросы, neighborhood, точные NED↔lat/lon | lat/lon → tile id / bbox query / UTM | admin (вкладка 20×20 км), Sionna cache | «оперативная карта 400 км², быстрый поиск объектов в области» | 🟢 (WGS84/UTM via pyproj, см-точность на любой дистанции) |

## 2. Радиофизика — каналы связи

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **ns-3 два канала** | `ns3/scenarios/two_channel.cc` | Эмуляция control + payload каналов (WiFi) | loss ratio, goodput, delay, jitter, outage | TAP-bridge ↔ loss/delay JSON | Web GCS RF-панель, Sionna hook | «связь деградирует при заходе БАС за здание» | 🟢 |
| **ns-3 LoRa serial** | `ns3/scenarios/lora_serial*.cc` | LoRa через Serial PTY, калибровано под SX1276 | PDR, airtime, byte loss без IP в петле | PTY байты ↔ NetDevice | SITL primary serial | «телеметрия по LoRa без WiFi fallback» | 🟢 |
| **Sionna RT** | `scripts/sionna_real_tile.py`, `run_sionna_live.sh`, `scene/` | Физический ray-trace радиополя по реальной геометрии | RSSI heatmap, LoS/NLoS, multipath, coverage | scene + tx → radio map / channel.json | ns-3 (online hook), large_map, Munich/город сцены | «карта покрытия в реальном городе, тень от зданий» | 🟢 (live GPU); 🟢 (cached) |

## 3. Автопилот, физика полёта, маршрутизация

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **ArduPilot SITL** | `docker/ardupilot-sitl/`, `~/ardupilot` | Реальный автопилот ArduCopter в SITL | heartbeat, режимы, arm, mission, телеметрия | MAVLink TCP/UDP | Web GCS, MAVROS, JsonFdmBridge, ns-3 | «настоящий автопилот, тот же код что на Pixhawk» | 🟢 |
| **JsonFdmBridge + 6DOF** | `scripts/arducopter_airsim_interface.py`, `scripts/multirotor_dynamics.py` | ArduPilot ↔ AirSim, реальная X-config квадро-физика + IMU noise | PWM→полёт, IMU/GPS обратно в EKF | servo PWM → sensor JSON / pose | SITL (JSON frame), AirSim | «автопилот летает по нашей физике, ARM+takeoff verified» | 🟢 |
| **MAVLink sim router** | `scripts/mavlink_sim_router.py` | Прозрачный 1→N fanout MAVLink (v1/v2) | msgid-статистика, раздача на N приёмников | udpin → udpout×N + file | SITL → GCS/Gazebo/AirSim/лог | «один поток автопилота на несколько потребителей» | 🟢 |
| **Gazebo** | `gazebo/worlds/iris_runway_urban.sdf` | Физика полёта + 3D городская сцена | дрон в мире, препятствия, FPV-камера | SDF + ArduPilot plugin → physics | SITL, FPV, ИССГР urban seed | «полёт в городе с препятствиями» | 🟢 |
| **MAVROS** | `docker/mavros/` | ROS2-бэкенд управления как альтернатива pymavlink | mission push, set mode, force arm через ROS2 | MAVLink ↔ ROS2 service calls | SITL | «управление на основе ROS2 (для ROS-экосистемы)» | 🟢 |

## 4. Визуал и сенсоры (фотореализм)

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **Cosys-AirSim overlay** | `scripts/airsim_{client,bridge,stub_server}.py`, `run_stage_2_2_airsim_overlay.sh` | UE5 фотореалистичный визуал + сенсоры поверх Gazebo физики | RGB/depth/LiDAR камеры, 209 scene objects | pose → simSetVehiclePose / simGetImages | Gazebo (физика), JsonFdmBridge | «фотореалистичная картинка борта на RTX GPU» | 🟢 (real GPU via Windows-host, verified PNG 256×144 RTX 5070 Ti; Linux=nullrhi CI) |
| **AirSim scene builder** | `scripts/airsim_scene_builder.py` | Спавн сцены в AirSim из встроенного каталога **или реальной OSM-геометрии** + semantic segmentation | здания с реальным footprint/высотой/рельефом, segmentation ID по категории, settings.json | catalog / `--from-osm` ИССГР obstacles → simSpawnObject + simSetSegmentationObjectID | AirSim, ИССГР URBAN_OBSTACLES, OSM importer | «реальный город из OSM в AirSim с семантическими масками для CV» | 🟢 (реальная геометрия + segmentation; primitives, не realistic mesh) |
| **CV детектор** | `scripts/cv_detector.py` | YOLOv8 детекция + geo-tagging → ИССГР | bbox объектов, ENU-координаты целей | FPV `/camera.mjpg` → ИССГР sensor_readings | FPV, ИССГР API | «борт распознаёт объекты и кладёт в двойник» | 🟢 (COCO weights; AGPL) |

## 5. Карта и данные (реальный мир)

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **OSM importer** | `scripts/import_osm_scenario.py` | Любая точка Земли → сценарий БАС | реальные здания: footprint, высота, материал | `--place`/bbox → ИССГР obstacles + Gazebo SDF | ИССГР, Gazebo, terrain | «карта сценария = реальный город (Москва, Тверская)» | 🟢 (Overpass; медленно) |
| **Terrain elevation** | `scripts/terrain_elevation.py` | Реальная высота рельефа из AWS Terrain Tiles | elevation grid, AMSL высоты, рельеф | bbox → elevation grid / base_elevation_m | OSM importer (`--with-terrain`), large_map | «здания на реальном рельефе (Москва 146м, Эльбрус 5626м)» | 🟢 (keyless SRTM) |
| **Urban Gazebo сцена** | `gazebo/worlds/iris_runway_urban.sdf` | Хардкод-сцена: 6 зданий, дороги, деревья, машины | городская застройка для полёта/RF | — | ИССГР urban seed, Web GCS RF | «типовой городской тестовый сценарий» | 🟢 |

## 6. Интерфейсы оператора и админа

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **Web GCS** (`:8765`) | `web/gcs/`, `scripts/gcs_web_ui_server.py`, `scripts/mavproxy_stage_2_4_driver.py` | Пульт ручного управления одним БАС | карта, WASD/Space/Ctrl, FPV-окно, RF-панель | браузер → MAVProxy → ns-3 → SITL | SITL, ns-3, FPV, Sionna | «оператор вручную управляет БАС, видит RF за зданием» | 🟢 |
| **Admin dashboard** (`:8810`) | `web/admin/`, `scripts/admin_web_server.py` | Витрина состояния стенда (не пульт) | ИССГР, бортовая БД, sync, tile-карта, multi-UAV | REST proxy + SQLite read | ИССГР API, on-board DB, sync, large_map | «доказать что все модули живы и связаны» | 🟢 (read-only) |

## 7. Специальные модули

| Модуль | Файл | Назначение | Что показывает | Вход → Выход | В связке с | Сценарий | Статус |
|---|---|---|---|---|---|---|---|
| **Кибер атака/защита** | `scripts/cyber_attack_simulator.py`, `scripts/cyber_defense_monitor.py` | Defensive research: 3 атаки + детектор | GPS spoof / cmd injection / RF jam + алерты | inject MAVLink/RF → detection alerts | MAVLink endpoint, sionna channel | «детектор распознаёт спуфинг GPS / инъекцию команд / глушение» | 🔵 research |
| **Параллельные вычисления** | `orchestrator/parallel.py` | ProcessPool: задачи + флот SITL + Sionna tiles | speedup, N SITL спавн, tile pre-compute | задачи → результаты с retry | SITL fleet, Sionna tiles | «пакетный pre-compute карт покрытия на всех ядрах» | 🟢 |
| **Оркестратор** | `orchestrator/` | Сценарный движок: состояние сцены, events.jsonl | поток событий полёта, фазы сценария | YAML конфиг → events.jsonl | всё (events.jsonl — общая шина) | «единый прогон сценария с записью событий» | 🟢 |

---

## Как этим пользоваться

- **Хочу увидеть end-to-end связки модулей** → `docs/SCENARIOS.md` (рецепты цепочек).
- **Хочу запустить демо, но не знаю какое** → `scripts/demo.sh` (интерактивное меню) или `docs/DEMOS.md`.
- **Что значит ИССГР / LoS / FDM / SITL** → `docs/GLOSSARY.md`.
- **Что реально, а что заглушка** → колонка «Статус» выше + `docs/LIMITATIONS.md`.
- **Какие сайты открываются в браузере** → `docs/ui_guide.md`.
