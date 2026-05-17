#!/usr/bin/env python3
"""Этап 2.1.e: real-time Sionna channel publisher.

Читает текущую позицию UAV из `events.jsonl` (tail -f аналог), делает
2D lookup в pre-computed radio map (.npz, см. compute_radio_map.py) и
публикует текущие `loss_ratio` + `delay_ms` в JSON-файл, который ns-3
читает каждые 100 мс через FileWatcher (см. ns3/scenarios/two_channel.cc).

Это минимальная интеграция — не требует кастомного `SionnaErrorModel` в
C++ и nzp-loader'а; вместо этого orchestrator делает физику в Python и
обновляет существующий `RateErrorModel::SetRate()` через файл-IPC.

Запуск (обычно из run_stage_2_1_sionna.sh):
  ./sionna_env/bin/python scripts/sionna_channel_publisher.py \
      --events logs/<run_id>/events.jsonl \
      --radio-map radio_maps/iris_runway.npz \
      --out /tmp/sionna_channel.json \
      --interval-ms 100
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np  # type: ignore


# Origin для маппинга lat/lon -> метры. iris в Gazebo стартует в этой точке,
# config совпадает с iris_runway.sdf <spherical_coordinates>.
ORIGIN_LAT = -35.363262
ORIGIN_LON = 149.165237


def haversine_xy_m(lat: float, lon: float) -> tuple[float, float]:
    """Local-tangent-plane conversion (lat,lon) -> (x_north, y_east) в метрах
    относительно ORIGIN_LAT/LON. Точность ±1м в радиусе нескольких км —
    достаточно для radio map с 10 м cell-size."""
    R = 6_371_000.0
    dlat = math.radians(lat - ORIGIN_LAT)
    dlon = math.radians(lon - ORIGIN_LON)
    x_north = R * dlat
    y_east = R * dlon * math.cos(math.radians(ORIGIN_LAT))
    return x_north, y_east


class RadioMap:
    """Wrapper над .npz: 2D bilinear lookup path_loss / RSS по (x, y)."""

    def __init__(self, npz_path: Path) -> None:
        data = np.load(npz_path)
        self.rss_db = data["rss_db"]              # (n_y, n_x), dB
        self.path_loss_db = data["path_loss_db"]
        cx, cy, _ = data["map_center"]
        sx, sy = data["map_size"]
        cell_x, cell_y = data["cell_size"]
        self.x_min = float(cx - sx / 2)
        self.x_max = float(cx + sx / 2)
        self.y_min = float(cy - sy / 2)
        self.y_max = float(cy + sy / 2)
        self.cell_x = float(cell_x)
        self.cell_y = float(cell_y)
        self.tx_pos = data["tx_position"]
        self.carrier_hz = float(data["carrier_hz"])
        print(f"RadioMap loaded: {self.rss_db.shape}, "
              f"x∈[{self.x_min:.0f},{self.x_max:.0f}] "
              f"y∈[{self.y_min:.0f},{self.y_max:.0f}] "
              f"carrier={self.carrier_hz/1e9:.2f} GHz")

    def lookup(self, x: float, y: float) -> tuple[float, float]:
        """Возвращает (rss_db, path_loss_db) для позиции (x, y) в метрах.

        За пределами карты возвращает path_loss = 130 dB (полный blackout),
        rss = -130 dB. В рамках карты делает bilinear interpolation.
        """
        if not (self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max):
            return (-130.0, 130.0)

        fx = (x - self.x_min) / self.cell_x
        fy = (y - self.y_min) / self.cell_y
        ix = int(np.floor(fx))
        iy = int(np.floor(fy))
        # bilinear weights
        dx = fx - ix
        dy = fy - iy
        ix = max(0, min(self.rss_db.shape[1] - 2, ix))
        iy = max(0, min(self.rss_db.shape[0] - 2, iy))
        rss = (
            (1 - dx) * (1 - dy) * self.rss_db[iy,   ix]
            + dx * (1 - dy)       * self.rss_db[iy,   ix+1]
            + (1 - dx) * dy       * self.rss_db[iy+1, ix]
            + dx * dy             * self.rss_db[iy+1, ix+1]
        )
        return float(rss), float(-rss)  # path_loss = -RSS (dB-domain)


def rss_to_loss_ratio(rss_db: float) -> float:
    """Эвристическое отображение RSS dB -> packet error ratio для WiFi 2.4 GHz.

    Используем простую logistic: при rss > -65 dB loss ≈ 0; при rss < -90 dB
    loss → 1; в середине плавный переход. Параметры подобраны чтобы
    coverage = 65% radio map (как в smoke run) давал ~5-15% средний loss.
    """
    # logistic centered at -78 dB, slope = 0.5
    return 1.0 / (1.0 + math.exp(0.5 * (rss_db + 78.0)))


def rss_to_extra_delay_ms(rss_db: float) -> float:
    """Чем хуже сигнал, тем больше delay (retransmits, queueing).
    Простая модель: 0 ms при RSS > -60 dB, до +200 ms при RSS < -90 dB."""
    if rss_db >= -60.0:
        return 0.0
    if rss_db <= -90.0:
        return 200.0
    return 200.0 * (-60.0 - rss_db) / 30.0


def tail_flight_positions(events_path: Path, from_start: bool = False):
    """Generator: yield последнюю UAV-позицию (lat, lon, alt_rel_m) из
    events.jsonl. Делает tail -f аналог: при появлении новых строк
    парсит `event_type=flight` и `position.{lat,lon,alt_rel_m}`.

    `from_start=True` — читать journal с начала (полезно для replay/smoke
    на завершённых прогонах).
    """
    fp = None
    while True:
        if not events_path.exists():
            time.sleep(0.1)
            continue
        if fp is None:
            fp = events_path.open("r", encoding="utf-8")
            if not from_start:
                fp.seek(0, os.SEEK_END)   # real-time: только новые события
        line = fp.readline()
        if not line:
            time.sleep(0.05)
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("event_type") != "flight":
            continue
        pos = ev.get("position", {})
        if "lat" not in pos:
            continue
        yield (
            float(pos["lat"]),
            float(pos["lon"]),
            float(pos.get("alt_rel_m", 0.0)),
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, help="Путь к events.jsonl")
    ap.add_argument("--radio-map", required=True, help="Путь к .npz")
    ap.add_argument("--out", default="/tmp/sionna_channel.json")
    ap.add_argument("--interval-ms", type=int, default=100)
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="Auto-stop после N секунд (0 = forever)")
    ap.add_argument("--replay", action="store_true",
                    help="Прочитать events.jsonl с начала (smoke на завершённых прогонах)")
    args = ap.parse_args()

    rm = RadioMap(Path(args.radio_map))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    last_write = 0.0
    interval_s = args.interval_ms / 1000.0
    last_pos = None

    print(f"==> publishing to {out_path} every {args.interval_ms} ms "
          f"(reading {args.events})")

    for lat, lon, alt in tail_flight_positions(Path(args.events), from_start=args.replay):
        last_pos = (lat, lon, alt)
        now = time.time()
        if now - last_write < interval_s:
            continue
        last_write = now

        x, y = haversine_xy_m(lat, lon)
        rss_db, path_loss_db = rm.lookup(x, y)
        loss_ratio = rss_to_loss_ratio(rss_db)
        extra_delay_ms = rss_to_extra_delay_ms(rss_db)

        record = {
            "wall_time": now,
            "wall_dt": now - start,
            "uav_lat": lat,
            "uav_lon": lon,
            "uav_alt_rel_m": alt,
            "uav_x_north": x,
            "uav_y_east": y,
            "rss_db": rss_db,
            "path_loss_db": path_loss_db,
            "loss_ratio": loss_ratio,
            "extra_delay_ms": extra_delay_ms,
        }
        tmp_path = out_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(record, separators=(",", ":")))
        tmp_path.replace(out_path)   # atomic rename

        if args.max_seconds and (now - start) > args.max_seconds:
            print("max-seconds reached")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
