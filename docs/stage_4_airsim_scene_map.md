# Stage 4 — Карта сценария в AirSim

Закрывает пункт ТЗ "Карта сценария в AirSim" (зона
Андрончева А.Д. / Федотенкова А.А.) как content-driven artifact +
runtime spawner — без необходимости поставлять custom UE5 map asset.

## Подход

Существующая Gazebo SDF сцена `gazebo/worlds/iris_runway_urban.sdf` —
**single source of truth** для геометрии. Этот модуль:

1. Хранит синхронизированный с SDF/ИССГР catalog `URBAN_SCENE_CATALOG`
   (26 объектов: hangar, control tower, 6 multi-storey buildings,
   12 trees, 4 streetlights, 2 vehicles), 79 000 м³ суммарного volume;
2. Генерирует AirSim `settings.json` template с vehicle config
   (2 камеры + 5 sensors: lidar, baro, IMU, GPS, magnetometer);
3. Через Cosys-AirSim `simSpawnObject` API populates current UE5
   level с primitives (Cube/Cylinder) в правильных позициях —
   работает поверх любого default AirSim asset (Blocks, Neighborhood,
   AirSimNH, Plains) **без custom map**.

## Coordinate convention

```
Gazebo ENU                  AirSim NED
--------------              ----------
x = East                    x = North
y = North                   y = East
z = Up (positive)           z = Down (positive)

AirSim_x = Gazebo_y
AirSim_y = Gazebo_x
AirSim_z = -Gazebo_z  (центр объекта = -height/2 ниже origin)
```

URBAN_SCENE_CATALOG уже в local NED формате (north/east/height) — это
тот же формат что в `scripts/issgr_api_server.py URBAN_OBSTACLES`,
поэтому AirSim/ИССГР/Gazebo edit-once.

## Catalog содержимое

| Категория | Шт. | Asset | Material variety |
|---|---:|---|---|
| hangar | 1 | Cube | metal |
| tower | 2 | Cylinder + Cube | concrete |
| building | 5 | Cube | concrete (3) + brick (2) |
| tree | 12 | Cylinder | tree (custom material tag) |
| streetlight | 4 | Cylinder | metal |
| vehicle | 2 | Cube | metal |
| **Total** | **26** | — | — |

Координатное покрытие: ~150м × ~150м area вокруг origin (CMAC
default -35.363262°, 149.165237°). Total volume ≈ 79 000 м³.

## Файлы

| Файл | Что |
|---|---|
| `scripts/airsim_scene_builder.py` | `URBAN_SCENE_CATALOG`, `emit_settings_json`, `populate_scene`, `destroy_scene`, SDF parser, CLI |
| `scripts/airsim_stub_server.py` | Расширен: `simSpawnObject`, `simDestroyObject`, `simListAssets`, `--spawn-log` |
| `scripts/airsim_client.py` | Добавлен public `call(method, *args)` alias для generic RPC |
| `scripts/_airsim_scene_smoke.py` | CI smoke: 4 этапа (settings + SDF + stats + spawn roundtrip) |
| `docs/stage_4_airsim_scene_map.md` | Этот файл |

## Запуск

### Генерация AirSim settings.json

```bash
# В stdout:
./scripts/airsim_scene_builder.py --emit-settings

# В файл:
./scripts/airsim_scene_builder.py --emit-settings \
    --settings-output ~/Documents/AirSim/settings.json
```

settings.json содержит `Vehicles.Iris1` с camera/sensor config + `_BasUrbanScene` metadata block (наш extension, AirSim ignores unknown keys).

### Populate live AirSim scene

```bash
# С airsim_stub_server (CI):
./scripts/airsim_stub_server.py --port 41451 --spawn-log /tmp/spawn.jsonl &
./scripts/airsim_scene_builder.py --populate \
    --airsim-host=127.0.0.1 --airsim-port=41451 \
    --spawn-log=/tmp/spawn_client.jsonl
```

### Через реальный Cosys-AirSim (Windows / Linux UE5):

```bash
# UE5 Editor запущен с любым default level (Blocks):
./scripts/airsim_scene_builder.py --populate --destroy-first \
    --airsim-host=192.168.1.50 --airsim-port=41451
```

`--destroy-first` удаляет любые предыдущие `BasUrban_*` объекты для
clean re-spawn.

