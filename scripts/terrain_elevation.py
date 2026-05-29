#!/usr/bin/env python3
"""Real terrain elevation from AWS Terrain Tiles (keyless, no account).

Тянет global SRTM-derived elevation через AWS public S3 "terrarium" tiles
(https://registry.opendata.aws/terrain-tiles/) — keyless, без API-токена.
Декодирует RGB→высоту, стичит tile-grid под bbox, отдаёт elevation grid +
интерполятор `elevation_at(lat, lon)`.

Закрывает ограничение large_map «flat-earth ±1м»: даёт реальную высоту
рельефа для scenario origin, building bases, и Sionna ground level.

Terrarium decode (per pixel):
  elevation_m = (R * 256 + G + B / 256) - 32768

Self-contained: stdlib urllib + Pillow + numpy (всё уже в .venv).

CLI:
  ./terrain_elevation.py --bbox 55.74,37.60,55.78,37.64 --zoom 12 \\
      --out generated/moscow/terrain.npz
"""
from __future__ import annotations

import argparse
import io
import math
import sys
import urllib.request
from pathlib import Path
from typing import Any

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
USER_AGENT = "bas-prototype-terrain/1.0"
TILE_PX = 256


def _deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    """lat/lon/zoom → slippy-map tile (xtile, ytile)."""
    n = 2 ** z
    xt = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    yt = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return xt, yt


def _tile_lon_lat(x: int, y: int, z: int) -> tuple[float, float]:
    """Tile NW corner → (lon, lat)."""
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def _fetch_tile(x: int, y: int, z: int, timeout: float = 15.0) -> "Any":
    """Fetch + decode one terrarium tile → numpy elevation array (256×256)."""
    import numpy as np
    from PIL import Image
    url = TERRARIUM_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    arr = np.asarray(img, dtype=np.float64)
    # elevation = (R*256 + G + B/256) - 32768
    elev = (arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0) - 32768.0
    return elev


def fetch_elevation_grid(bbox: tuple[float, float, float, float],
                         zoom: int = 12) -> dict:
    """Fetch + stitch terrarium tiles covering bbox → elevation grid.

    bbox = (south, west, north, east).
    Returns dict с numpy 'elevation' (rows=lat desc, cols=lon asc),
    geotransform (lat/lon per pixel), stats.
    """
    import numpy as np
    south, west, north, east = bbox
    x0, y0 = _deg2tile(north, west, zoom)    # NW tile
    x1, y1 = _deg2tile(south, east, zoom)    # SE tile
    nx = x1 - x0 + 1
    ny = y1 - y0 + 1
    if nx * ny > 64:
        raise ValueError(f"bbox too large for zoom {zoom}: {nx}×{ny} tiles "
                         f"(max 64) — lower zoom или smaller bbox")

    rows = []
    for ty in range(y0, y1 + 1):
        cols = [_fetch_tile(tx, ty, zoom) for tx in range(x0, x1 + 1)]
        rows.append(np.hstack(cols))
    full = np.vstack(rows)   # (ny*256, nx*256)

    # Geo extent of stitched mosaic (NW corner of x0,y0 → SE corner of x1,y1).
    lon_nw, lat_nw = _tile_lon_lat(x0, y0, zoom)
    lon_se, lat_se = _tile_lon_lat(x1 + 1, y1 + 1, zoom)
    H, W = full.shape
    lat_per_px = (lat_se - lat_nw) / H        # negative (lat decreases down)
    lon_per_px = (lon_se - lon_nw) / W        # positive

    return {
        "elevation": full,
        "lat_nw": lat_nw, "lon_nw": lon_nw,
        "lat_per_px": lat_per_px, "lon_per_px": lon_per_px,
        "zoom": zoom, "bbox": bbox,
        "n_tiles": nx * ny,
    }


def elevation_at(grid: dict, lat: float, lon: float) -> float:
    """Bilinear-ish nearest elevation at lat/lon from grid."""
    col = (lon - grid["lon_nw"]) / grid["lon_per_px"]
    row = (lat - grid["lat_nw"]) / grid["lat_per_px"]
    arr = grid["elevation"]
    H, W = arr.shape
    r = max(0, min(H - 1, int(round(row))))
    c = max(0, min(W - 1, int(round(col))))
    return float(arr[r, c])


def grid_stats(grid: dict) -> dict:
    import numpy as np
    arr = grid["elevation"]
    return {
        "min_m": round(float(np.min(arr)), 1),
        "max_m": round(float(np.max(arr)), 1),
        "mean_m": round(float(np.mean(arr)), 1),
        "relief_m": round(float(np.max(arr) - np.min(arr)), 1),
        "shape": list(arr.shape),
        "n_tiles": grid["n_tiles"],
        "zoom": grid["zoom"],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bbox", required=True, help="south,west,north,east")
    p.add_argument("--zoom", type=int, default=12, help="tile zoom (10-13 typical)")
    p.add_argument("--out", type=Path, help="save .npz elevation grid")
    p.add_argument("--at", help="query elevation at lat,lon")
    args = p.parse_args()

    bbox = tuple(float(x) for x in args.bbox.split(","))
    print(f"==> fetching terrain z={args.zoom} bbox={bbox}")
    grid = fetch_elevation_grid(bbox, args.zoom)
    s = grid_stats(grid)
    print(f"    {s['n_tiles']} tiles → grid {s['shape']}")
    print(f"    elevation: {s['min_m']}–{s['max_m']}m  mean {s['mean_m']}m  "
          f"relief {s['relief_m']}m")

    if args.at:
        lat, lon = (float(x) for x in args.at.split(","))
        print(f"    elevation_at({lat},{lon}) = {elevation_at(grid, lat, lon):.1f}m")

    if args.out:
        import numpy as np
        args.out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            args.out, elevation=grid["elevation"].astype("float32"),
            lat_nw=grid["lat_nw"], lon_nw=grid["lon_nw"],
            lat_per_px=grid["lat_per_px"], lon_per_px=grid["lon_per_px"],
            zoom=grid["zoom"],
        )
        print(f"    saved → {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
