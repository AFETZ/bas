# bas-prototype — ограничения и не-production aspects

Этот документ — **честный список** ограничений текущей среды
моделирования. Создан в ответ на требование "что осталось скелетом /
архитектурой без реальной реализации".

Каждый пункт: **(а) что реально работает, (б) что НЕ работает или
требует доделки, (в) каков workaround / future work**.

## 0. Общая граница: что в scope vs out of scope

| In scope (реализовано) | Out of scope (специально не делалось) |
|---|---|
| Headless simulation в WSL/Linux | Hardware-in-the-loop (HIL) с реальным Pixhawk |
| MAVLink / ns-3 / Sionna / Gazebo / AirSim integration | Полёт реального дрона / field test |
| Веб-интерфейс (operator + admin) | Production deployment (Kubernetes / monitoring stack) |
| Defensive cyber attack research | Penetration testing real targets |
| 4-канальный квадрокоптер X-config dynamics | Fixed-wing / VTOL / hexacopter / coaxial |
| Iris-class параметры (1.5 kg, 6N/motor) | Multi-rotor с пользовательскими массой/моторами в runtime |
| 20×20 км tile grid algorithms | Real OSM tile streaming / satellite imagery loading |

## 1. ArduPilot ↔ AirSim JsonFdmBridge

### Работает
- X-config quadrotor 6DOF dynamics с motor mixer, body forces/torques,
  quaternion attitude integration, IMU specific force output
- Round-trip с synthetic PWM frames (verified: ground rest, climb,
  yaw spin)
- Real-time dt clamping для numerical stability
- Optional AirSim visual sync (~20 Hz `simSetVehiclePose`)

### Не работает / не сделано
- **End-to-end test с real ArduPilot SITL `-f airsim-copter` НЕ проведён**.
  ArduPilot не установлен в текущем dev окружении (`~/ardupilot/`
  отсутствует). Smoke использует synthetic SITL emulator (PWM
  generator) вместо real `sim_vehicle.py`.
- EKF compatibility не проверена с real ArduCopter (могут потребоваться
  doctored covariance / sensor noise модели).
- Wind / turbulence model отсутствует (только linear drag).
- Ground effect / propeller wash / battery sag не моделируются.

### Workaround
```bash
# Установить ArduPilot SITL:
git clone https://github.com/ArduPilot/ardupilot.git ~/ardupilot
cd ~/ardupilot && Tools/environment_install/install-prereqs-ubuntu.sh -y
./waf configure --board sitl && ./waf copter

# Запустить с JsonFdmBridge:
./Tools/autotest/sim_vehicle.py -v ArduCopter -f airsim-copter \
    --no-mavproxy --out=udpout:127.0.0.1:14550
# В другом терминале:
./scripts/arducopter_airsim_interface.py --mode=json_fdm
```

## 2. Sionna RT — radio coverage maps

### Работает
- **Pre-computed map** `radio_maps/iris_runway.npz` (real Sionna RT
  output: 800m × 300m, 2.4 GHz, 100k samples/Tx, max_depth=3 bounces,
  все ITU materials)
- `scripts/sionna_real_tile.py --mode cached` — slice любой tile из
  pre-computed map с real RSS dBm values
- Per-tile RSS статистика (mean/min/max RSSI, coverage_fraction)
- Использование в master demo (4 tiles in real time)

### Не работает / не сделано
- **Live mode (`--mode live`) на WSL2 НЕ работает**: Mitsuba CUDA
  OptiX недоступен в WSL2 без manual setup (см. mitsuba3 docs); LLVM
  CPU backend на Sionna 1.2 имеет известный spectrum/Color3f mismatch
  при ITU materials.
- Pre-computed map покрывает только iris_runway scene (800×300м).
  Для других сцен нужно отдельный pre-compute run.
- Real-time live updates на CUDA Linux native — работает (есть Stage
  2.1 `online_sionna_publisher.py` для реального GPU), но не
  testable на этом dev host'е.

### Workaround
```bash
# На Linux native с GPU:
MITSUBA_VARIANT=cuda_ad_mono \
sionna_env/bin/python scripts/sionna_real_tile.py --mode live \
    --tile-i 0 --tile-j 0 --freq-mhz 2400

# Pre-compute для новой scene:
sionna_env/bin/python scripts/online_sionna_publisher.py \
    --scene path/to/my_scene.xml --output radio_maps/my_scene.npz
```

## 3. AirSim 3D scene