### CI smoke

```bash
./scripts/_airsim_scene_smoke.py
```

Verified output:

```
===== [1] settings.json generation =====
  settings.json valid; 26 catalog objects, 2 cameras, 5 sensors

===== [2] SDF parser =====
  SDF parsed: 31 <model> blocks, 14 с box geometry

===== [3] Scene stats =====
  total=26  by_cat={'hangar': 1, 'tower': 2, 'building': 5, 'tree': 12, 'streetlight': 4, 'vehicle': 2}  vol_m3=79050

===== [4] Spawn via stub server =====
  stub up on :41560
  baseline list: 4 objects
  populated: 26/26 ok
  client log=26 lines, server log=26 lines
  post-spawn list: 30 total, 26 BasUrban_*
  destroyed: 26, post-destroy BasUrban_* count=0

ALL CHECKS PASSED
```

## Wire format — simSpawnObject (Cosys-AirSim RPC)

Каждый spawn вызывает msgpack-rpc method `simSpawnObject(...)` с
аргументами:

| Arg | Type | Value |
|---|---|---|
| `object_name` | string | `"BasUrban_<Name>"` уникальный |
| `asset_name`  | string | `"Cube"` или `"Cylinder"` (UE5 default mesh) |
| `pose`        | Pose dict | `{position: Vector3r, orientation: Quaternionr}` |
| `scale`       | Vector3r | `(x: size_n, y: size_e, z: height)` для Cube |
| `physics_enabled` | bool | `False` (static obstacle, no rigid-body sim) |
| `is_blueprint` | bool | `False` (asset_name = mesh, not blueprint) |

Position = `Vector3r(x=north_m, y=east_m, z=-height_m/2)` — центр
объекта на половину высоты выше ground. Orientation = identity
quaternion (для урбан-сцены rotations не нужны).

## Cross-check с другими модулями

Catalog согласован с тремя другими источниками одной и той же сцены:

| Источник | Где | Что |
|---|---|---|
| `gazebo/worlds/iris_runway_urban.sdf` | SDF | Gazebo физика + визуал (31 `<model>` blocks, 14 с box geometry) |
| `scripts/issgr_api_server.py URBAN_OBSTACLES` | Python tuple | ИССГР REST API seed для buildings/towers (8 объектов) |
| `web/gcs/app.js RF_OBSTACLES_URBAN` | JavaScript const | Web GCS RF панель overlay (8 объектов) |
| `scripts/airsim_scene_builder.py URBAN_SCENE_CATALOG` | dataclass list | AirSim spawn (26 объектов: building/tower + trees/streetlights/vehicles) |

Catalog AirSim — superset: он включает trees/streetlights/vehicles
которых нет в ИССГР (там только RF-relevant obstacles). Это
намеренно: AirSim рендерит полную urban-сцену с visual richness,
ИССГР хранит только то что влияет на radio propagation.

## Ограничения и расширения

1. **Asset diversity** — текущий catalog использует только Cube и
   Cylinder. Cosys-AirSim Blocks scene не имеет high-fidelity building
   meshes (это были бы UE5 marketplace assets). Для realistic
   visualization нужно custom UE5 level с building blueprints,
   которые spawn'ятся через `is_blueprint=True`.

2. **No textures** — primitives spawn'ятся с default material
   (matte gray). Material tag в catalog (metal/concrete/brick) идёт
   в spawn record metadata но не применяется к UE5 actor. Для
   корректного visual material — extension через `simSetSegmentationObjectID`.

3. **Static only** — `physics_enabled=False`. Vehicles в catalog
   расположены статично; для moving traffic нужен отдельный module.

4. **Координаты до ~150м** — catalog покрывает urban core; для maps
   >20×20 км см. отдельный пункт ТЗ.

## Pattern source

- [Cosys-AirSim Spawning API](https://cosys-lab.github.io/Cosys-AirSim/apis/#sim-only-apis)
- [Microsoft AirSim settings.json reference](https://microsoft.github.io/AirSim/settings/)
- ArduPilot CMAC default location (-35.363262°, 149.165237°) — same origin что Gazebo SDF
- ИССГР `URBAN_OBSTACLES` catalog (`scripts/issgr_api_server.py`)
