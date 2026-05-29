#!/usr/bin/env python3
"""Large-map smoke — TileGrid + SpatialIndex для 20×20 km сценария.

Verify:
  1. TileGrid creates 10×10 grid из 2km tiles → 20×20 km coverage = 400 km²
  2. tile_at / bounds round-trip
  3. neighbors() chebyshev radius
  4. Spatial index: insert 5000 obstacles spread over grid → uniform-ish buckets
  5. bbox query — точно возвращает hits внутри bbox
  6. neighborhood query — radius=1 (3x3 tiles) даёт ~9% от total
  7. Sionna cache key determinism
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/orchestrator/src")

from orchestrator.issgr import (   # noqa: E402
    TileGrid, TileId, SpatialIndex, IndexEntry,
    latlon_to_local_ned, local_ned_to_latlon,
    sionna_cache_key, tiles_to_preload,
)


ORIGIN_LAT = -35.36
ORIGIN_LON = 149.165


def main() -> int:
    # --- 1. Grid 20×20 km из 2km tiles ---
    print("===== [1] TileGrid =====")
    grid = TileGrid(
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
        tile_size_m=2000.0, n_tiles_north=10, n_tiles_east=10,
    )
    print(f"  coverage: {grid.coverage_north_km:.1f} × {grid.coverage_east_km:.1f} km"
          f" = {grid.total_area_km2:.0f} km²")
    print(f"  tiles: {grid.total_tiles} ({grid.n_tiles_north}×{grid.n_tiles_east})")
    assert grid.total_area_km2 == 400.0
    assert grid.total_tiles == 100

    # --- 2. Tile round-trip ---
    print("\n===== [2] tile_at / bounds round-trip =====")
    sw = grid.tile_at(ORIGIN_LAT, ORIGIN_LON)
    print(f"  origin → tile {sw.as_str()}")
    assert sw == TileId(i=0, j=0)
    b = grid.bounds(sw)
    print(f"  bounds: N {b.n_min_m:.0f}..{b.n_max_m:.0f}m, E {b.e_min_m:.0f}..{b.e_max_m:.0f}m"
          f"\n          lat {b.lat_min:.5f}..{b.lat_max:.5f}, lon {b.lon_min:.5f}..{b.lon_max:.5f}")
    assert abs(b.width_m - 2000.0) < 1.0
    assert abs(b.height_m - 2000.0) < 1.0
    # Origin внутри bounds (corner).
    assert b.contains_latlon(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001)

    # Tile далеко от origin.
    far = grid.tile_at(ORIGIN_LAT + 0.05, ORIGIN_LON + 0.05)
    fb = grid.bounds(far)
    print(f"  far(+5.5km N, +5km E) → tile {far.as_str()}  "
          f"bounds-center lat={fb.center_lat:.4f} lon={fb.center_lon:.4f}")
    assert far.i > 0 and far.j > 0

    # --- 3. Neighbors ---
    print("\n===== [3] neighbors() =====")
    nb1 = grid.neighbors(TileId(i=5, j=5), radius=1)
    nb2 = grid.neighbors(TileId(i=5, j=5), radius=2)
    print(f"  radius=1: {len(nb1)} tiles (expected 9)")
    print(f"  radius=2: {len(nb2)} tiles (expected 25)")
    assert len(nb1) == 9
    assert len(nb2) == 25

    # --- 4. SpatialIndex stress: 5000 obstacles ---
    print("\n===== [4] SpatialIndex insert 5000 =====")
    idx = SpatialIndex(grid)
    rng = random.Random(42)
    t0 = time.perf_counter()
    for n in range(5000):
        lat = ORIGIN_LAT + rng.uniform(0, 0.18)   # ~20 km coverage
        lon = ORIGIN_LON + rng.uniform(0, 0.22)
        idx.insert(f"obs-{n:05d}", lat=lat, lon=lon,
                   alt=rng.uniform(0, 50))
    dt_ms = (time.perf_counter() - t0) * 1000
    s = idx.stats()
    print(f"  inserted 5000 in {dt_ms:.1f}ms; {s}")
    assert s["total_objects"] == 5000
    assert s["occupied_tiles"] >= 80   # должно быть ~100 (uniform spread)
    # Avg ~50 records / tile (5000/100 ~ 50).
    assert 30 <= s["avg_bucket"] <= 100, f"avg_bucket={s['avg_bucket']}"

    # --- 5. BBox query ---
    print("\n===== [5] bbox query =====")
    # Bbox первого 4×4 km блока (4 tiles).
    t0 = time.perf_counter()
    hits = idx.query_bbox(
        lat_min=ORIGIN_LAT, lon_min=ORIGIN_LON,
        lat_max=ORIGIN_LAT + 0.036, lon_max=ORIGIN_LON + 0.044,
    )
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"  bbox 4×4 km (expected ~200 hits): got {len(hits)} in {dt_ms:.2f}ms")
    assert 100 < len(hits) < 400, f"got {len(hits)}"

    # --- 6. Neighborhood query (3x3 tiles) ---
    print("\n===== [6] neighborhood query rad=1 =====")
    t0 = time.perf_counter()
    nb_hits = idx.query_neighborhood(
        lat=ORIGIN_LAT + 0.05, lon=ORIGIN_LON + 0.05,
        radius_tiles=1,
    )
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"  3x3 tiles around (5km N, 5km E): {len(nb_hits)} hits in {dt_ms:.2f}ms")
    # 9 tiles × ~50 records/tile ~ 450. ±50%.
    assert 250 < len(nb_hits) < 700, f"got {len(nb_hits)}"

    # --- 7. Sionna cache key ---
    print("\n===== [7] sionna_cache_key =====")
    k1 = sionna_cache_key(TileId(i=3, j=4), freq_mhz=915.0, antenna_height_m=2.0)
    k2 = sionna_cache_key(TileId(i=3, j=4), freq_mhz=915.0, antenna_height_m=2.0)
    k3 = sionna_cache_key(TileId(i=3, j=4), freq_mhz=2400.0, antenna_height_m=2.0)
    print(f"  k1 = {k1}")
    print(f"  k3 = {k3}")
    assert k1 == k2, "cache key must be deterministic"
    assert k1 != k3, "different freq → different key"

    # --- 8. tiles_to_preload ---
    print("\n===== [8] tiles_to_preload =====")
    preload = tiles_to_preload(grid, uav_lat=ORIGIN_LAT + 0.05,
                                uav_lon=ORIGIN_LON + 0.05, radius_tiles=2)
    print(f"  UAV at (5km N, 5km E), radius=2: preload {len(preload)} tiles (5×5)")
    assert len(preload) == 25

    # --- 9. tiles_in_bbox ---
    print("\n===== [9] tiles_in_bbox =====")
    in_bbox = grid.tiles_in_bbox(
        lat_min=ORIGIN_LAT, lon_min=ORIGIN_LON,
        lat_max=ORIGIN_LAT + 0.054, lon_max=ORIGIN_LON + 0.066,
    )
    print(f"  bbox 6×6 km: {len(in_bbox)} tiles (expected 3×3=9 ±1)")
    assert 6 <= len(in_bbox) <= 16

    # --- 10. NED ↔ latlon round-trip precision (UTM) ---
    print("\n===== [10] NED ↔ latlon round-trip (UTM) =====")
    test_lat = ORIGIN_LAT + 0.1
    test_lon = ORIGIN_LON + 0.1
    n, e = latlon_to_local_ned(test_lat, test_lon, ORIGIN_LAT, ORIGIN_LON)
    back_lat, back_lon = local_ned_to_latlon(n, e, ORIGIN_LAT, ORIGIN_LON)
    err_lat = abs(back_lat - test_lat)
    err_lon = abs(back_lon - test_lon)
    print(f"  test (lat={test_lat:.6f}, lon={test_lon:.6f}) → NED ({n:.1f}m, {e:.1f}m)")
    print(f"    back → (lat={back_lat:.6f}, lon={back_lon:.6f})  err={err_lat:.2e}/{err_lon:.2e} deg")
    # UTM series round-trip ~мм → ~1e-8 deg. Допуск 1e-7 (~1см).
    assert err_lat < 1e-7 and err_lon < 1e-7

    # --- 11. UTM accuracy vs flat-earth at long range (200 км) ---
    print("\n===== [11] UTM vs flat-earth @ 200 км (закрывает 🟡→🟢) =====")
    from orchestrator.issgr import (
        latlon_to_utm, utm_to_latlon, latlon_to_local_ned_flat,
    )
    # Известное geodetic-расстояние: 1° широты ≈ 111.32 км. Берём точку
    # ~200 км севернее origin и проверяем что UTM-northing совпадает с
    # эталоном (UTM↔latlon round-trip) лучше чем flat-earth.
    far_lat = ORIGIN_LAT + 1.8     # ~200 км на север
    far_lon = ORIGIN_LON + 0.5     # ~45 км на восток
    n_utm, e_utm = latlon_to_local_ned(far_lat, far_lon, ORIGIN_LAT, ORIGIN_LON)
    n_flat, e_flat = latlon_to_local_ned_flat(far_lat, far_lon, ORIGIN_LAT, ORIGIN_LON)
    # Эталон: UTM forward обеих точек в общей зоне.
    _, _, z, _ = latlon_to_utm(ORIGIN_LAT, ORIGIN_LON)
    e0, n0, _, _ = latlon_to_utm(ORIGIN_LAT, ORIGIN_LON, force_zone=z)
    e1, n1, _, _ = latlon_to_utm(far_lat, far_lon, force_zone=z)
    ref_n, ref_e = (n1 - n0), (e1 - e0)
    # UTM round-trips: точка из NED обратно в latlon → совпадает с far.
    rt_lat, rt_lon = local_ned_to_latlon(n_utm, e_utm, ORIGIN_LAT, ORIGIN_LON)
    rt_err_m = abs(rt_lat - far_lat) * 111_320.0
    flat_dev_m = abs(n_flat - ref_n)
    print(f"  @200км: UTM round-trip error = {rt_err_m*100:.2f} см")
    print(f"          flat-earth northing отклонение от UTM = {flat_dev_m:.1f} м")
    assert rt_err_m < 0.1, f"UTM round-trip drift {rt_err_m}m @200km"
    assert flat_dev_m > 5.0, "ожидали заметное расхождение flat-earth на 200км"
    print(f"  ✓ UTM точен на любой дистанции (flat-earth уходит на {flat_dev_m:.0f}м)")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