### Работает
- AirSim stub server (msgpack-rpc :41451) — pose forwarding,
  simSpawnObject/Destroy, scene listing
- 26-object urban catalog populates через `simSpawnObject` API:
  Cube/Cylinder primitives с правильными NED coords
- `settings.json` generator c полным Multirotor config (camera, lidar,
  IMU, GPS, magnetometer)
- Cosys-AirSim Windows-side real GPU rendering verified (Stage 2.2):
  RTX 5070 Ti, 209 scene objects, 7 cameras returning real PNG

### Не работает / не сделано
- **Custom UE5 .umap asset с realistic building meshes НЕ создан**.
  Текущий scene populator spawn'ит Cube/Cylinder primitives поверх
  default UE5 levels (Blocks, Neighborhood). Это functional
  obstacles но без visual realism.
- Material tags (metal/concrete/brick) в catalog metadata, но не
  применяются к UE5 actors (no segmentation ID / material override).
- Cosys-AirSim Linux build — headless rendering only (nullrhi); image
  API возвращает empty PNG. Real GPU rendering — только Windows.
- Vehicles в catalog статичные; нет moving traffic simulation.

### Workaround
- Использовать Cosys-AirSim Windows-side для photorealistic rendering
  (Stage 2.2: `BAS_AIRSIM_MODE=windows`)
- Для production custom map: UE5 Editor с blueprint actors +
  `simSpawnObject(..., is_blueprint=True)`

## 4. Large maps (>20×20 км)

### Работает
- `TileGrid` (lat/lon ↔ tile_id ↔ NED bounds)
- `SpatialIndex` (bucketing, O(tiles_in_bbox) queries)
- Sionna cache key generator (per-tile per-freq)
- `tiles_to_preload()` для AirSim asset streaming logic
- Verified: 100 tiles × 2km × 2km = 20×20 км, 5000 obstacles за 13ms

### Не работает / не сделано
- **Flat-earth approximation** — точность ±1 м только в радиусе ~50
  км от origin. Для maps >100 км нужна UTM или MGRS projection.
- **OSM tile streaming НЕ реализован**: модуль предоставляет coordinate
  algebra, но не downloads / caches tiles от OSM / Mapbox / similar.
- **GeoTIFF chunking / vector MVT** — out of scope.
- **R-tree spatial index** — текущее bucketing достаточно до ~100k
  objects, дальше нужен PyPI `rtree` package или PostGIS.

### Workaround
- Для >50 км scenarios — switch на UTM (`pyproj.Proj`); требует ~20
  LOC замены в `latlon_to_local_ned`.
- Real OSM streaming — добавить `requests`-based tile loader с disk
  cache (3-5 часов работы).

## 5. ИССГР объектная модель

### Работает
- Pydantic 2 models с JSON schema auto-generation
- FastAPI + OGC API Features 1.0 (Core + GeoJSON + OpenAPI 3.0)
- 8 collections: uavs / obstacles / gcs / missions / sensor_readings / digital_twin / waypoint_routes
- SQLite-backed onboard persistence (5 tables + composite engine)
- Custom multicast wire format (40/80B) с FNV-1a hashes + CRC-16

### Не работает / не сделано
- **"Модель данных кварк"** из `Poyasnitelnaya_zapiska_lot_8.pdf` —
  функциональный эквивалент через Pydantic + ObjectIdentifier, но не
  буквально "quark" entity.
- **Meta storage / Primary storage** разделение — текущее single-tier
  SQLite + in-memory; нет двух-уровневого storage.
- **MongoDB / Minio** из PDF — заменены SQLite (relational + JSON)
  для prototype simplicity; production deployment может swap'нуть на
  MongoDB + Minio через repository pattern.
- **numberMatched** в OGC API возвращает `len(returned features)`,
  не "total available" — это deviation от strict OGC spec (admin
  dashboard работает с правильным `limit=10000` query).

### Workaround
- Для real MongoDB swap: implement `IssgrRepository` interface поверх
  PyMongo вместо in-memory dict
- Для Minio raster storage: добавить blob field в `Obstacle` model
  + Minio upload в POST handler

## 6. Cyber attacks / defense

### Работает
- 3 attack vectors (GPS spoof / cmd injection / RF jam) с safety
  guards (loopback / RFC1918 only)
- DefenseMonitor с 3 detection algorithms (position jump / unauthorized
  sysid / sustained low RSSI)
