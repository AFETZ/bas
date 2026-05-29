#!/usr/bin/env python3
"""AirSim scene builder — карта тестового сценария в AirSim (зона
Андрончева А.Д. / Федотенкова А.А.).

Закрывает пункт ТЗ "Карта сценария в AirSim" как content-driven artifact.
Существующий Gazebo SDF `gazebo/worlds/iris_runway_urban.sdf` —
**single source of truth** для геометрии сцены; этот модуль:

  1. Парсит SDF (или использует hardcoded URBAN_SCENE_CATALOG ниже —
     синхронизирован с issgr_api_server URBAN_OBSTACLES и SDF).
  2. Конвертирует Gazebo ENU → AirSim NED.
  3. Генерирует AirSim `settings.json` template для Multirotor scene
     с initial vehicle pose, camera setup, sensors.
  4. Через `simSpawnObject` API populates current AirSim runtime сцену
     с primitives (Cube/Cylinder) в соответствующих позициях. Это
     способ имитировать карту сценария **без custom UE5 map asset** —
     работает поверх Blocks / любой default AirSim сцены.

Coordinate convention:

  Gazebo ENU                AirSim NED
  --------------            -----------------
  x = East                  x = North
  y = North                 y = East
  z = Up (positive)         z = Down (positive)

  → AirSim_x = Gazebo_y
  → AirSim_y = Gazebo_x
  → AirSim_z = -Gazebo_z  (для центра объекта = высота_центра ground = -height/2)

CLI:
  # Сгенерировать settings.json:
  ./airsim_scene_builder.py --emit-settings > /tmp/settings.json

  # Populate live AirSim сцену (требует airsim или stub):
  ./airsim_scene_builder.py --populate \
      --airsim-host=127.0.0.1 --airsim-port=41451 \
      --spawn-log=logs/airsim_scene_spawn.jsonl

  # Сгенерировать + populate:
  ./airsim_scene_builder.py --emit-settings --populate
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from airsim_client import (   # noqa: E402
    AirSimRpcClient, AirSimRpcError, Pose, Quaternionr, Vector3r,
)


# ---------------------------------------------------------------------------
# Scene catalog (sync with gazebo/worlds/iris_runway_urban.sdf и
# scripts/issgr_api_server.py URBAN_OBSTACLES).
#
# Координаты — local **NED**:
#   north_m = north offset from origin (positive = north)
#   east_m  = east offset from origin (positive = east)
#   size_n_m, size_e_m = footprint dimensions
#   height_m = vertical extent (z_down = -height/2 для центра)
# ---------------------------------------------------------------------------
@dataclass
class SceneObject:
    name: str
    asset: str          # AirSim asset name: Cube / Cylinder / ...
    north_m: float
    east_m: float
    size_n_m: float
    size_e_m: float
    height_m: float
    material: str       # metal / concrete / brick / wood / tree
    category: str       # building / tower / hangar / vehicle / tree / streetlight
    base_offset_m: float = 0.0   # высота рельефа под объектом (м над datum);
    #                              0 = плоская земля. Заполняется из OSM/terrain.


# ---------------------------------------------------------------------------
# Semantic segmentation IDs (Cosys-AirSim simSetSegmentationObjectID).
#
# Каждой категории — стабильный объект-ID, чтобы segmentation-камера давала
# воспроизводимые маски для CV-pipeline (yolo-detector / issgr classifier).
# ID < 256 (AirSim кодирует id в 8-bit на канал, цвет берётся из палитры).
# ---------------------------------------------------------------------------
SEGMENTATION_IDS: dict[str, int] = {
    "building":    20,
    "tower":       21,
    "hangar":      22,
    "warehouse":   23,
    "vehicle":     30,
    "tree":        40,
    "streetlight": 50,
    "ground":       1,
}
DEFAULT_SEGMENTATION_ID = 60


def segmentation_id(obj: "SceneObject") -> int:
    """Стабильный segmentation object-ID по категории объекта."""
    return SEGMENTATION_IDS.get(obj.category, DEFAULT_SEGMENTATION_ID)


URBAN_SCENE_CATALOG: list[SceneObject] = [
    # Existing RF demo obstacles (Stage 2.4 back-compat).
    SceneObject("Hangar",             "Cube",      0.0,  45.0, 32.0, 20.0, 18.0, "metal",    "hangar"),
    SceneObject("ControlTower",       "Cylinder", 32.0,  82.0,  9.0,  9.0, 24.0, "concrete", "tower"),
    # Multi-storey buildings из urban scene.
    SceneObject("OfficeTower",        "Cube",   -40.0,  60.0, 15.0, 15.0, 40.0, "concrete", "building"),
    SceneObject("ApartmentBlock",     "Cube",    40.0,  60.0, 15.0, 25.0, 25.0, "brick",    "building"),
    SceneObject("Warehouse",          "Cube",     0.0, 100.0, 40.0, 30.0, 12.0, "metal",    "building"),
    SceneObject("ResidentialTower",   "Cube",   -50.0, 120.0, 12.0, 12.0, 60.0, "concrete", "tower"),
    SceneObject("Mall",               "Cube",    50.0, 140.0, 30.0, 50.0, 15.0, "brick",    "building"),
    SceneObject("Commercial",         "Cube",    60.0,  30.0, 10.0, 18.0,  8.0, "concrete", "building"),
    # Trees (12 шт. вокруг застройки).
    SceneObject("Tree_NW1",           "Cylinder", -25.0,  20.0,  3.0,  3.0,  6.0, "tree", "tree"),
    SceneObject("Tree_NW2",           "Cylinder", -25.0,  35.0,  3.0,  3.0,  7.0, "tree", "tree"),
    SceneObject("Tree_NE1",           "Cylinder",  25.0,  20.0,  3.0,  3.0,  6.0, "tree", "tree"),
    SceneObject("Tree_NE2",           "Cylinder",  25.0,  35.0,  3.0,  3.0,  6.5, "tree", "tree"),
    SceneObject("Tree_SE1",           "Cylinder",  55.0, 100.0,  3.0,  3.0,  7.0, "tree", "tree"),
    SceneObject("Tree_SE2",           "Cylinder",  55.0, 115.0,  3.0,  3.0,  6.0, "tree", "tree"),
    SceneObject("Tree_E1",            "Cylinder",  10.0, 150.0,  3.0,  3.0,  6.0, "tree", "tree"),
    SceneObject("Tree_E2",            "Cylinder",  -5.0, 150.0,  3.0,  3.0,  7.0, "tree", "tree"),
    SceneObject("Tree_W1",            "Cylinder", -30.0,  90.0,  3.0,  3.0,  6.0, "tree", "tree"),
    SceneObject("Tree_W2",            "Cylinder", -40.0, 100.0,  3.0,  3.0,  7.0, "tree", "tree"),
    SceneObject("Tree_N1",            "Cylinder",  -15.0,  10.0,  3.0,  3.0,  6.0, "tree", "tree"),
    SceneObject("Tree_S1",            "Cylinder",   65.0,  60.0,  3.0,  3.0,  7.0, "tree", "tree"),
    # Streetlights (4).
    SceneObject("Streetlight_1",      "Cylinder", -15.0,  50.0,  1.0,  1.0,  8.0, "metal", "streetlight"),
    SceneObject("Streetlight_2",      "Cylinder",  15.0,  50.0,  1.0,  1.0,  8.0, "metal", "streetlight"),
    SceneObject("Streetlight_3",      "Cylinder",   0.0,  90.0,  1.0,  1.0,  8.0, "metal", "streetlight"),
    SceneObject("Streetlight_4",      "Cylinder",   0.0, 130.0,  1.0,  1.0,  8.0, "metal", "streetlight"),
    # Vehicles (2).
    SceneObject("Vehicle_Sedan",      "Cube",      5.0,  55.0,  2.0,  5.0,  1.5, "metal", "vehicle"),
    SceneObject("Vehicle_Truck",      "Cube",     -8.0,  95.0,  2.5,  8.0,  3.0, "metal", "vehicle"),
]


# ---------------------------------------------------------------------------
# AirSim settings.json template
# ---------------------------------------------------------------------------
def emit_settings_json(
    scene: list[SceneObject] | None = None,
    vehicle_name: str = "Iris1",
    origin_lat: float = -35.363262,
    origin_lon: float = 149.165237,
    origin_alt_m: float = 584.0,
) -> dict[str, Any]:
    """Build AirSim settings.json structure для урбан-сцены.

    Возвращает dict suitable для `json.dumps`. Не пишет файл сам —
    caller решает где сохранить (типично ~/Documents/AirSim/settings.json).
    """
    scene = scene or URBAN_SCENE_CATALOG
    return {
        "SettingsVersion": 2.0,
        "SimMode": "Multirotor",
        "ClockType": "SteppableClock",
        "ViewMode": "FlyWithMe",
        "OriginGeopoint": {
            "Latitude": origin_lat,
            "Longitude": origin_lon,
            "Altitude": origin_alt_m,
        },
        "Vehicles": {
            vehicle_name: {
                "VehicleType": "SimpleFlight",
                "X": 0, "Y": 0, "Z": -2.0,   # 2м над землёй
                "Yaw": 0,
                "Cameras": {
                    "front_center_cam": {
                        "CaptureSettings": [{
                            "ImageType": 0,   # Scene RGB
                            "Width": 256, "Height": 144,
                            "FOV_Degrees": 80,
                        }],
                        "X": 0.5, "Y": 0, "Z": 0.0,
                        "Pitch": -45, "Roll": 0, "Yaw": 0,
                    },
                    "fpv_cam": {
                        "CaptureSettings": [{
                            "ImageType": 0,
                            "Width": 640, "Height": 480,
                            "FOV_Degrees": 90,
                        }],
                        "X": 0.3, "Y": 0, "Z": -0.1,
                        "Pitch": -5, "Roll": 0, "Yaw": 0,
                    },
                },
                "Sensors": {
                    "Lidar1": {
                        "SensorType": 6,   # Lidar
                        "Enabled": True,
                        "NumberOfChannels": 16,
                        "Range": 100.0,
                        "PointsPerSecond": 100_000,
                        "X": 0, "Y": 0, "Z": -0.1,
                        "Pitch": 0, "Roll": 0, "Yaw": 0,
                    },
                    "Barometer1": {"SensorType": 1, "Enabled": True},
                    "Imu1":       {"SensorType": 2, "Enabled": True},
                    "Gps1":       {"SensorType": 3, "Enabled": True},
                    "Magnetometer1": {"SensorType": 4, "Enabled": True},
                },
            },
        },
        # Метаданные scene (не AirSim-стандарт, но мы их пишем чтобы
        # spawner.populate_scene мог reload sci из той же settings.json).
        "_BasUrbanScene": {
            "object_count": len(scene),
            "origin_geopoint_ned": {"x_north_m": 0, "y_east_m": 0, "z_down_m": 0},
            "objects": [asdict(o) for o in scene],
            "source_sdf": "gazebo/worlds/iris_runway_urban.sdf",
            "issgr_seed_profile": "urban",
        },
    }


# ---------------------------------------------------------------------------
# SDF parser (опц. — для verification что catalog синхронизирован с SDF)
# ---------------------------------------------------------------------------
def parse_sdf_models(sdf_path: Path) -> list[dict[str, Any]]:
    """Парсит SDF, возвращает список {name, pose:[x,y,z,r,p,yaw], boxes:[...]}.

    Используется CI smoke для cross-check: SDF model count должен совпасть
    с URBAN_SCENE_CATALOG (плюс/минус GCS, runway plane, default actors).
    """
    tree = ET.parse(sdf_path)
    root = tree.getroot()
    out: list[dict[str, Any]] = []
    for model in root.iter("model"):
        name = model.get("name") or ""
        pose_el = model.find("pose")
        pose = [0.0] * 6
        if pose_el is not None and pose_el.text:
            pose = [float(x) for x in pose_el.text.split()][:6]
            while len(pose) < 6:
                pose.append(0.0)
        boxes: list[list[float]] = []
        for box in model.iter("box"):
            sz_el = box.find("size")
            if sz_el is not None and sz_el.text:
                sz = [float(x) for x in sz_el.text.split()][:3]
                if len(sz) == 3:
                    boxes.append(sz)
        out.append({"name": name, "pose": pose, "boxes": boxes})
    return out


# ---------------------------------------------------------------------------
# OSM-driven scene source — реальная геометрия зданий из import_osm_scenario
# ---------------------------------------------------------------------------
def _ring_footprint(ring: list[list[float]]) -> tuple[float, float]:
    """GeoJSON ring [[lon,lat],...] → (span_north_m, span_east_m) AABB."""
    pts = [p for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 2:
        return 1.0, 1.0
    lons = [float(p[0]) for p in pts]
    lats = [float(p[1]) for p in pts]
    clat = sum(lats) / len(lats)
    span_n = (max(lats) - min(lats)) * 111_320.0
    span_e = (max(lons) - min(lons)) * 111_320.0 * math.cos(math.radians(clat))
    return max(span_n, 1.0), max(span_e, 1.0)


def _osm_category(issgr_class: str, height_m: float) -> str:
    """Категория объекта из issgr_class (или эвристика по высоте)."""
    cls = (issgr_class or "").lower()
    for key in ("tower", "hangar", "warehouse"):
        if key in cls:
            return key
    if "tree" in cls or "vegetation" in cls:
        return "tree"
    if "vehicle" in cls or "car" in cls:
        return "vehicle"
    # Высокие здания трактуем как tower — отдельная segmentation-метка.
    return "tower" if height_m >= 40.0 else "building"


def scene_from_osm(
    obstacles_json: Path,
    max_objects: int = 200,
    flatten_terrain: bool = False,
) -> list[SceneObject]:
    """ИССГР obstacles JSON (из import_osm_scenario.py) → list[SceneObject].

    В отличие от hardcoded URBAN_SCENE_CATALOG использует **реальную**
    геометрию OSM:

      * footprint (size_n/size_e) — AABB полигона здания (geometry_polygon);
      * позиция — properties.local_north_m / local_east_m (от origin);
      * высота — height_m (OSM height / building:levels);
      * категория — из issgr_class либо эвристика по высоте;
      * рельеф — properties.base_elevation_m, нормированный к минимальной
        отметке набора (низшее здание = земля), чтобы z-смещения отражали
        перепад рельефа без знания AMSL datum'а сцены.

    flatten_terrain=True → игнорировать base_elevation (всё на z=0).
    """
    raw = json.loads(Path(obstacles_json).read_text(encoding="utf-8"))
    if isinstance(raw, dict):   # допускаем {"items"/"features"/"obstacles": [...]}
        raw = raw.get("items") or raw.get("features") or raw.get("obstacles") or []
    recs = list(raw)[:max_objects]

    # База рельефа: минимальная отметка набора → 0 (земля).
    elevs = [float(r["properties"]["base_elevation_m"])
             for r in recs
             if isinstance(r.get("properties"), dict)
             and "base_elevation_m" in r["properties"]]
    min_elev = min(elevs) if elevs else 0.0

    scene: list[SceneObject] = []
    seen: dict[str, int] = {}
    for i, r in enumerate(recs):
        props = r.get("properties") or {}
        geom = r.get("geometry_polygon") or r.get("geometry") or {}
        coords = geom.get("coordinates") or [[]]
        ring = coords[0] if coords else []
        span_n, span_e = _ring_footprint(ring)
        north = float(props.get("local_north_m", 0.0))
        east = float(props.get("local_east_m", 0.0))
        height = float(r.get("height_m") or 8.0)
        material = str(r.get("material", "concrete"))
        category = _osm_category(str(r.get("issgr_class", "")), height)
        base_off = 0.0
        if not flatten_terrain and "base_elevation_m" in props:
            base_off = float(props["base_elevation_m"]) - min_elev

        # Уникализируем имя (object_name должен быть уникален в AirSim level).
        base_name = str(r.get("name") or f"osm-{i}").replace(" ", "_")
        n_dup = seen.get(base_name, 0)
        seen[base_name] = n_dup + 1
        name = base_name if n_dup == 0 else f"{base_name}_{n_dup}"

        scene.append(SceneObject(
            name=name, asset="Cube",
            north_m=round(north, 2), east_m=round(east, 2),
            size_n_m=round(span_n, 2), size_e_m=round(span_e, 2),
            height_m=round(height, 2),
            material=material, category=category,
            base_offset_m=round(base_off, 2),
        ))
    return scene


# ---------------------------------------------------------------------------
# Populator — spawn objects через simSpawnObject
# ---------------------------------------------------------------------------
def _scene_object_pose(obj: SceneObject) -> Pose:
    """SceneObject NED → AirSim Pose.

    Низ объекта стоит на рельефе (base_offset_m над datum, NED z_down
    отрицательный вверх), центр приподнят на height/2:

        z_down(center) = -(base_offset_m + height_m / 2)
    """
    z_center_down = -(obj.base_offset_m + obj.height_m / 2.0)
    return Pose(
        position=Vector3r(x=obj.north_m, y=obj.east_m, z=z_center_down),
        orientation=Quaternionr(),   # identity (no rotation)
    )


def _scene_object_scale(obj: SceneObject) -> Vector3r:
    """Object scale. AirSim Cube — unit 1м × 1м × 1м, Cylinder — 1м diameter."""
    if obj.asset == "Cube":
        return Vector3r(x=obj.size_n_m, y=obj.size_e_m, z=obj.height_m)
    if obj.asset == "Cylinder":
        # Используем больший из size_n/size_e как diameter.
        d = max(obj.size_n_m, obj.size_e_m)
        return Vector3r(x=d, y=d, z=obj.height_m)
    return Vector3r(x=obj.size_n_m, y=obj.size_e_m, z=obj.height_m)


def populate_scene(
    client: AirSimRpcClient,
    scene: list[SceneObject] | None = None,
    name_prefix: str = "BasUrban_",
    spawn_log: Path | None = None,
    set_segmentation: bool = True,
) -> list[dict[str, Any]]:
    """Spawn each scene object в AirSim. Returns list записей о spawn'ах.

    На stub server (`airsim_stub_server.py`) — записывает в spawn-log
    для verification без real UE5. На real AirSim — Cosys-AirSim
    `simSpawnObject` создаёт actor в текущем UE5 level.

    set_segmentation=True → после spawn'а каждому объекту присваивается
    стабильный segmentation object-ID (по категории) через
    `simSetSegmentationObjectID`, чтобы segmentation-камера давала
    воспроизводимые семантические маски для CV-pipeline.
    """
    scene = scene or URBAN_SCENE_CATALOG
    records: list[dict[str, Any]] = []

    for obj in scene:
        object_name = f"{name_prefix}{obj.name}"
        pose = _scene_object_pose(obj)
        scale = _scene_object_scale(obj)
        try:
            result = client.call(
                "simSpawnObject",
                object_name,                 # object_name
                obj.asset,                   # asset_name (Cube / Cylinder / ...)
                pose.to_msgpack(),           # pose
                scale.to_msgpack(),          # scale Vector3r
                False,                       # physics_enabled (static obstacle)
                False,                       # is_blueprint (asset name)
            )
            ok = bool(result) or result == object_name
        except (AirSimRpcError, OSError) as e:
            result = f"<error: {e}>"
            ok = False

        # Семантическая segmentation-метка (best-effort; на stub — учитывается,
        # на real Cosys-AirSim красит mesh в палитру по object-ID).
        seg_id = segmentation_id(obj)
        seg_ok = False
        if set_segmentation and ok:
            try:
                seg_res = client.call(
                    "simSetSegmentationObjectID",
                    object_name,   # mesh_name
                    seg_id,        # object_id
                    False,         # is_name_regex
                )
                seg_ok = seg_res is not False
            except (AirSimRpcError, OSError):
                seg_ok = False

        rec = {
            "wall_time": time.time(),
            "object_name": object_name,
            "asset_name": obj.asset,
            "category": obj.category,
            "material": obj.material,
            "pose_ned": {"x_north": pose.position.x, "y_east": pose.position.y,
                         "z_down": pose.position.z},
            "scale": {"x": scale.x, "y": scale.y, "z": scale.z},
            "base_offset_m": obj.base_offset_m,
            "segmentation_id": seg_id,
            "segmentation_ok": seg_ok,
            "ok": ok, "result": result,
        }
        records.append(rec)
        if spawn_log is not None:
            with spawn_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return records


def destroy_scene(
    client: AirSimRpcClient,
    scene: list[SceneObject] | None = None,
    name_prefix: str = "BasUrban_",
) -> int:
    """Cleanup spawn'ed objects (для repeat run'ов)."""
    scene = scene or URBAN_SCENE_CATALOG
    n = 0
    for obj in scene:
        try:
            if client.call("simDestroyObject", f"{name_prefix}{obj.name}"):
                n += 1
        except (AirSimRpcError, OSError):
            pass
    return n


# ---------------------------------------------------------------------------
# Scene stats
# ---------------------------------------------------------------------------
def scene_stats(scene: list[SceneObject] | None = None) -> dict[str, Any]:
    scene = scene or URBAN_SCENE_CATALOG
    by_cat: dict[str, int] = {}
    by_mat: dict[str, int] = {}
    by_seg: dict[int, int] = {}
    total_vol_m3 = 0.0
    max_base_off = 0.0
    for o in scene:
        by_cat[o.category] = by_cat.get(o.category, 0) + 1
        by_mat[o.material] = by_mat.get(o.material, 0) + 1
        sid = segmentation_id(o)
        by_seg[sid] = by_seg.get(sid, 0) + 1
        max_base_off = max(max_base_off, o.base_offset_m)
        # Approx volume: для Cube — size_n × size_e × height; для Cylinder
        # — π·(d/2)²·height.
        if o.asset == "Cylinder":
            d = max(o.size_n_m, o.size_e_m)
            total_vol_m3 += math.pi * (d / 2) ** 2 * o.height_m
        else:
            total_vol_m3 += o.size_n_m * o.size_e_m * o.height_m
    return {
        "total_objects": len(scene),
        "by_category": by_cat,
        "by_material": by_mat,
        "by_segmentation_id": dict(sorted(by_seg.items())),
        "terrain_relief_m": round(max_base_off, 1),
        "total_volume_m3": round(total_vol_m3, 1),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--emit-settings", action="store_true",
                   help="Печатает AirSim settings.json в stdout")
    p.add_argument("--settings-output", type=Path,
                   help="Записать settings.json в файл (по умолчанию — stdout)")
    p.add_argument("--populate", action="store_true",
                   help="Spawn objects в live AirSim через simSpawnObject")
    p.add_argument("--destroy-first", action="store_true",
                   help="Удалить любые предыдущие BasUrban_* объекты перед spawn'ом")
    p.add_argument("--airsim-host", default="127.0.0.1")
    p.add_argument("--airsim-port", type=int, default=41451)
    p.add_argument("--spawn-log", type=Path,
                   help="NDJSON record каждого spawn результата")
    p.add_argument("--validate-sdf", type=Path,
                   help="Cross-check parsed SDF model count vs catalog")
    p.add_argument("--print-stats", action="store_true",
                   help="Печатать scene stats summary")
    p.add_argument("--from-osm", type=Path,
                   help="Источник сцены — ИССГР obstacles JSON из "
                        "import_osm_scenario.py (реальная геометрия OSM "
                        "вместо встроенного URBAN_SCENE_CATALOG)")
    p.add_argument("--max-objects", type=int, default=200,
                   help="Лимит объектов из --from-osm (default 200)")
    p.add_argument("--flatten-terrain", action="store_true",
                   help="Игнорировать base_elevation из OSM (всё на z=0)")
    p.add_argument("--no-segmentation", action="store_true",
                   help="Не присваивать segmentation object-ID при spawn'е")
    args = p.parse_args()

    # Источник сцены: реальная OSM-геометрия или встроенный urban catalog.
    if args.from_osm:
        scene = scene_from_osm(args.from_osm, max_objects=args.max_objects,
                               flatten_terrain=args.flatten_terrain)
        print(f"[scene] loaded {len(scene)} objects from OSM {args.from_osm}",
              file=sys.stderr)
    else:
        scene = URBAN_SCENE_CATALOG

    if args.print_stats or (not args.emit_settings and not args.populate
                             and not args.validate_sdf):
        s = scene_stats(scene)
        print(f"Scene: {s['total_objects']} objects, "
              f"volume ≈ {s['total_volume_m3']:.0f} m³")
        print(f"  by category: {s['by_category']}")
        print(f"  by material: {s['by_material']}")
        print(f"  segmentation IDs: {s['by_segmentation_id']}")

    if args.validate_sdf:
        sdf_models = parse_sdf_models(args.validate_sdf)
        print(f"SDF {args.validate_sdf}: {len(sdf_models)} <model> blocks")
        # Sanity: 6+ multi-storey buildings, 2 vehicles, плюс ground/iris.
        names = [m["name"] for m in sdf_models]
        # Catalog должен покрыть основную геометрию; некоторые SDF models
        # (iris UAV, ground plane, runway) не попадают в catalog.
        catalog_names = {o.name.replace("_", " ") for o in URBAN_SCENE_CATALOG}
        common = sum(1 for n in names if any(
            cn.lower() in n.lower() or n.lower() in cn.lower()
            for cn in catalog_names
        ))
        print(f"  overlap with catalog: ~{common} / {len(catalog_names)}")

    if args.emit_settings:
        settings = emit_settings_json(scene)
        out_str = json.dumps(settings, indent=2, ensure_ascii=False)
        if args.settings_output:
            args.settings_output.parent.mkdir(parents=True, exist_ok=True)
            args.settings_output.write_text(out_str, encoding="utf-8")
            print(f"settings.json → {args.settings_output} "
                  f"({len(out_str)} bytes, {len(scene)} _BasUrbanScene objects)")
        else:
            print(out_str)

    if args.populate:
        if args.spawn_log:
            args.spawn_log.parent.mkdir(parents=True, exist_ok=True)
            args.spawn_log.unlink(missing_ok=True)
        try:
            client = AirSimRpcClient(host=args.airsim_host,
                                     port=args.airsim_port, timeout_s=5.0)
            ping_ok = client.call("ping")
            print(f"[airsim] connect {args.airsim_host}:{args.airsim_port}  ping={ping_ok}")
        except (AirSimRpcError, OSError) as e:
            print(f"[airsim] ERROR connect: {e}", file=sys.stderr)
            return 2
        if args.destroy_first:
            n = destroy_scene(client, scene)
            print(f"[scene] destroyed {n} pre-existing BasUrban_* objects")
        recs = populate_scene(client, scene=scene, spawn_log=args.spawn_log,
                              set_segmentation=not args.no_segmentation)
        ok = sum(1 for r in recs if r["ok"])
        seg_ok = sum(1 for r in recs if r.get("segmentation_ok"))
        print(f"[scene] spawned {ok}/{len(recs)} objects "
              f"(prefix=BasUrban_*); segmentation set {seg_ok}/{ok}; "
              f"log={args.spawn_log or '<none>'}")
        # Verify через simListSceneObjects (если support).
        try:
            objs = client.call("simListSceneObjects")
            spawned_in_list = sum(1 for o in (objs or [])
                                  if isinstance(o, str) and o.startswith("BasUrban_"))
            print(f"[scene] listSceneObjects: total={len(objs or [])}, "
                  f"BasUrban_* in list={spawned_in_list}")
        except (AirSimRpcError, OSError):
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
