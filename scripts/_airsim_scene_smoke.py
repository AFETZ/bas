#!/usr/bin/env python3
"""AirSim scene builder smoke — generate settings.json + populate stub.

Запускает stub server, прогоняет airsim_scene_builder через ping +
populate_scene, verifies:
  1. settings.json содержит правильное количество vehicles/cameras/sensors
     и _BasUrbanScene block с N catalog объектов;
  2. Spawn log писется и содержит N успешных entries;
  3. simListSceneObjects returns base + spawned;
  4. SDF parser работает на iris_runway_urban.sdf.
"""
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from airsim_scene_builder import (   # noqa: E402
    URBAN_SCENE_CATALOG, SEGMENTATION_IDS, emit_settings_json, parse_sdf_models,
    populate_scene, destroy_scene, scene_stats, scene_from_osm, segmentation_id,
)
from airsim_client import AirSimRpcClient   # noqa: E402


def _write_osm_fixture(path: Path) -> None:
    """Синтетический ИССГР obstacles JSON (формат import_osm_scenario.py)
    с реальными полигонами + перепадом рельефа base_elevation_m.

    Tall Office (60 м → tower), Low Shop (8 м → building, min elev),
    Mid Block (25 м → building). min_elev=145 → base_offset 5/0/10.
    """
    olat, olon = -35.3600, 149.1650
    deg_lat = 1.0 / 111_320.0
    deg_lon = 1.0 / (111_320.0 * math.cos(math.radians(olat)))

    def rect(n0: float, e0: float, span_n: float, span_e: float):
        """Прямоугольный ring [[lon,lat],...] вокруг (n0,e0) метров от origin."""
        lat0 = olat + n0 * deg_lat
        lon0 = olon + e0 * deg_lon
        lat1 = olat + (n0 + span_n) * deg_lat
        lon1 = olon + (e0 + span_e) * deg_lon
        clat = (lat0 + lat1) / 2.0
        clon = (lon0 + lon1) / 2.0
        nm = (clat - olat) / deg_lat
        em = (clon - olon) / deg_lon
        ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
        return ring, round(nm, 2), round(em, 2)

    specs = [
        ("Tall Office", 60.0, 30.0, 20.0, 100.0, 50.0, 150.0, "concrete"),
        ("Low Shop",     8.0, 10.0, 12.0,  20.0, 80.0, 145.0, "brick"),
        ("Mid Block",   25.0, 40.0, 15.0, -50.0, 30.0, 155.0, "glass"),
    ]
    records = []
    for i, (nm_, h, sn, se, n0, e0, elev, mat) in enumerate(specs):
        ring, nm, em = rect(n0, e0, sn, se)
        records.append({
            "id": {"domain": "bas", "system": "osm-import", "object_uuid": f"u{i}"},
            "name": nm_,
            "issgr_class": "geospatial_objects.building.generic",
            "geometry_polygon": {"type": "Polygon", "coordinates": [ring]},
            "height_m": h,
            "material": mat,
            "properties": {"osm_id": 1000 + i, "local_north_m": nm,
                           "local_east_m": em, "base_elevation_m": elev},
        })
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


STUB_PORT = 41560   # отличный от prod 41451
STUB_LOG = Path("/tmp/_airsim_scene_smoke_stub.log")
SPAWN_LOG_CLIENT = Path("/tmp/_airsim_scene_smoke_spawn_client.jsonl")
SPAWN_LOG_SERVER = Path("/tmp/_airsim_scene_smoke_spawn_server.jsonl")


