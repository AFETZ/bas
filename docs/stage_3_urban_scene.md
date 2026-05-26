# Stage 3 — Urban Gazebo scene

Расширенная карта тестового сценария с urban props на geometric primitives.
Закрывает пункт ТЗ "Создание и наполнение карты тестового сценария
моделирования на основе реалистичных данных" (зона Карпов А.К. + Маргарян А.Г.
по ТЗ распределения).

> Архитектурный артефакт без external mesh dependencies (всё на box/cylinder/cone/sphere).
> Для production-grade artwork оператор подключает свои model:// пакеты —
> wrapper передаёт сцену через `BAS_GAZEBO_WORLD`.

## Что в сцене

| Категория | Количество | Размер/высота |
|---|---:|---|
| RF demo obstacles (back-compat) | 3 | Hangar 20×32×18м, Tower 9×9×24м, GCS mast 5м |
| Multi-storey buildings | 6 | от 18×8м commercial до 12×12×60м residential tower |
| Asphalt roads | 3 | main avenue NS 180×8м + 2 cross streets 200×6м |
| Trees | 12 | trunk 0.3м radius × 3-3.6м height + canopy 2.5-4м radius (mix sphere/cone) |
| Streetlights | 4 | pole 6м + emissive lamp sphere |
| Parked vehicles | 2 | car body 4.2×1.7×1.2м + roof |
| UAV | 1 | iris_with_gimbal (камера, ardupilot FDM port 9002) |

Сцена покрывает ~200×200м вокруг origin (0,0). По фактической Gazebo ENU
геометрии основная застройка находится восточнее точки старта.

## Координаты (local NED, метры от UAV origin)

Координаты ниже в UI/MAVLink local NED: `N` = north, `E` = east. В SDF они
лежат как ENU (`x=east`, `y=north`), поэтому при сверке с
`iris_runway_urban.sdf` нужно читать `pose x y` как `E N`.

```
                    Mall (N=50,E=140, 30×50×15м)
            Residential tower (N=-50,E=120, 12×12×60м)
                    │
        Warehouse (N=0,E=100, 40×30×12м)
                    │
              Tree06 (N=25,E=150)
                    │
        Tower (N=32,E=82, 9×9×24м)              Commercial (N=60,E=30, 10×18×8м)
                    │
        Office (N=-40,E=60)    Apartment (N=40,E=60)
                    │
        Hangar (N=0,E=45, 32×20×18м)
                    │
              Streetlight (N=±5,E=40)
                    │
        Vehicle (N=±12,E=80 — на дороге)
                    │
    Tree (N=±30,E=15..25)
                    │
══════ UAV spawn (0,0) ══════
              GCS mast (N=-60,E=0)
```

## Файлы

| Файл | Что |
|---|---|
| `gazebo/worlds/iris_runway_urban.sdf` | Главная SDF сцена |
| `scripts/run_stage_3_urban_demo.sh` | Wrapper: FPV+RF stack с urban world + urban RF profile |
| `scripts/issgr_api_server.py` | Расширен `--seed-profile urban` для 6 buildings ИССГР seed |
| `scripts/gcs_web_ui_server.py` | `RF_OBSTACLES_URBAN` + `BAS_RF_OBSTACLE_PROFILE=urban` env переключатель |

## Запуск

```bash
sudo bash scripts/run_stage_3_urban_demo.sh
```

Откроется Gazebo окно с urban scene. В браузере http://127.0.0.1:8765/
видны все 6 zданий на карте как obstacles, FPV окно показывает реальный
urban view с борта.

Для headless smoke (без Gazebo GUI):
```bash
sudo env BAS_GAZEBO_GUI=0 bash scripts/run_stage_3_urban_demo.sh
```

ИССГР с urban seed:
```bash
sudo env BAS_ISSGR_SEED_PROFILE=urban bash scripts/run_stage_3_issgr_demo.sh
```

Или standalone:
```bash
.venv/bin/python scripts/issgr_api_server.py --port 8770 --seed-profile urban
```

## Verified

```
=== ISSGR seed (urban profile) ===
{
  "collections": {"obstacles": 8, "gcs": 1, ...},
  "total_objects": 9
}

=== /collections/obstacles/items ===
  - Hangar               class=geospatial_objects.building.hangar    h=18.0м metal
  - Control Tower        class=geospatial_objects.building.tower     h=24.0м concrete
  - Office tower         class=geospatial_objects.building.generic   h=40.0м concrete
  - Apartment block      class=geospatial_objects.building.generic   h=25.0м brick
  - Warehouse            class=geospatial_objects.building.generic   h=12.0м metal
  - Residential tower    class=geospatial_objects.building.tower     h=60.0м concrete
  - Mall                 class=geospatial_objects.building.generic   h=15.0м brick
  - Commercial           class=geospatial_objects.building.generic   h= 8.0м concrete
```

GeoJSON FeatureCollection через `/digital_twin` совместим с QGIS overlay —
все 8 buildings + 1 GCS видны на real map в Canberra coords.

## Интеграция с другими stages

| Stage | Что меняется |
|---|---|
| 2.4 FPV+RF | UAV видит больше препятствий через камеру, RF panel показывает 8 препятствий вместо 2 |
| 2.4 Multi-UAV | 2 SITL спавнятся, оба пролетают через urban сцену |
| 2.4 Auto demo | recorder снимает скриншоты с urban в кадре |
| 2.1 Sionna RT | Можно re-generate `scene/iris_runway.xml` (Mitsuba) с urban geometry для real ray-tracing через здания |
| 2.2 Cosys-AirSim overlay | bridge форвардит UAV pose в AirSim — там можно подложить свой UE5 urban env |
| 3 CV detector | YOLOv8 не находит COCO classes в urban Blocks (нет real cars/people, только geometric props) — для test нужен либо fine-tune, либо AirSim с realistic actors |
| 3 ISSGR API | `--seed-profile urban` загружает 6 buildings в naземную часть ИССГР, доступны через REST /collections/obstacles/items |

## Что **не** сделано (вне scope)

- **Texture maps** (asphalt, brick, glass) — все materials simple color
- **Dynamic elements**: traffic light cycling, parked vehicles → moving (можно
  через `<plugin>` JointController), pedestrians (NPC sim Carla-style)
- **3D mesh assets** (Blender DAE/OBJ) — не подключены, оператор может
  добавить через `<include><uri>model://my-asset</uri>` если хочет
- **Sionna scene re-export** — `scripts/export_scene_to_sionna.py` нужно
  расширить чтобы urban_obstacles тоже попадали в Mitsuba XML. Текущий
  exporter знает только RF demo basic obstacles.

## Pattern source

- [SDF format 1.9 specification](http://sdformat.org/spec)
- [Gazebo Harmonic primitives](https://gazebosim.org/docs/harmonic/sdf_worlds)
- [ardupilot_gazebo plugin](https://github.com/ArduPilot/ardupilot_gazebo)
- `gazebo/worlds/iris_runway_rf_demo.sdf` — base scene (от Stage 2.4 RF demo)
