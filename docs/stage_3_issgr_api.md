# Stage 3 — ИССГР REST/OGC API

Объектно-ориентированная геопространственная база данных + стандарт-compliant
REST/OGC API для имитаторов ведомственных АСУ. Закрывает значительную
часть **внешней рамки гранта** из `docs/Краткая_выдержка_актуального_из_гранта_БАС.docx`:
объектная модель, унифицированный классификатор, бортовая/наземная части,
цифровой двойник группы БАС, API для внешних имитаторов АСУ.

> Это **не личная зона Физулина А.В.** (по ТЗ распределения задач — зона
> Андрончева / Карпова / Маргарян / Федотенкова), но архитектурный
> интерфейс положен здесь как контрактный артефакт для последующей передачи —
> аналогично AirSim bridge в Stage 2.2.

## Что закрывает из ТЗ

| Пункт ТЗ верхнего уровня | Реализация |
|---|---|
| Объектная модель: идентификаторы, классификации, пространственная/временная модели | `orchestrator/issgr/models.py` (Pydantic) + `ObjectIdentifier` (domain:system:uuid) + Pose + timestamp |
| Унифицированный классификатор | `orchestrator/issgr/classifier.py` `IssgrClass` enum, top-level группы: `geospatial_objects`, `operational_situation`, `functional_objects`, `raster_3d_displays` |
| Геопространственные типы (точка, линия, полигон) | `PointGeometry`, `LineStringGeometry`, `PolygonGeometry` GeoJSON-compatible |
| Бортовая часть (UAV + sensor readings) | `UAV`, `SensorReading` models, live-tail orchestrator events.jsonl |
| Наземная часть (GCS, obstacles, digital_twin) | `GCS`, `Obstacle` models, unified `/digital_twin` endpoint |
| Имитаторы ведомственных АСУ (REST API) | FastAPI app `orchestrator/issgr/api.py` |
| OGC API Features compliance | `/collections`, `/collections/{c}`, `/collections/{c}/items`, `/items/{id}`, `/conformance`, OpenAPI 3.0 |

## Архитектура

```
┌──────────────────────────────────────────────────────────────────────┐
│ orchestrator events.jsonl ──── tail thread ──→ IssgrRepository      │
│   (flight events → UAV upsert)                       (in-memory +   │
│                                                       optional      │
│                                                       JSONL snapshot)│
│                                                                ↑     │
│  Static seed (RF demo obstacles + GCS) ────────────────────────┤     │
│                                                                ↑     │
│  АСУ-клиенты POST новых объектов через REST ──────────────────┘     │
│                                                                      │
│                                ↓                                     │
│                        FastAPI/uvicorn app                           │
│                                ↓                                     │
│           OGC API Features endpoints (GeoJSON over HTTP)             │
│                                ↓                                     │
│  Внешние клиенты: QGIS, leaflet.js, openlayers, имитаторы АСУ,      │
│  имитаторы наземных систем ИССГР                                     │
└──────────────────────────────────────────────────────────────────────┘
```

## Файлы

| Файл | Что |
|---|---|
| `orchestrator/src/orchestrator/issgr/__init__.py` | Package exports |
| `orchestrator/src/orchestrator/issgr/classifier.py` | `IssgrClass` enum (17 значений в 4 top-level) |
| `orchestrator/src/orchestrator/issgr/models.py` | Pydantic data classes: ObjectIdentifier, Pose, PointGeometry, PolygonGeometry, LineStringGeometry, BBox, UAV, Obstacle, GCS, SensorReading, Mission, Waypoint |
| `orchestrator/src/orchestrator/issgr/serializers.py` | `to_geojson_feature()` + `to_geojson_feature_collection()` |
| `orchestrator/src/orchestrator/issgr/repository.py` | `IssgrRepository` thread-safe in-memory + optional JSONL persistence |
| `orchestrator/src/orchestrator/issgr/api.py` | FastAPI `create_app()` с OGC API Features endpoints |
| `scripts/issgr_api_server.py` | Standalone entrypoint: uvicorn + tail orchestrator events |
| `scripts/issgr_asu_client_demo.py` | Demo АСУ-имитатор (stdlib only, no deps) |
| `scripts/run_stage_3_issgr_demo.sh` | Wrapper: base stack + ИССГР API в background |

## OGC API Features endpoints