def _free_port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def main() -> int:
    # --- 1. settings.json ---
    print("===== [1] settings.json generation =====")
    settings = emit_settings_json()
    assert settings["SettingsVersion"] == 2.0
    assert settings["SimMode"] == "Multirotor"
    assert "Iris1" in settings["Vehicles"]
    v = settings["Vehicles"]["Iris1"]
    assert "front_center_cam" in v["Cameras"]
    assert "fpv_cam" in v["Cameras"]
    assert "Lidar1" in v["Sensors"]
    bus = settings["_BasUrbanScene"]
    assert bus["object_count"] == len(URBAN_SCENE_CATALOG)
    assert bus["object_count"] >= 20, \
        f"expected ≥20 scene objects, got {bus['object_count']}"
    assert bus["source_sdf"] == "gazebo/worlds/iris_runway_urban.sdf"
    print(f"  settings.json valid; {bus['object_count']} catalog objects, "
          f"{len(v['Cameras'])} cameras, {len(v['Sensors'])} sensors")

    # --- 2. SDF parser ---
    print("\n===== [2] SDF parser =====")
    sdf_path = REPO / "gazebo/worlds/iris_runway_urban.sdf"
    if sdf_path.exists():
        sdf_models = parse_sdf_models(sdf_path)
        assert len(sdf_models) > 0
        building_like = [m for m in sdf_models if m["boxes"]]
        print(f"  SDF parsed: {len(sdf_models)} <model> blocks, "
              f"{len(building_like)} с box geometry")
    else:
        print(f"  SDF not found at {sdf_path}, skip")

    # --- 3. Scene stats ---
    print("\n===== [3] Scene stats =====")
    s = scene_stats()
    print(f"  total={s['total_objects']}  by_cat={s['by_category']}  "
          f"vol_m3={s['total_volume_m3']:.0f}")
    assert s["total_objects"] >= 20
    assert "building" in s["by_category"]
    assert "tree" in s["by_category"]
    assert "vehicle" in s["by_category"]
    # Catalog теперь несёт segmentation breakdown.
    assert "by_segmentation_id" in s and SEGMENTATION_IDS["building"] in s["by_segmentation_id"]

    # --- 3b. scene_from_osm: реальная OSM-геометрия + рельеф + категории ---
    print("\n===== [3b] scene_from_osm (real OSM geometry) =====")
    osm_fixture = Path("/tmp/_airsim_osm_fixture.json")
    _write_osm_fixture(osm_fixture)
    osm_scene = scene_from_osm(osm_fixture)
    assert len(osm_scene) == 3, f"expected 3, got {len(osm_scene)}"
    by_name = {o.name: o for o in osm_scene}
    assert {"Tall_Office", "Low_Shop", "Mid_Block"} <= set(by_name), list(by_name)
    tall = by_name["Tall_Office"]
    # Footprint восстановлен из полигона (±0.5 м).
    assert abs(tall.size_n_m - 30.0) < 0.5, f"span_n={tall.size_n_m}"
    assert abs(tall.size_e_m - 20.0) < 0.5, f"span_e={tall.size_e_m}"
    # Позиция — из local_north/east_m (центроид полигона).
    assert abs(tall.north_m - 115.0) < 1.0 and abs(tall.east_m - 60.0) < 1.0
    # Категория: высокое (60 м) → tower; низкие → building.
    assert tall.category == "tower", tall.category
    assert by_name["Low_Shop"].category == "building"
    assert by_name["Mid_Block"].category == "building"
    # Рельеф нормирован к минимуму (145 м): Low=0, Tall=5, Mid=10.
    assert abs(by_name["Low_Shop"].base_offset_m - 0.0) < 0.01
    assert abs(tall.base_offset_m - 5.0) < 0.01
    assert abs(by_name["Mid_Block"].base_offset_m - 10.0) < 0.01
    os_stats = scene_stats(osm_scene)
    assert os_stats["terrain_relief_m"] == 10.0, os_stats["terrain_relief_m"]
    assert segmentation_id(tall) == SEGMENTATION_IDS["tower"]
    assert segmentation_id(by_name["Low_Shop"]) == SEGMENTATION_IDS["building"]
    print(f"  3 OSM objects; Tall footprint {tall.size_n_m}×{tall.size_e_m}m @"
          f"({tall.north_m},{tall.east_m}); relief={os_stats['terrain_relief_m']}m; "
          f"seg={os_stats['by_segmentation_id']}")

    # --- 4. Spawn через airsim_stub_server ---
    print("\n===== [4] Spawn via stub server =====")
    for p in (SPAWN_LOG_CLIENT, SPAWN_LOG_SERVER, STUB_LOG):
        if p.exists():
            p.unlink()
    # Стартуем stub server в subprocess.
    stub = subprocess.Popen(
        ["python3", str(REPO / "scripts/airsim_stub_server.py"),
         "--port", str(STUB_PORT),
         "--spawn-log", str(SPAWN_LOG_SERVER)],
        stdout=open(STUB_LOG, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")},
    )
    try:
        # Wait for port open.
        for _ in range(30):
            if _free_port_open(STUB_PORT):
                break
            time.sleep(0.1)
        else:
            print("  stub didn't bind; aborting", file=sys.stderr)
            return 1
        print(f"  stub up on :{STUB_PORT}")

        client = AirSimRpcClient(host="127.0.0.1", port=STUB_PORT, timeout_s=2.0)
        assert client.call("ping") is True
        # Verify simListSceneObjects baseline.
        base = client.call("simListSceneObjects")
        assert isinstance(base, list)
        print(f"  baseline list: {len(base)} objects")

        # Populate.
        recs = populate_scene(client, spawn_log=SPAWN_LOG_CLIENT)
        ok = sum(1 for r in recs if r["ok"])
        print(f"  populated: {ok}/{len(recs)} ok")
        assert ok == len(URBAN_SCENE_CATALOG), \
            f"only {ok}/{len(URBAN_SCENE_CATALOG)} spawned successfully"

        # Client-side spawn log (наш populate_scene writes).
        assert SPAWN_LOG_CLIENT.exists()
        c_lines = SPAWN_LOG_CLIENT.read_text().strip().splitlines()
        assert len(c_lines) == len(URBAN_SCENE_CATALOG), \
            f"client log has {len(c_lines)} lines, expected {len(URBAN_SCENE_CATALOG)}"
        for ln in c_lines:
            rec = json.loads(ln)
            for k in ("object_name", "asset_name", "pose_ned", "scale",
                      "category", "material", "ok"):
                assert k in rec, f"client log missing key {k!r}: {rec}"
            assert rec["object_name"].startswith("BasUrban_")

        # Server-side spawn log (stub server writes for each RPC).
        assert SPAWN_LOG_SERVER.exists()
        s_lines = SPAWN_LOG_SERVER.read_text().strip().splitlines()
        assert len(s_lines) == len(URBAN_SCENE_CATALOG), \
            f"server log has {len(s_lines)} lines, expected {len(URBAN_SCENE_CATALOG)}"
        print(f"  client log={len(c_lines)} lines, server log={len(s_lines)} lines")

        # Verify simListSceneObjects now contains spawn list.
        after = client.call("simListSceneObjects")
        spawned_in_list = [o for o in after if isinstance(o, str)
                           and o.startswith("BasUrban_")]
        print(f"  post-spawn list: {len(after)} total, "
              f"{len(spawned_in_list)} BasUrban_*")
        assert len(spawned_in_list) == len(URBAN_SCENE_CATALOG)

        # Destroy roundtrip.
        n = destroy_scene(client)
        after_destroy = client.call("simListSceneObjects")
        spawned_after = [o for o in after_destroy if isinstance(o, str)
                         and o.startswith("BasUrban_")]
        print(f"  destroyed: {n}, post-destroy BasUrban_* count={len(spawned_after)}")
        assert n == len(URBAN_SCENE_CATALOG)
        assert len(spawned_after) == 0

        # --- 5. OSM scene spawn + segmentation round-trip ---
        print("\n===== [5] OSM scene spawn + segmentation =====")
        spawn_log_osm = Path("/tmp/_airsim_scene_smoke_osm.jsonl")
        if spawn_log_osm.exists():
            spawn_log_osm.unlink()
        recs_osm = populate_scene(client, scene=osm_scene, name_prefix="BasOsm_",
                                  spawn_log=spawn_log_osm, set_segmentation=True)
        ok_osm = sum(1 for r in recs_osm if r["ok"])
        seg_ok = sum(1 for r in recs_osm if r["segmentation_ok"])
        assert ok_osm == len(osm_scene), f"{ok_osm}/{len(osm_scene)} spawned"
        assert seg_ok == len(osm_scene), f"segmentation set {seg_ok}/{len(osm_scene)}"
        # Round-trip: stub хранит назначенный id, getSegmentationObjectID отдаёт его.
        for o in osm_scene:
            got = client.call("simGetSegmentationObjectID", f"BasOsm_{o.name}")
            assert got == segmentation_id(o), \
                f"{o.name}: seg {got} != expected {segmentation_id(o)}"
        # Spawn log несёт segmentation_id + base_offset_m.
        ol = spawn_log_osm.read_text().strip().splitlines()
        assert len(ol) == len(osm_scene)
        r0 = json.loads(ol[0])
        for k in ("segmentation_id", "segmentation_ok", "base_offset_m"):
            assert k in r0, f"osm spawn log missing key {k!r}: {r0}"
        print(f"  spawned {ok_osm}/{len(osm_scene)}, segmentation {seg_ok}, "
              f"getSegId round-trip OK for all {len(osm_scene)}")
        destroy_scene(client, osm_scene, name_prefix="BasOsm_")
    finally:
        stub.terminate()
        stub.wait(timeout=5)

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
