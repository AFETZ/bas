#!/usr/bin/env python3
"""Synthetic end-to-end Sionna drive: имитирует flight events.jsonl с UAV-
trajectory, который проходит через зоны препятствий iris_runway scene.
Publisher следует за этими event'ами и обновляет /tmp/sionna_channel.json.

Используется как defensible demo полного pipeline'а:
  Synthetic trajectory → events.jsonl → publisher → JSON → ns-3 → channel_updated

Без зависимости от SITL/Gazebo (которые на WSL2 имеют race-condition в FDM).
Главная цель — показать что **в реалистичной trajectory UAV ns-3 действительно
получает разные channel params в зависимости от позиции**.

Запуск:
  ./sionna_env/bin/python scripts/sionna_synthetic_drive.py \
      --out-dir logs/sionna_synth_<id> \
      --trajectory s_meander \
      --duration 60
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

# Origin как у iris_runway scene.
ORIGIN_LAT = -35.363262
ORIGIN_LON = 149.165237


def xy_to_latlon(x_north_m: float, y_east_m: float) -> tuple[float, float]:
    """Inverse of haversine_xy_m в sionna_channel_publisher.py."""
    R = 6_371_000.0
    lat = ORIGIN_LAT + math.degrees(x_north_m / R)
    lon = ORIGIN_LON + math.degrees(y_east_m / (R * math.cos(math.radians(ORIGIN_LAT))))
    return lat, lon


def synthetic_s_meander_trajectory(duration_s: float, freq_hz: float = 10.0):
    """UAV пролёт от (-50, 0) до (650, 0) с S-meander ±60м по y.
    Yield: (wall_time, lat, lon, alt_rel_m)."""
    n = int(duration_s * freq_hz)
    start_wall = time.time()
    for i in range(n):
        t = i / freq_hz
        x = -50.0 + (700.0 * t / duration_s)
        y = 60.0 * math.sin(2 * math.pi * x / 250.0)
        alt = 30.0
        lat, lon = xy_to_latlon(x, y)
        yield start_wall + t, lat, lon, alt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, help="Куда писать events.jsonl")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="Длительность synthetic полёта, сек")
    ap.add_argument("--freq", type=float, default=10.0,
                    help="Частота flight events, Гц")
    ap.add_argument("--realtime", action="store_true",
                    help="Писать события в realtime (поддерживая freq), "
                         "иначе пишет всё сразу batch'ем")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"

    # Стартовое событие сценария.
    with events_path.open("w") as f:
        start = time.time()
        f.write(json.dumps({
            "event_type": "run_start",
            "run_id": out_dir.name,
            "scenario_id": "sionna_synthetic",
            "config_hash": "synthetic",
            "seed": 42,
            "wall_time": start,
            "sim_time": 0.0,
            "versions": {"orchestrator": "synthetic"},
        }) + "\n")
        f.flush()

        period_s = 1.0 / args.freq
        sample_idx = 0
        for wall_time, lat, lon, alt in synthetic_s_meander_trajectory(
                args.duration, args.freq):
            sim_time = sample_idx / args.freq
            sample_idx += 1
            record = {
                "event_type": "flight",
                "wall_time": wall_time,
                "sim_time": sim_time,
                "vehicle_id": "iris",
                "position": {
                    "lat": lat,
                    "lon": lon,
                    "alt_amsl_m": 584.0 + alt,
                    "alt_rel_m": alt,
                },
                "velocity_mps": {"vx": 11.7, "vy": 0.0, "vz": 0.0},
                "flight_mode": "AUTO",
                "mission_state": "flying",
            }
            f.write(json.dumps(record) + "\n")
            f.flush()
            if args.realtime:
                # Synchronize wall clock with our schedule.
                now = time.time()
                target = start + sim_time + period_s
                sleep_s = max(0.0, target - now)
                time.sleep(sleep_s)

        f.write(json.dumps({
            "event_type": "run_end",
            "run_id": out_dir.name,
            "wall_time": time.time(),
            "sim_time": args.duration,
        }) + "\n")

    print(f"events.jsonl written: {events_path} ({sample_idx} flight events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