| Endpoint | Что |
|---|---|
| `GET /` | Landing page с links на conformance, collections, openapi |
| `GET /conformance` | OGC API Features 1.0 conformance classes |
| `GET /api` | Redirect на `/openapi.json` |
| `GET /openapi.json` | OpenAPI 3.0 spec (auto-generated FastAPI) |
| `GET /docs` | Swagger UI (interactive API explorer) |
| `GET /collections` | Список ИССГР collections |
| `GET /collections/{c}` | Описание одной collection + JSON Schema |
| `GET /collections/{c}/items` | GeoJSON FeatureCollection (filters: `bbox`, `limit`, `offset`, `issgr_class`) |
| `GET /collections/{c}/items/{id}` | Single GeoJSON Feature |
| `POST /collections/{c}/items` | Создать объект (для имитаторов АСУ) |
| `DELETE /collections/{c}/items/{id}` | Удалить объект |

### BAS-specific endpoints

| Endpoint | Что |
|---|---|
| `GET /digital_twin` | Unified GeoJSON FeatureCollection всех ИССГР объектов |
| `GET /stats` | Счётчики по collections |
| `GET /schema` | JSON Schema всех моделей |
| `GET /classifier` | `IssgrClass` values сгруппированные по top-level |

## ИССГР Collections

| Collection ID | Object type | Назначение |
|---|---|---|
| `uavs` | `UAV` | Бортовая часть: живой state дронов (sysid, armed, mode, battery, pose) |
| `obstacles` | `Obstacle` | Геопространственные препятствия: hangar, tower, mast, trees |
| `gcs` | `GCS` | Functional objects: НПУ, retranslators, sensor stations |
| `missions` | `Mission` | Операционная situation: waypoint routes (POST-able из АСУ) |
| `sensor_readings` | `SensorReading` | Time-series: RSSI, LiDAR distance, CV detections |

## Унифицированный классификатор

```
geospatial_objects
  ├── building.generic
  ├── building.hangar
  ├── building.tower
  ├── infrastructure.mast
  ├── infrastructure.runway
  ├── terrain.vegetation
  └── terrain.dem

operational_situation
  ├── uav.rotary_wing
  ├── uav.fixed_wing
  ├── uav.vtol
  ├── mission.waypoint_route
  └── target.point

functional_objects
  ├── gcs.command_post
  ├── retranslator.lora
  └── sensor.ground_station

raster_3d_displays
  ├── radio_map.coverage
  └── occupancy.grid
```

Class кодируется как dot-separated path для расширяемости без breaking
existing clients. `IssgrClass.from_string()` имеет fallback на ближайший
top-level если leaf неизвестен.

## Запуск

### Полный demo с base stack

```bash
sudo bash scripts/run_stage_3_issgr_demo.sh
```

Поднимает:
1. Base FPV+RF stack (Gazebo + SITL + ns-3 + Web GCS на :8765)
2. ИССГР API server (:8770) с live-tailer'ом events.jsonl
3. Default seed: GCS (Operator-1) + 2 obstacles (Hangar + Tower) из RF demo сцены

Открыть в браузере:
- http://127.0.0.1:8770/docs — Swagger UI
- http://127.0.0.1:8770/digital_twin — GeoJSON всех объектов

### Standalone API server (без base stack)

```bash
.venv/bin/python scripts/issgr_api_server.py
.venv/bin/python scripts/issgr_api_server.py --port 8770 --no-seed
.venv/bin/python scripts/issgr_api_server.py --events logs/<run>/events.jsonl
```

### Demo АСУ-клиент

```bash
python3 scripts/issgr_asu_client_demo.py --port 8770
```

Выполняет 6-step workflow: discover → list obstacles → poll UAV → POST mission → POST CV-detected obstacle → GET digital_twin.

## Примеры HTTP вызовов

### Discover collections

```bash
curl -s http://127.0.0.1:8770/collections | jq '.collections[].id'
# ["uavs", "obstacles", "gcs", "missions", "sensor_readings"]
```

### Получить все UAV (GeoJSON FeatureCollection)

```bash
curl -s http://127.0.0.1:8770/collections/uavs/items | jq .
```

```json
{
  "type": "FeatureCollection",
  "numberMatched": 1, "numberReturned": 1,
  "features": [{
    "type": "Feature",
    "id": "bas:fizulin-rig:00000000-0000-0000-0000-000000000100",
    "geometry": {"type": "Point", "coordinates": [149.165, -35.363, 10.0]},
    "properties": {
      "issgr_class": "operational_situation.uav.rotary_wing",
      "issgr_class_top": "operational_situation",
      "object_type": "UAV", "name": "UAV-1", "sysid": 1,
      "armed": true, "flight_mode": "AUTO",
      "altitude_m": 10.0, "heading_deg": 90.0
    }
  }]
}
```

### Фильтр по bbox

