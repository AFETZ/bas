#!/usr/bin/env python3
"""OSM importer smoke — pure-logic (no network) + optional live fetch.

Part 1 (offline): parsing/geometry/SDF/ИССГР-emit на synthetic OSM data.
Part 2 (--live): real Overpass fetch on tiny bbox (skipped if network down).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/scripts")
from import_osm_scenario import (   # noqa: E402
    build_scenario, building_height, building_material,
    polygon_ring, ring_centroid_bbox, latlon_to_local,
    emit_issgr_json, emit_gazebo_sdf,
)

# Synthetic OSM building: square ~20m near Moscow center.
SYNTHETIC = [
    {"id": 1, "type": "way",
     "tags": {"building": "yes", "height": "24", "building:material": "concrete",
              "name": "TestTower"},
     "geometry": [{"lat": 55.7585, "lon": 37.6160},
                  {"lat": 55.7585, "lon": 37.6163},
                  {"lat": 55.7587, "lon": 37.6163},
                  {"lat": 55.7587, "lon": 37.6160}]},
    {"id": 2, "type": "way",
     "tags": {"building": "residential", "building:levels": "5"},
     "geometry": [{"lat": 55.7590, "lon": 37.6170},
                  {"lat": 55.7590, "lon": 37.6174},
                  {"lat": 55.7593, "lon": 37.6174},
                  {"lat": 55.7593, "lon": 37.6170}]},
]


def part1_offline() -> None:
    print("===== [1] offline parsing/geometry/emit =====")
    # height
    assert building_height({"height": "24"}) == 24.0
    assert building_height({"building:levels": "5"}) == 15.0
    assert building_height({}) == 10.0
    print("  ✓ building_height: 24m / 5lv→15m / default 10m")
    # material
    assert building_material({"building:material": "concrete"}) == "concrete"
    assert building_material({"material": "steel"}) == "metal"
    assert building_material({}) == "concrete"
    print("  ✓ building_material: concrete / steel→metal / default")
    # ring closure
    ring = polygon_ring(SYNTHETIC[0]["geometry"])
    assert ring[0] == ring[-1], "ring must be closed"
    assert len(ring) == 5
    print(f"  ✓ polygon_ring closed: {len(ring)} pts")
    # centroid + span
    clat, clon, sn, se = ring_centroid_bbox(ring)
    assert 55.758 < clat < 55.759
    assert sn > 0 and se > 0
    print(f"  ✓ centroid ({clat:.5f},{clon:.5f}) span {sn:.1f}×{se:.1f}m")
    # local NED
    n, e = latlon_to_local(55.7587, 37.6163, 55.7585, 37.6160)
    assert n > 0 and e > 0, f"expected +N+E, got {n},{e}"
    print(f"  ✓ latlon→local NED: N={n:.1f}m E={e:.1f}m")
    # build_scenario
    obstacles = build_scenario(SYNTHETIC, 55.7585, 37.6160, max_buildings=10)
    assert len(obstacles) == 2
    assert obstacles[0]["height_m"] == 24.0
    assert obstacles[0]["material"] == "concrete"
    assert obstacles[1]["height_m"] == 15.0
    print(f"  ✓ build_scenario: {len(obstacles)} obstacles, heights "
          f"{[o['height_m'] for o in obstacles]}")
    # emit files
    with tempfile.TemporaryDirectory() as td:
        ij = Path(td) / "test_issgr.json"
        sdf = Path(td) / "test.sdf"
        emit_issgr_json(obstacles, ij)
        emit_gazebo_sdf(obstacles, "test_world", sdf)
        # Verify ИССГР JSON valid + has geometry_polygon.
        recs = json.loads(ij.read_text())
        assert len(recs) == 2
        assert recs[0]["geometry_polygon"]["type"] == "Polygon"
        assert recs[0]["height_m"] == 24.0
        assert "geospatial_objects.building" in recs[0]["issgr_class"]
        # Verify SDF has 2 models + valid XML structure.
        sdf_txt = sdf.read_text()
        assert sdf_txt.count("<model name=") == 2
        assert "<sdf version" in sdf_txt and "</sdf>" in sdf_txt
        assert "<box><size>" in sdf_txt
        print(f"  ✓ emit_issgr_json: 2 valid Polygon obstacles")
        print(f"  ✓ emit_gazebo_sdf: 2 models, valid SDF XML")
    # Verify ИССГР JSON loads into Pydantic Obstacle model.
    sys.path.insert(0, "/home/afetz/bas-prototype/orchestrator/src")
    from orchestrator.issgr.models import Obstacle
    obj = Obstacle(**recs[0])
    assert obj.height_m == 24.0
    assert obj.geometry_polygon.type == "Polygon"
    print(f"  ✓ ИССГР JSON validates через Pydantic Obstacle model")


def part2_live() -> None:
    print("\n===== [2] live Overpass fetch (tiny bbox) =====")
    from import_osm_scenario import fetch_buildings
    bbox = (55.7585, 37.6150, 55.7600, 37.6175)
    try:
        raw = fetch_buildings(bbox, timeout=60)
    except Exception as e:
        print(f"  [skip] network/overpass unavailable: {e}")
        return
    print(f"  ✓ fetched {len(raw)} real buildings from OSM")
    if raw:
        obstacles = build_scenario(raw, 55.7592, 37.6162, max_buildings=20)
        print(f"  ✓ normalized {len(obstacles)} obstacles, "
              f"heights {min(o['height_m'] for o in obstacles):.0f}-"
              f"{max(o['height_m'] for o in obstacles):.0f}m")
        assert len(obstacles) > 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    args = p.parse_args()
    part1_offline()
    if args.live:
        part2_live()
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