- NDJSON structured alert log
- Round-trip smoke verified (20 alerts emitted на 3 атаки)

### Не работает / не сделано
- **Не реальный pentest**. Эти атаки — research simulator на
  synthetic MAVLink endpoint, не proven against production GCS /
  autopilot stack.
- **Mitigations (MAVLink signing, freq hopping)** — упомянуты в docs
  как production recommendations, но не реализованы.
- **Replay attacks / spoofed RTK GPS** — отдельные attack vectors не
  покрыты.

### Workaround
- Для real MAVLink message signing: ArduPilot имеет built-in support
  начиная с MAVLink v2.0; нужно set `BRD_SIG_KEY` параметр.
- Для freq hopping: SX1276 LoRa уже поддерживает hop sequences;
  нужно custom firmware на radio.

## 7. Параллельные вычисления

### Работает
- ProcessPoolExecutor-based `TaskScheduler` с priority queue + retry
- `launch_sitl_fleet` (verified 4 mock SITL processes)
- `precompute_sionna_tiles` (1.94× speedup на 4 workers vs sequential)

### Не работает / не сделано
- **`sionna_compute_tile` в `parallel.py` — это FSPL stub**, не
  real Sionna RT. Real version в отдельном `scripts/sionna_real_tile.py`
  (см. секцию 2).
- **GPU pool** не реализован — все workers CPU.
- **Cross-host distribution** (Ray / Dask Distributed) — out of scope.
- **launch_sitl_fleet возвращает только launch handles** — caller
  отвечает за lifecycle (kill PID, парс logs).

### Workaround
- Для real Sionna parallel: `precompute_sionna_tiles_real` через
  ProcessPoolExecutor wrapping `sionna_real_tile.compute_real_tile`
- Для GPU pool: switch на Ray + GPU resource scheduling

## 8. Web admin dashboard

### Работает
- 6 tabs (Overview / ИССГР Collections / Multi-UAV / OnBoard Metrics
  / Tile Map / Multicast Sync)
- Live ИССГР REST proxy + OnBoardDB direct read + sync stats fetch
- Pure stdlib HTTP server (no FastAPI dep)
- Leaflet map с auto-fit
- Auto-refresh on tab switch

### Не работает / не сделано
- **Read-only**: нет POST/PUT/DELETE endpoints (нельзя arm UAV / push
  mission через dashboard).
- **No auth**: только loopback bind по default; не подходит для public
  exposure без reverse proxy.
- **No WebSocket / SSE**: poll-based updates (~5s).
- **Tile Map limit**: рендерит до 2500 polygons (50×50); больше нужен
  clustering.

### Workaround
- Для write operations: добавить proxy к ИССГР `POST
  /collections/.../items` через admin endpoint с auth middleware
- Для realtime: SSE endpoint что publishes events.jsonl как они
  appear

## 9. Что НЕ требует hardware но всё-таки не сделано

| Пункт | Почему не сделано |
|---|---|
| Production deployment (Docker Compose stack для всего) | docker/ есть отдельные images, но нет single `docker-compose.yml` для full stack |
| Continuous integration test всего master demo | GitHub Actions есть, но не запускает full `run_master_demo.sh` |
| Full ИССГР spec coverage (растр, точечные облака, 3D thumbnails) | Pydantic models есть для Point/Polygon/LineString, не для Raster/PointCloud |
| Mission planner UI с drag/drop waypoints | Web GCS даёт click-to-go, но не editable mission |
| Multi-language UI (рус/eng toggle) | Все тексты захардкожены русские/english mix |
| Time-series visualization (Grafana-style charts) | Admin даёт table view, не graphs |

## 10. Что точно требует hardware

| Пункт | Почему |
|---|---|
| Real Pixhawk + ESC + motors | Physical actuators |
| Real LoRa radio (Semtech SX1276) | Physical RF |
| Real GPS module spoofing test | Radio attack |
| Real camera + gimbal | Sensor hardware |
| Real ground station laptop с RC controller | Operator input |

## Master demo

`scripts/run_master_demo.sh` поднимает все работающие модули и
показывает live integration. Verified end-to-end на 60-second run:

```
[t+ 60s] node-A: uavs=1 obs=8 | node-B: uavs=1
         sync: HB=52 L1=52
         onboard_rows=800
         cyber_alerts=85 (scheduled attacks at t+30/38/45s detected)
```

Это **полная operational demo** того, что работает. Каждый ➖ выше
объясняет точно, где скелет / archive / production-gap.
