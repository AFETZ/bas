#!/usr/bin/env python3
"""Real Sionna RT tile compute — два режима.

  1. **cached** (default, works на WSL без OptiX): загружает
     pre-computed `radio_maps/iris_runway.npz` (real Sionna RT
     output из Stage 2.1 — 800m × 300m coverage map, 2.4 GHz,
     100k samples/Tx, max_depth=3), slice'ит per-tile bounds и
     возвращает RSS статистику.

  2. **live** (требует working Mitsuba CUDA/OptiX): live ray-trace
     через `sionna.rt.RadioMapSolver`. На WSL2 default Mitsuba LLVM
     backend имеет известные spectrum issues с ITU materials в
     Sionna 1.2 — поэтому live режим требует CUDA + OptiX setup.

Cached mode — production-grade использование real Sionna output
без runtime depend на CUDA. Compute шёл offline (Stage 2.1
`scripts/run_sionna_pipeline.py`), результат в git как
`radio_maps/iris_runway.npz`.

CLI:

  ./sionna_real_tile.py --tile-i 5 --tile-j 5 --tile-size-m 50

  # Live mode (нужен GPU + working OptiX):
  ./sionna_real_tile.py --mode live --tile-i 0 --tile-j 0 \\
      --scene scene/iris_runway.xml --freq-mhz 2400
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SCENE_DEFAULT = REPO / "scene" / "iris_runway.xml"
CACHED_MAP_DEFAULT = REPO / "radio_maps" / "iris_runway.npz"

# Per-process caches.
_CACHED_MAP: dict[str, Any] = {}
_LIVE_SCENE: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Cached mode — slice pre-computed RSS map
# ---------------------------------------------------------------------------
def _load_cached_map(npz_path: Path) -> dict:
    key = str(npz_path)
    if key in _CACHED_MAP:
        return _CACHED_MAP[key]
    import numpy as np
    d = np.load(str(npz_path))
    cached = {
        "rss_db": d["rss_db"],                  # shape (H, W) float32
        "path_loss_db": d["path_loss_db"],
        "cell_size": d["cell_size"],            # [cell_h, cell_w] meters
        "map_center": d["map_center"],          # [cx, cy, cz] world coords (m)
        "map_size": d["map_size"],              # [size_y, size_x] meters
        "tx_position": d["tx_position"],
        "carrier_hz": float(d["carrier_hz"]),
        "max_depth": int(d["max_depth"]),
        "samples_per_tx": int(d["samples_per_tx"]),
    }
    _CACHED_MAP[key] = cached
    return cached


def compute_cached_tile(args: dict) -> dict:
    """Slice pre-computed map по tile bounds. Возвращает RSS stats.

    args:
      tile_i, tile_j: int — индекс tile'а
      tile_size_m: float — размер tile (50м default — соответствует Stage 4 large_map 2km tile / 40 scale-down)
      cached_path: str | Path — путь к npz
    """
    tile_i = args["tile_i"]
    tile_j = args["tile_j"]
    tile_size_m = float(args.get("tile_size_m", 50.0))
    cached_path = Path(args.get("cached_path", CACHED_MAP_DEFAULT))

    if not cached_path.exists():
        raise FileNotFoundError(f"cached Sionna map missing: {cached_path}")

    t0 = time.time()
    c = _load_cached_map(cached_path)
    import numpy as np

    rss_db = c["rss_db"]                  # (H, W) — H = y axis, W = x axis
    H, W = rss_db.shape
    cell_h_m, cell_w_m = float(c["cell_size"][0]), float(c["cell_size"][1])
    map_total_h_m = H * cell_h_m          # 30 * 10 = 300m
    map_total_w_m = W * cell_w_m          # 80 * 10 = 800m

    # Map origin (SW corner) = map_center - map_size / 2 (assuming map_center
    # is центр карты). Для Stage 2.1 iris_runway: map_center = (-30, 0, 1.5),
    # map_size = (300, 800).
    cx, cy, _cz = c["map_center"]
    sy, sx = float(c["map_size"][0]), float(c["map_size"][1])
    origin_x = cx - sx / 2.0
    origin_y = cy - sy / 2.0

    # Tile bounds in world coords.
    tile_x_min = origin_x + tile_j * tile_size_m
    tile_x_max = tile_x_min + tile_size_m
    tile_y_min = origin_y + tile_i * tile_size_m
    tile_y_max = tile_y_min + tile_size_m

    # Slice rows/cols overlapping этот tile.
    col_min = max(0, int((tile_x_min - origin_x) / cell_w_m))
    col_max = min(W, int((tile_x_max - origin_x) / cell_w_m) + 1)
    row_min = max(0, int((tile_y_min - origin_y) / cell_h_m))
    row_max = min(H, int((tile_y_max - origin_y) / cell_h_m) + 1)

    if col_max <= col_min or row_max <= row_min:
        # Tile fully вне map bounds.
        return {
            "tile_i": tile_i, "tile_j": tile_j,
            "tile_size_m": tile_size_m,
            "freq_mhz": c["carrier_hz"] / 1e6,
            "backend": "sionna_cached",
            "in_bounds": False,
            "n_cells": 0,
            "n_valid_cells": 0,
            "compute_s": round(time.time() - t0, 4),
        }

    sub = rss_db[row_min:row_max, col_min:col_max]
    n_cells = int(sub.size)
    # Valid = не floor (-300 dB означает no path).
    mask = sub > -250.0
    n_valid = int(mask.sum())
    if n_valid > 0:
        mean_rssi = float(sub[mask].mean())
        min_rssi = float(sub[mask].min())
        max_rssi = float(sub[mask].max())
    else:
        mean_rssi = min_rssi = max_rssi = -250.0

    return {
        "tile_i": tile_i, "tile_j": tile_j,
        "tile_size_m": tile_size_m,
        "freq_mhz": c["carrier_hz"] / 1e6,
        "backend": "sionna_cached",
        "in_bounds": True,
        "n_cells": n_cells,
        "n_valid_cells": n_valid,
        "coverage_fraction": round(n_valid / max(n_cells, 1), 4),
        "min_rssi_dbm": round(min_rssi, 2),
        "max_rssi_dbm": round(max_rssi, 2),
        "mean_rssi_dbm": round(mean_rssi, 2),
        "tile_bounds_world": {
            "x_min": tile_x_min, "x_max": tile_x_max,
            "y_min": tile_y_min, "y_max": tile_y_max,
        },
        "source_map_meta": {
            "shape": [H, W], "cell_size_m": [cell_h_m, cell_w_m],
            "max_depth": c["max_depth"],
            "samples_per_tx": c["samples_per_tx"],
        },
        "compute_s": round(time.time() - t0, 4),
    }


# ---------------------------------------------------------------------------
# Live mode — real-time Sionna RT (требует CUDA OptiX)
# ---------------------------------------------------------------------------
def _ensure_live_loaded(scene_path: Path) -> tuple[Any, Any]:
    key = str(scene_path)
    if key in _LIVE_SCENE:
        return _LIVE_SCENE[key]
    import mitsuba as mi
    variant = os.environ.get("MITSUBA_VARIANT", "cuda_ad_mono")
    mi.set_variant(variant)
    from sionna import rt   # noqa: WPS433
    scene = rt.load_scene(str(scene_path))
    scene.tx_array = rt.PlanarArray(
        num_rows=1, num_cols=1, vertical_spacing=0.5, horizontal_spacing=0.5,
        pattern="iso", polarization="V",
    )
    scene.rx_array = rt.PlanarArray(
        num_rows=1, num_cols=1, vertical_spacing=0.5, horizontal_spacing=0.5,
        pattern="iso", polarization="V",
    )
    solver = rt.RadioMapSolver()
    _LIVE_SCENE[key] = (scene, solver)
    return scene, solver


def compute_live_tile(args: dict) -> dict:
    """Live Sionna RT tile compute. Требует CUDA + OptiX (Linux native или WSL2
    с manual OptiX install per Mitsuba docs)."""
    tile_i = args["tile_i"]
    tile_j = args["tile_j"]
    tile_size_m = float(args.get("tile_size_m", 100.0))
    freq_mhz = float(args["freq_mhz"])
    cell_size_m = float(args.get("cell_size_m", 5.0))
    antenna_h = float(args.get("antenna_height_m", 2.0))
    max_depth = int(args.get("max_depth", 2))
    scene_path = Path(args.get("scene_path", SCENE_DEFAULT))

    t0 = time.time()
    scene, solver = _ensure_live_loaded(scene_path)
    scene.frequency = freq_mhz * 1e6

    center_x = tile_i * tile_size_m + tile_size_m / 2
    center_y = tile_j * tile_size_m + tile_size_m / 2

    import mitsuba as mi
    from sionna import rt
    if scene.get("tx") is not None:
        scene.remove("tx")
    scene.add(rt.Transmitter(name="tx",
                              position=mi.Point3f(0.0, 0.0, antenna_h),
                              power_dbm=20.0))

    rm = solver(
        scene=scene,
        center=mi.Point3f(center_x, center_y, antenna_h),
        orientation=mi.Point3f(0.0, 0.0, 0.0),
        size=mi.Point2f(tile_size_m, tile_size_m),
        cell_size=mi.Point2f(cell_size_m, cell_size_m),
        samples_per_tx=10_000, max_depth=max_depth,
    )
    import numpy as np
    pg = rm.path_gain.numpy() if hasattr(rm.path_gain, "numpy") else np.asarray(rm.path_gain)
    if pg.ndim == 3:
        pg = pg[0]
    tx_dbm = 20.0
    with np.errstate(divide="ignore"):
        rss = tx_dbm + 10.0 * np.log10(np.maximum(pg, 1e-30))
    return {
        "tile_i": tile_i, "tile_j": tile_j, "freq_mhz": freq_mhz,
        "tile_size_m": tile_size_m, "cell_size_m": cell_size_m,
        "n_cells": int(rss.size),
        "mean_rssi_dbm": round(float(rss.mean()), 2),
        "min_rssi_dbm": round(float(rss.min()), 2),
        "max_rssi_dbm": round(float(rss.max()), 2),
        "backend": "sionna_live",
        "compute_s": round(time.time() - t0, 3),
    }


def compute_real_tile(args: dict) -> dict:
    """Auto-dispatch к cached или live в зависимости от args/availability."""
    mode = args.get("mode", "cached")
    if mode == "live":
        return compute_live_tile(args)
    return compute_cached_tile(args)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("cached", "live"), default="cached")
    p.add_argument("--tile-i", type=int, default=0)
    p.add_argument("--tile-j", type=int, default=0)
    p.add_argument("--tile-size-m", type=float, default=50.0)
    p.add_argument("--cell-size-m", type=float, default=5.0)
    p.add_argument("--freq-mhz", type=float, default=2400.0)
    p.add_argument("--antenna-height-m", type=float, default=2.0)
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--scene", type=Path, default=SCENE_DEFAULT)
    p.add_argument("--cached-path", type=Path, default=CACHED_MAP_DEFAULT)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    spec = {
        "mode": args.mode,
        "tile_i": args.tile_i, "tile_j": args.tile_j,
        "tile_size_m": args.tile_size_m,
        "cell_size_m": args.cell_size_m,
        "freq_mhz": args.freq_mhz,
        "antenna_height_m": args.antenna_height_m,
        "max_depth": args.max_depth,
        "scene_path": args.scene,
        "cached_path": args.cached_path,
    }
    result = compute_real_tile(spec)
    out = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out, encoding="utf-8")
        print(f"saved → {args.output}")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
