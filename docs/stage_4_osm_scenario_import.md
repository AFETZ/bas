# Stage 4 — OSM → ИССГР + Gazebo scenario importer

`scripts/import_osm_scenario.py` превращает **любую точку Земли** в
тестовый сценарий БАС, вытягивая building footprints из OpenStreetMap
и генерируя артефакты для трёх модулей стенда.

Закрывает ограничение «карта сценария — хардкод iris_runway»: теперь
сцена = реальный город по bbox или названию места.

## Принцип — self-contained, без GDAL

Вместо тяжёлого `osmnx`/`geopandas`/GDAL стека используется **Overpass
API** (чистый HTTP + JSON) + stdlib `urllib`/`json`. Ноль новых тяжёлых
зависимостей, работает в любом venv, WSL2-safe.

Несколько Overpass-зеркал с failover (main instance часто перегружен):
private.coffee → kumi.systems → overpass-api.de → maps.mail.ru.

## Что генерит

| Артефакт | Файл | Для модуля |
|---|---|---|
| ИССГР obstacles JSON | `<name>_issgr_obstacles.json` | наземная ИССГР (Obstacle: geometry_polygon + height_m + material) |
| Gazebo SDF world | `<name>.sdf` | физика/визуал (box-приближение каждого здания) |
| Summary | `<name>_summary.json` | bbox, N зданий, высоты, материалы |
| (опц.) POST в ИССГР | `--issgr-url` | live REST в `/collections/obstacles/items` |

Каждое здание → ИССГР `Obstacle` с реальным GeoJSON Polygon footprint,
высотой из тега `height` или `building:levels×3м`, материалом из
`building:material`.

## Использование

```bash
# По bbox (south,west,north,east):
./scripts/import_osm_scenario.py \
    --bbox 55.7585,37.6150,55.7608,37.6190 \
    --name moscow_center --out-dir generated/moscow

# По названию места (Nominatim geocode → bbox + радиус):
./scripts/import_osm_scenario.py \
    --place "Tverskaya, Moscow" --radius-m 300 \
    --name tverskaya

# С live POST в работающую ИССГР:
./scripts/import_osm_scenario.py --bbox ... \
    --issgr-url http://127.0.0.1:8770
```

Флаги: `--max-buildings N` (cap, default 200), `--origin lat,lon`
(local NED origin, default = центр bbox).

## Verified

**Live fetch** (real Moscow центр, bbox ~250м):
```
11 buildings fetched (via overpass.private.coffee)
normalized 11 obstacles
Buildings: 11  height 3.0-15.0m (mean 7.5m)
Materials: {'concrete': 11}
```
Среди зданий — реальная «Палаты Троекуровых» (историческое здание у
Госдумы), с корректным footprint и именем в SDF.

**Offline smoke** (`scripts/_osm_import_smoke.py`):
- building_height: `height` тег / `building:levels×3` / default 10м
- building_material: tag → ИССГР material map
- polygon_ring closure, centroid/bbox, latlon→local NED
- emit_issgr_json + emit_gazebo_sdf
- **ИССГР JSON валидируется через реальную Pydantic `Obstacle` модель**
- SDF: valid XML, N `<model>` blocks с `<box>`

```
ALL CHECKS PASSED
```

## Связь модулей

```
OSM (Overpass) ──┐
                 ▼
       import_osm_scenario.py
        │        │         │
        ▼        ▼         ▼
   ИССГР     Gazebo     Summary
 obstacles    SDF        stats
   │            │
   ▼            ▼
 admin       physics
 dashboard   simulation
 tile map    (drone flies
 + REST       реальный город)
 для АСУ
```

Один скрипт связывает OSM-данные с ИССГР (наземная БД для имитаторов
АСУ) и Gazebo (физика полёта в реальном городе).

## Реальный рельеф (--with-terrain, Phase B2)

`--with-terrain` добирает реальную высоту рельефа под каждым зданием
через **AWS Terrain Tiles** (SRTM-derived, keyless public S3 — без
API-токена). Модуль `scripts/terrain_elevation.py`.

```bash
./scripts/import_osm_scenario.py --bbox ... --with-terrain --terrain-zoom 12
```

Каждое здание получает `base_elevation_m` (AMSL) в properties; summary
получает `terrain` блок (min/max/mean/relief). Закрывает ограничение
large_map «flat-earth».

Terrarium decode: `elevation_m = (R*256 + G + B/256) - 32768`.

Verified: Москва центр 91–190м (origin ground 146м AMSL — реальная
высота); Кавказ 2032–5626м (max ≈ Эльбрус 5642м). Standalone:
```bash
./scripts/terrain_elevation.py --bbox 43.30,42.40,43.40,42.50 --zoom 11
# → elevation: 2032.0–5626.0m  mean 3344.5m  relief 3594.0m
```

Smoke `scripts/_terrain_smoke.py --live`: tile-coord round-trip,
elevation_at gradient, terrarium decode, live Moscow ≈146м, OSM
importer terrain wiring. ALL CHECKS PASSED.

Источник: [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/)
(SRTM/ASTER/etc., public domain / attribution per source).

## Ограничения

- **Скорость**: Overpass-зеркала бывают медленные (10-200с на bbox),
  failover между зеркалами помогает. Для production — self-hosted
  Overpass instance.
- **Box-приближение**: Gazebo SDF использует bounding box здания, а не
  точный footprint mesh. Для photorealistic — экспорт в Blender/Mitsuba
  (как делает CARLA Digital Twins).
- **Высоты зданий**: если в OSM нет `height`/`building:levels` — default
  10м. (Высота *рельефа* — отдельно через `--with-terrain`.)
- **Terrain zoom**: bbox ограничен 64 tiles на zoom; для больших
  областей понизить `--terrain-zoom` (10-11).
- **Лицензия данных**: OpenStreetMap © ODbL; terrain — AWS Terrain Tiles
  (public). Атрибуция обязательна при публикации производных карт.

## Pattern source

- [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [OSM building tags](https://wiki.openstreetmap.org/wiki/Key:building)
- [Nominatim geocoding](https://nominatim.org/release-docs/latest/api/Search/)
