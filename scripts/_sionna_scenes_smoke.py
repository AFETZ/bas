#!/usr/bin/env python3
"""Sionna built-in scenes + car spawning smoke.

Part 1 (pure, no GPU): resolve_scene() для всех scene names → valid paths.
Part 2 (live, needs OptiX env via run_sionna_live.sh): Munich RT + 3 cars.

Запуск:
  # Part 1 only (no GPU):
  python scripts/_sionna_scenes_smoke.py --resolve-only
  # Full (через wrapper):
  bash scripts/run_sionna_live.sh -- python scripts/_sionna_scenes_smoke.py
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/scripts")
from sionna_real_tile import (   # noqa: E402
    resolve_scene, SIONNA_BUILTIN_SCENES, compute_real_tile,
)


def part1_resolve() -> None:
    print("===== [1] resolve_scene() для всех scene names =====")
    names = ["iris_runway"] + list(SIONNA_BUILTIN_SCENES.keys())
    for name in names:
        path, meta = resolve_scene(name)
        ok = Path(path).exists()
        tag = "builtin" if meta else "bundled"
        assert ok, f"scene path missing for {name}: {path}"
        print(f"  {name:32s} {tag:8s} OK  {Path(path).name}")
    print(f"  ✓ all {len(names)} scenes resolve to existing XML files")


def part2_live() -> None:
    print("\n===== [2] live RT: Munich + Etoile + cars =====")
    for scene_name, cars in [("munich", 0), ("munich", 3),
                              ("simple_street_canyon_with_cars", 0)]:
        path, meta = resolve_scene(scene_name)
        result = compute_real_tile({
            "mode": "live", "scene_path": path, "scene_name": scene_name,
            "builtin_meta": meta, "add_cars": cars,
            "tile_i": 0, "tile_j": 0, "tile_size_m": 50.0,
            "cell_size_m": 25.0, "freq_mhz": 2400.0,
            "antenna_height_m": 2.0, "max_depth": 2,
        })
        print(f"  {scene_name:32s} cars={cars}  "
              f"valid={result['n_valid_cells']}/{result['n_cells']}  "
              f"mean_rssi={result['mean_rssi_dbm']} dBm  "
              f"t={result['compute_s']}s")
        assert result["backend"] == "sionna_live"
        assert result["n_cells"] > 0
        assert result["n_cars"] == cars
        # Munich/canyon должны иметь хоть какое-то покрытие.
        assert result["n_valid_cells"] > 0, f"no coverage for {scene_name}"
    print("  ✓ live RT works on built-in scenes with car spawning")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--resolve-only", action="store_true")
    args = p.parse_args()

    part1_resolve()
    if not args.resolve_only:
        part2_live()
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
