# Stage 4 — Веб-интерфейс расширенный (Admin Dashboard)

Закрывает пункт ТЗ "Веб-интерфейс" (зона Федотенкова А.А.) как
single-page admin dashboard поверх существующих API. Дополняет
Stage 2.4 Operator Console (manual flight control) administrative
surface для multi-UAV monitoring и ИССГР browse.

## Архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│  web/admin/  (Leaflet + vanilla JS, 6 tabs)                      │
│  ─────────────────────────────────────────────────────────────  │
│   Overview / ИССГР Collections / Multi-UAV / On-board Metrics    │
│   Tile Map / Multicast Sync                                       │
└──────────────────────────────────────────────────────────────────┘
                       │ fetch (lazy per-tab)
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  scripts/admin_web_server.py  (stdlib http.server)               │
│  ─────────────────────────────────────────────────────────────  │
│   /api/admin/health                                              │
│   /api/admin/issgr_url                                           │
│   /api/admin/collections          ← proxy ИССГР                  │
│   /api/admin/items?c=…            ← proxy ИССГР                  │
│   /api/admin/onboard_stats        ← OnBoardDB direct read        │
│   /api/admin/onboard_composite                                   │
│   /api/admin/tile_grid?n=&e=&size=  ← TileGrid generator          │
│   /api/admin/activity                                            │
│   /api/admin/sync_stats           ← placeholder                  │
└──────────────────────────────────────────────────────────────────┘
            │ HTTP                       │ SQLite
            ▼                            ▼
   ИССГР REST :8770              OnBoardDB SQLite
   (optional)                    (optional)
```

Pure stdlib HTTP — нет FastAPI dep, easy embed в любой dev env.

## Tabs

### 1. Overview
- 4 KPI cards: collections / UAVs / obstacles / on-board rows
- Endpoint health table (ИССГР, OnBoard DB, multicast)
- Activity log (recent /api/admin/activity events)

### 2. ИССГР Collections
- Collection dropdown (uavs / obstacles / gcs / missions / sensor_readings)
- Refresh button + count display
- Table: id / name / geometry type / coords / properties (truncated)

### 3. Multi-UAV
- Roster table: sysid / name / mode / armed / alt / battery
- Live Leaflet map с circle markers (green=armed, gray=disarmed)
- Auto-fit bounds

### 4. On-board Metrics
- 4 KPI cards: rows per table (uav_state / sensor_readings / mission / composite)
- Latest composite metrics table: sysid / metric / value / extra / age

### 5. Tile Map (>20×20 km)
- Configurable inputs: tile size (500..10000m), n_north / n_east
- Render button → polygons via TileGrid через `/api/admin/tile_grid`
- Stats: total tiles, coverage NxE km, total km²

### 6. Multicast Sync
- Static info: endpoint, TTL, packet sizes, wire format
- Placeholder для live publisher stats (extension)

## Запуск

### Standalone (degraded mode — без backend)

```bash
./scripts/admin_web_server.py --port 8810
# Открыть http://127.0.0.1:8810/
# Все API endpoints возвращают 200 с empty / placeholder data;
# Tile Map работает (no backend dep).
```

### Полный режим — ИССГР + OnBoard DB

```bash
# Terminal 1 — ИССГР API server:
./scripts/issgr_api_server.py --port 8770 --seed-profile urban

# Terminal 2 — admin dashboard:
./scripts/admin_web_server.py \
    --port 8810 \
    --issgr-url http://127.0.0.1:8770 \
    --onboard-db /var/lib/onboard/bas.db

