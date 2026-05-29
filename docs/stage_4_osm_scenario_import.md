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

## Ограничения

- **Скорость**: Overpass-зеркала бывают медленные (10-200с на bbox),
  failover между зеркалами помогает. Для production — self-hosted
  Overpass instance.
- **Box-приближение**: Gazebo SDF использует bounding box здания, а не
  точный footprint mesh. Для photorealistic — экспорт в Blender/Mitsuba
  (как делает CARLA Digital Twins).
- **Высоты**: если в OSM нет `height`/`building:levels` — default 10м.
  Для точных высот можно добрать Microsoft Global Building Footprints
  (CC0) — следующий шаг (Phase B2).
- **Лицензия данных**: OpenStreetMap © ODbL — атрибуция обязательна при
  публикации производных карт.

## Pattern source

- [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [OSM building tags](https://wiki.openstreetmap.org/wiki/Key:building)
- [Nominatim geocoding](https://nominatim.org/release-docs/latest/api/Search/)
