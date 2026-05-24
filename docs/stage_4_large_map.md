# Stage 4 — Адаптация под карты >20×20 км

Закрывает пункт ТЗ "Адаптация под карты больше 20 на 20 км" (зона
Андрончева А.Д. / Карпова А.К.) как алгоритмический пакет: tile grid +
spatial index + интеграционные patterns для Sionna RT cache и AirSim
asset streaming.

## Проблема

Текущая урбан-сцена покрывает ~150×150 м локально. Для
operational-level missions нужна работа с tiled maps размером
20×20 км или больше — это требует:

| Компонент | Проблема при >20×20 км | Решение |
|---|---|---|
| **Sionna RT** | Не может ray-trace 400 км² за один pass (mesh size + memory) | Per-tile cached coverage maps |
| **AirSim / Gazebo** | Не могут render 20×20 км в одном level (50+ GB asset budget) | Tile streaming с UAV-position triggered hot-load |
| **ИССГР** | Linear scan O(N) для query "что в области X?" становится slow при ~100k объектов | Spatial bucketing index → O(tile_count_in_bbox) |
| **Multicast sync** | UAV в tile (5,5) не нужны packets от tile (12,8) | Per-tile filter / AOI subscription |

## Архитектура

`orchestrator.issgr.large_map` модуль:

```
TileGrid(origin_lat, origin_lon, tile_size_m, n_tiles_north, n_tiles_east)
  ├── tile_at(lat, lon)          → TileId(i, j)
  ├── bounds(tile)               → TileBounds (NED + lat/lon + GeoJSON poly)
  ├── neighbors(tile, radius)    → list[TileId] (chebyshev box)
  ├── tiles_in_bbox(...)         → list[TileId]
  ├── iter_all()                 → Iterator[TileId]
  └── total_area_km2 / tiles_*

SpatialIndex(grid)
  ├── insert(object_id, lat, lon, alt, meta) → TileId
  ├── remove(object_id)
  ├── query_tile(tile)           → list[IndexEntry]
  ├── query_bbox(...)            → list[IndexEntry]   # O(tiles_in_bbox)
  ├── query_neighborhood(lat,lon,radius) → list[IndexEntry]
  └── stats()                    → {total_objects, occupied_tiles, max/min/avg_bucket}

sionna_cache_key(tile, freq_mhz, antenna_h) → "sionna/T_+003_+004/f915_h20"
tiles_to_preload(grid, uav_lat, uav_lon, radius_tiles=2) → list[TileId]  # 5×5=25 для radius=2
```

## Coordinate convention

- **Grid origin** — lat/lon якорь сетки. Tile (0,0) — SW corner на origin.
- **Positive i** → на север (NED `n_min_m > 0`)
- **Positive j** → на восток (NED `e_min_m > 0`)
- **Negative i/j** — на юг / запад (символично; обычно grid анкерится так
  что вся область покрыта 0…N-1 tiles).
- **Flat-earth approximation** для NED ↔ latlon — точность ±1м в радиусе
  50 км от origin. Для >50 км требуется UTM или MGRS.

## Использование

### TileGrid + SpatialIndex

```python
from orchestrator.issgr import TileGrid, SpatialIndex

grid = TileGrid(
    origin_lat=-35.36, origin_lon=149.165,
    tile_size_m=2000, n_tiles_north=10, n_tiles_east=10,
)
# coverage = 20.0 × 20.0 km = 400 km², 100 tiles

idx = SpatialIndex(grid)
for n in range(100_000):
    lat, lon = generate_obstacle()
    idx.insert(f"obs-{n}", lat=lat, lon=lon, alt=15.0)
# ≈ 1000 records / tile (uniform spread), ~250ms insert total

# Query "что в радиусе 1 tile от UAV":
hits = idx.query_neighborhood(uav_lat, uav_lon, radius_tiles=1)
# 9 tiles × 1000 records = O(9000) instead of O(100_000)
```

### Sionna RT cache pre-compute (pseudo)