```bash
curl -s 'http://127.0.0.1:8770/collections/obstacles/items?bbox=149.16,-35.37,149.17,-35.36' | jq .
```

### Фильтр по issgr_class

```bash
curl -s 'http://127.0.0.1:8770/collections/uavs/items?issgr_class=operational_situation.uav.rotary_wing' | jq .
```

### POST новой mission из АСУ

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{
    "id": {"domain": "asw", "system": "imitator-1", "object_uuid": "00000000-0000-0000-0000-000000000999"},
    "name": "АСУ-задача-1",
    "target_uav_id": {"domain": "bas", "system": "fizulin-rig", "object_uuid": "00000000-0000-0000-0000-000000000100"},
    "state": "pending",
    "waypoints": [
      {"seq": 0, "action": "takeoff", "altitude_m": 10.0},
      {"seq": 1, "action": "waypoint", "latitude_deg": -35.36285, "longitude_deg": 149.16550, "altitude_m": 10.0},
      {"seq": 2, "action": "land"}
    ]
  }' \
  http://127.0.0.1:8770/collections/missions/items
```

### Получить цифровой двойник (наземная часть + бортовая в одном FeatureCollection)

```bash
curl -s http://127.0.0.1:8770/digital_twin | jq '.features | length'
# 5 (1 GCS + 2 obstacles + 1 UAV + 1 mission)
```

### Получить OpenAPI 3.0 spec

```bash
curl -s http://127.0.0.1:8770/openapi.json | jq '.paths | keys'
```

## Интеграция с QGIS

В QGIS: **Layer → Add Layer → Add WFS / OGC API Features Layer → New**:

- Name: `BAS ISSGR`
- URL: `http://127.0.0.1:8770`

После Connect — QGIS покажет 5 collections как layers, и можно перетащить любой
на карту для overlay над OSM/Google Tiles/etc.

## Verified end-to-end

```
=== Step 1: GET /collections ===
  uavs            n=0
  obstacles       n=2  (seed: Hangar, Control Tower)
  gcs             n=1  (seed: Operator-1)
  missions        n=0
  sensor_readings n=0

=== Step 2: GET /collections/obstacles/items ===
  - Control Tower    class=geospatial_objects.building.tower    height=24.0м material=concrete  centroid=(149.16558, -35.36253)
  - Hangar           class=geospatial_objects.building.hangar   height=18.0м material=metal     centroid=(149.16520, -35.36288)

=== Step 4: POST /collections/missions/items ===
  Created mission id=asw-imitator:asu-client-demo:14670464-36ae-4f29-8363-e6ee253e9b8e

=== Step 5: POST /collections/obstacles/items (CV-detected) ===
  Created obstacle id=cv-detector:asu-client-demo:3aafc3bb-bd71-4ac5-b8c5-bc2662de3826

=== Step 6: GET /digital_twin ===
  numberMatched=5, numberReturned=5
    functional_objects     count=1
    geospatial_objects     count=3  (2 seed + 1 CV-detected)
    operational_situation  count=1  (1 mission)
```

## Границы API модуля и соседние Stage 3 реализации

- **Синхронизация БД с multicast** реализована отдельно в
  [stage_3_issgr_sync.md](stage_3_issgr_sync.md): compact 40/80-byte UDP
  packets, CRC, gap detection и REST resync.
- **On-board persistent DB** реализована отдельно в
  [stage_3_issgr_onboard.md](stage_3_issgr_onboard.md): SQLite WAL,
  time-series, sensor readings и composite metrics. Сам REST API остаётся
  in-memory + JSONL snapshot; для production backend нужен PostGIS / MongoDB.
- **Authentication / authorization** — endpoints open. Для production нужен
  OpenID Connect + RBAC (можно через fastgeoapi).
- **CV-обработка видовых данных** реализована отдельно в
  [stage_3_cv_detector.md](stage_3_cv_detector.md): YOLOv8n, FPV frames,
  geo-tagging и POST detections в ИССГР API.
- **WMS/WCS/WMTS** — у нас только OGC API Features. WMS/WMTS для raster
  слоёв (radio maps) — отдельный модуль.

Эти вопросы — для последующего расширения через `pygeoapi` либо custom
extensions.

## Pattern source

- [OGC API Features 1.0 Core](https://docs.ogc.org/is/17-069r3/17-069r3.html)
- [pygeoapi](https://pygeoapi.io/) — full OGC API server (мы использовали FastAPI напрямую для минимальной зависимости)
- [GeoJSON RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946)
- [Cosys-AirSim bridge](stage_2_2_airsim_overlay.md) — аналогичный pattern для чужой зоны
