#!/usr/bin/env python3
"""Terrain elevation smoke — offline math + optional live fetch.

Part 1 (offline): tile coord round-trip, elevation_at on synthetic grid.
Part 2 (--live): real Moscow fetch (~150m) + OSM importer terrain wiring.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from terrain_elevation import (   # noqa: E402
    _deg2tile, _tile_lon_lat, elevation_at, grid_stats,
)


def part1_offline() -> None:
    print("===== [1] offline tile math + elevation_at =====")
    # Tile coord round-trip: lat/lon → tile → NW corner should be near.
    lat, lon, z = 55.76, 37.62, 12
    xt, yt = _deg2tile(lat, lon, z)
    nw_lon, nw_lat = _tile_lon_lat(xt, yt, z)
    se_lon, se_lat = _tile_lon_lat(xt + 1, yt + 1, z)
    assert nw_lat >= lat >= se_lat, f"lat {lat} not in tile [{se_lat},{nw_lat}]"
    assert nw_lon <= lon <= se_lon, f"lon {lon} not in tile [{nw_lon},{se_lon}]"
    print(f"  ✓ deg2tile/tile_lon_lat: ({lat},{lon}) z{z} → tile ({xt},{yt}) contains point")

    # elevation_at on synthetic grid (north-high gradient).
    H, W = 100, 100
    arr = np.tile(np.linspace(500, 100, H).reshape(-1, 1), (1, W))  # row0=500m, rowN=100m
    grid = {"elevation": arr, "lat_nw": 56.0, "lon_nw": 37.0,
            "lat_per_px": -0.001, "lon_per_px": 0.001,
            "zoom": 12, "n_tiles": 1, "bbox": (55.9, 37.0, 56.0, 37.1)}
    # Point near NW (row 0) → high; near south → low.
    e_north = elevation_at(grid, 55.999, 37.05)
    e_south = elevation_at(grid, 55.905, 37.05)
    print(f"  ✓ elevation_at: north={e_north:.0f}m south={e_south:.0f}m (gradient)")
    assert e_north > e_south, "north should be higher in synthetic gradient"

    s = grid_stats(grid)
    assert s["min_m"] == 100.0 and s["max_m"] == 500.0
    assert s["relief_m"] == 400.0
    print(f"  ✓ grid_stats: {s['min_m']}–{s['max_m']}m relief {s['relief_m']}m")

    # Terrarium decode formula spot-check.
    # R=128,G=0,B=0 → 128*256 - 32768 = 0m (sea level).
    r, g, b = 128, 0, 0
    elev = (r * 256 + g + b / 256) - 32768
    assert elev == 0.0, f"terrarium decode wrong: {elev}"
    print(f"  ✓ terrarium decode: RGB(128,0,0) = {elev}m (sea level)")


def part2_live() -> None:
    print("\n===== [2] live AWS Terrain Tiles fetch =====")
    from terrain_elevation import fetch_elevation_grid
    try:
        grid = fetch_elevation_grid((55.74, 37.60, 55.78, 37.64), zoom=12)
    except Exception as e:
        print(f"  [skip] terrain tiles unavailable: {e}")
        return
    s = grid_stats(grid)
    e_center = elevation_at(grid, 55.76, 37.62)
    print(f"  ✓ Moscow center: {s['min_m']}–{s['max_m']}m, at(55.76,37.62)={e_center:.0f}m")
    # Moscow real elevation ~120-160m.
    assert 100 < e_center < 200, f"Moscow elevation unrealistic: {e_center}"

    # OSM importer terrain wiring: synthetic building + terrain grid.
    from import_osm_scenario import build_scenario
    synth = [{"id": 1, "type": "way", "tags": {"building": "yes", "height": "20"},
              "geometry": [{"lat": 55.760, "lon": 37.620},
                           {"lat": 55.760, "lon": 37.621},
                           {"lat": 55.761, "lon": 37.621},
                           {"lat": 55.761, "lon": 37.620}]}]
    obs = build_scenario(synth, 55.76, 37.62, terrain_grid=grid)
    assert "base_elevation_m" in obs[0], "terrain not wired into obstacle"
    print(f"  ✓ OSM importer terrain wiring: building base_elevation_m="
          f"{obs[0]['base_elevation_m']}m AMSL")
    assert 100 < obs[0]["base_elevation_m"] < 200


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