# Открыть http://127.0.0.1:8810/
```

### Совместно со Stage 3 demo (events.jsonl tail)

Admin dashboard работает поверх любого ИССГР instance. Запустите
`run_stage_3_issgr_demo.sh` или ваш custom orchestrator с ИССГР, и
admin будет показывать live состояние через REST proxy.

## API endpoints

| Path | Method | Returns |
|---|---|---|
| `/api/admin/health` | GET | `{ok: true, ts: epoch}` |
| `/api/admin/issgr_url` | GET | `{url: "http://..."}` |
| `/api/admin/collections` | GET | `{collection_name: count, ...}` |
| `/api/admin/items?c=NAME&limit=N` | GET | ИССГР FeatureCollection (passthrough) |
| `/api/admin/onboard_stats` | GET | OnBoardDB `stats()` или 404 если не configured |
| `/api/admin/onboard_composite` | GET | `{metrics: [{sysid, metric_name, metric_value, extra_json, ts_ms}]}` (latest per metric) |
| `/api/admin/tile_grid?n=N&e=E&size=M` | GET | `{total_tiles, total_area_km2, geojson: FeatureCollection}` |
| `/api/admin/activity` | GET | `{log: [{ts, event, detail}]}` |
| `/api/admin/sync_stats` | GET | Placeholder (extension) |

## Файлы

| Файл | Что |
|---|---|
| `web/admin/index.html` | Single-page UI с 6 tabs |
| `web/admin/app.js` | Vanilla JS, fetch + Leaflet (lazy per-tab) |
| `web/admin/styles.css` | Dark theme, 3500B |
| `scripts/admin_web_server.py` | Stdlib HTTP server + 9 API endpoints |
| `scripts/_admin_web_smoke.py` | Unit smoke — 10 endpoints без backend deps |
| `scripts/_admin_web_integration_smoke.py` | Integration smoke — ИССГР + OnBoardDB + admin end-to-end |
| `docs/stage_4_admin_web_interface.md` | Этот файл |

## Verified

### Unit smoke (без backend)

```
[1] /api/admin/health          → {ok: true}
[2] / (index.html)             → 6875B, content-type ok
[3] /app.js + /styles.css      → 9928B + 3957B
[4] /api/admin/tile_grid 10×10 → 100 tiles, 400 km²
[5] tile_grid bounds clamp     → n=999 → max 50
[6] /api/admin/activity        → ≥1 event (startup)
[7] /api/admin/onboard_stats   → 404 (no DB)
[8] /api/admin/collections     → {} (no ISSGR)
[9] /api/admin/items?c=uavs    → {} (no ISSGR)
[10] /api/admin/sync_stats     → placeholder

ALL CHECKS PASSED
```

### Integration smoke (ИССГР + OnBoardDB)

```
[5] /api/admin/collections     → uavs:0, obstacles:8, gcs:1, missions:0, sensor_readings:0
[6] /api/admin/items?c=obstacles → 8 features (Hangar, ControlTower, ...)
[7] /api/admin/onboard_stats   → uav_state=5, sensor_readings=4, composite=5
[8] /api/admin/onboard_composite → 5 metrics: avg_rssi_5s=-71.25, battery_pct=89.2, ...
[9] /api/admin/tile_grid 20×20 → 400 tiles

ALL CHECKS PASSED
```

## Ограничения

1. **No live websocket** — UI делает poll по таймеру / при tab switch.
   Для real-time (>1Hz updates) нужно SSE или WebSocket — extension.
2. **No auth** — admin endpoint должен быть за reverse proxy с auth
   или firewalled. Текущий standalone server bind'ит на 127.0.0.1
   по default.
3. **No write operations** — все endpoints read-only. POST/PUT/DELETE
   ИССГР объектов идёт напрямую в `/collections/.../items` через
   существующий ИССГР API (или через Stage 2.4 operator console).
4. **Tile Map** — рендерит до 2500 polygons (50×50). Больше нужен
   server-side simplification / clustering.

## Pattern source

- [Leaflet docs](https://leafletjs.com/reference.html) — tile map library
- [http.server Python](https://docs.python.org/3/library/http.server.html) — stdlib HTTP
- [OGC API Features 1.0](https://docs.ogc.org/is/17-069r4/17-069r4.html) — collections REST contract