```python
from orchestrator.issgr import sionna_cache_key, tiles_to_preload

# Offline pre-compute per tile + frequency:
for tile in grid.iter_all():
    for freq in (433, 915, 2400, 5800):
        key = sionna_cache_key(tile, freq_mhz=freq, antenna_height_m=2.0)
        if not cache_exists(key):
            coverage = sionna_rt.compute_coverage(tile.bounds(), freq)
            cache_save(key, coverage)
# 100 tiles × 4 freqs = 400 entries, ≈ 5min на GPU farm

# Runtime — UAV запрашивает только нужные tiles:
for tile in tiles_to_preload(grid, uav_lat, uav_lon, radius_tiles=2):
    coverage = cache_load(sionna_cache_key(tile, freq, height))
    # O(1) cache hit; нет live ray-tracing на критическом пути
```

### AirSim tile streaming (pseudo)

```python
preload_tiles = tiles_to_preload(grid, uav_lat, uav_lon, radius_tiles=1)
# 9 tiles вокруг UAV
for tile in preload_tiles:
    if tile not in airsim_loaded:
        spawn_assets_for_tile(client, tile, grid.bounds(tile))
for tile in (airsim_loaded - set(preload_tiles)):
    destroy_assets_for_tile(client, tile)
# UAV видит 6×6 км окружения; остальное unloaded → fit memory budget
```

## Файлы

| Файл | Что |
|---|---|
| `orchestrator/src/orchestrator/issgr/large_map.py` | `TileGrid`, `TileId`, `TileBounds`, `SpatialIndex`, `IndexEntry`, helpers |
| `orchestrator/src/orchestrator/issgr/__init__.py` | Export `TileGrid`, `SpatialIndex`, etc. |
| `scripts/_large_map_smoke.py` | 10-этапный CI smoke |
| `docs/stage_4_large_map.md` | Этот файл |

## Verified

CI smoke pass:

```
[1] TileGrid: 20.0 × 20.0 km = 400 km², 100 tiles
[2] tile_at / bounds round-trip: origin → T_+000_+000, bounds 0-2000m
[3] neighbors(radius=1)=9, neighbors(radius=2)=25
[4] SpatialIndex insert 5000 in 13.6ms; avg 47.6 records/tile, 105 occupied tiles
[5] bbox query 4×4 km: 216 hits in 0.06ms
[6] neighborhood query rad=1 (3x3 tiles): 452 hits in 0.02ms
[7] sionna_cache_key deterministic (same input → same key); diff freq → diff key
[8] tiles_to_preload(radius=2) = 25 tiles (5×5)
[9] tiles_in_bbox 6×6 km = 12 tiles (3×3 + edge overlap)
[10] NED ↔ latlon round-trip error = 0.0 (flat-earth precision)
```

## Ограничения

1. **Flat-earth** approximation — точность ±1м в радиусе 50 км от origin.
   Для maps 100×100 км+ нужно UTM / MGRS / WGS84 ellipsoid.
2. **Bucketing index** — простая reference imp; для >1M objects нужен
   R-tree (например `rtree` PyPI package) или PostGIS.
3. **No quad-tree LOD** — все tiles одного размера. Hierarchical
   (Google Maps style) tiling — extension.
4. **No tile data format** — модуль работает только с пространственной
   математикой; **что** в tile (mesh, raster, vector layers) — отдельная
   задача (GeoTIFF chunking, OSM PBF, MVT vector tiles).

## Pattern source

- [TMS tile coordinate](https://wiki.osgeo.org/wiki/Tile_Map_Service_Specification) — Google/OSM tile grids
- [Equirectangular projection](https://en.wikipedia.org/wiki/Equirectangular_projection) — flat-earth approximation
- [R-tree spatial index](https://en.wikipedia.org/wiki/R-tree) — production replacement для bucketing
- [Sionna RT coverage maps](https://nvlabs.github.io/sionna/api/rt.html) — pre-compute API
