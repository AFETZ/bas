#!/usr/bin/env python3
"""ИССГР Бортовая часть — demo.

Запускается standalone:
  1. Создаёт persistent SQLite БД (bas_onboard.db).
  2. Слушает orchestrator events.jsonl (опционально через --events-file).
  3. Каждый received `uav_state` / `cv_detection` / `rssi_sample` сохраняется
     в on-board БД.
  4. В фоне работает composite engine, считая derived metrics каждую секунду.
  5. Каждые 5 секунд печатает stats + последний CompositeMetrics в stdout.

Real BAS deployment: этот процесс запускается на companion-computer'е
рядом с автопилотом, держит persistent storage даже после reboot UAV.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/orchestrator/src")

from orchestrator.issgr import (   # noqa: E402
    UAV, Pose, ObjectIdentifier, SensorReading, IssgrClass,
    OnBoardDB,
)


def _uav_from_event(ev: dict) -> UAV | None:
    """Перевод orchestrator event → UAV pydantic-объект."""
    try:
        d = ev.get("data", {})
        sysid = int(d.get("sysid", 1))
        return UAV(
            id=ObjectIdentifier(
                domain="bas", system="onboard-demo",
                object_uuid=uuid.UUID(
                    f"00000000-0000-0000-0000-{sysid:012x}"),
            ),
            name=d.get("name", f"sysid{sysid}"),
            sysid=sysid,
            pose=Pose(
                latitude_deg=float(d.get("lat", d.get("latitude_deg", 0.0))),
                longitude_deg=float(d.get("lon", d.get("longitude_deg", 0.0))),
                altitude_m=float(d.get("alt", d.get("altitude_m", 0.0))),
                heading_deg=d.get("heading_deg") or d.get("yaw_deg"),
            ),
            armed=bool(d.get("armed", False)),
            flight_mode=d.get("flight_mode", "UNKNOWN"),
            battery_v=d.get("battery_v"),
            velocity_ned=d.get("velocity_ned"),
        )
    except (ValueError, KeyError, TypeError):
        return None


def _sensor_from_event(ev: dict, default_source_uav: uuid.UUID) -> SensorReading | None:
    try:
        d = ev.get("data", {})
        return SensorReading(
            id=ObjectIdentifier(domain="bas", system="onboard-demo"),
            name=ev.get("event", "sensor"),
            issgr_class=IssgrClass.FUNC_SENSOR_STATION,
            source_uav_id=ObjectIdentifier(
                domain="bas", system="onboard-demo",
                object_uuid=default_source_uav,
            ),
            sensor_type=ev.get("event", "unknown"),
            value=d if isinstance(d, dict) else {"raw": str(d)},
        )
    except (ValueError, KeyError, TypeError):
        return None


def follow(events_path: Path, db: OnBoardDB, sysid: int) -> None:
    """tail -f events.jsonl: routing → OnBoardDB."""
    default_src = uuid.UUID(f"00000000-0000-0000-0000-{sysid:012x}")
    with events_path.open("r", encoding="utf-8") as fh:
        fh.seek(0, 2)   # to end
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.2)
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("event", "")
            if etype in {"uav_state", "telemetry", "pose"}:
                uav = _uav_from_event(ev)
                if uav:
                    db.append_uav_state(uav)
            elif etype in {"cv_detection", "rssi_sample",
                           "lidar_distance", "camera_object_detection",
                           "rssi_lora", "rssi_wifi"}:
                sr = _sensor_from_event(ev, default_src)
                if sr:
                    sr.sensor_type = etype
                    db.append_sensor_reading(sr)


def _synth_loop(db: OnBoardDB, sysid: int, hz: float) -> None:
    """Если нет events.jsonl — генерируем synthetic data для демо."""
    import math
    uav_uuid = uuid.UUID(f"00000000-0000-0000-0000-{sysid:012x}")
    t0 = time.time()
    print(f"[demo] no events file — генерируем synthetic data @ {hz}Hz")
    interval = 1.0 / hz
    while True:
        t = time.time() - t0
        # Synthetic orbit (radius 50м around CMAC).
        lat = -35.363262 + 0.0005 * math.cos(t * 0.05)
        lon = 149.165237 + 0.0005 * math.sin(t * 0.05)
        alt = 15.0 + 2.0 * math.sin(t * 0.1)
        bv = max(10.5, 12.6 - t * 0.001)   # slow drain
        uav = UAV(
            id=ObjectIdentifier(object_uuid=uav_uuid),
            name=f"Iris-{sysid}",
            sysid=sysid,
            pose=Pose(latitude_deg=lat, longitude_deg=lon,
                      altitude_m=alt, heading_deg=(t * 5) % 360),
            armed=True, flight_mode="GUIDED",
            battery_v=bv,
            velocity_ned=[2.5, 0.5, 0.0],
        )
        db.append_uav_state(uav)

        # Каждые 3-я итерация — RSSI sample.
        if int(t * hz) % 3 == 0:
            rssi = -65 - 10 * math.sin(t * 0.2)
            sr = SensorReading(
                id=ObjectIdentifier(),
                name="RF:RSSI",
                source_uav_id=ObjectIdentifier(object_uuid=uav_uuid),
                sensor_type="rssi_lora",
                value={"rssi_dbm": rssi, "confidence": rssi},
            )
            db.append_sensor_reading(sr)

        # Каждая 7-я — CV detection.
        if int(t * hz) % 7 == 0:
            cls = ["person", "car", "person", "tree"][int(t) % 4]
            sr = SensorReading(
                id=ObjectIdentifier(),
                name=f"CV:{cls}",
                source_uav_id=ObjectIdentifier(object_uuid=uav_uuid),
                sensor_type="camera_object_detection",
                value={"class_name": cls, "confidence": 0.85,
                       "ground_lat": lat - 0.0001, "ground_lon": lon + 0.0001},
            )
            db.append_sensor_reading(sr)

        time.sleep(interval)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default="bas_onboard.db",
                   help="Path к SQLite файлу (default: ./bas_onboard.db)")
    p.add_argument("--retention-seconds", type=int, default=3600)
    p.add_argument("--composite-interval", type=float, default=1.0)
    p.add_argument("--events-file", type=Path,
                   help="orchestrator events.jsonl — tail -f")
    p.add_argument("--sysid", type=int, default=1)
    p.add_argument("--synth-hz", type=float, default=5.0,
                   help="Synthetic data rate если нет events file")
    p.add_argument("--max-seconds", type=int, default=0,
                   help="0 = бесконечно")
    args = p.parse_args()

    db_path = Path(args.db_path).resolve()
    print(f"==> OnBoardDB: {db_path}")
    print(f"    retention={args.retention_seconds}s   composite={args.composite_interval}s")

    db = OnBoardDB(
        path=db_path,
        retention_seconds=args.retention_seconds,
        composite_interval_s=args.composite_interval,
    )
    db.start_composite_engine(sysids=[args.sysid])

    stop = [False]
    def _on_sig(*_):
        stop[0] = True
    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    # Producer thread.
    import threading
    if args.events_file and args.events_file.exists():
        print(f"==> следим за events: {args.events_file}")
        t = threading.Thread(
            target=follow, args=(args.events_file, db, args.sysid),
            daemon=True)
    else:
        t = threading.Thread(
            target=_synth_loop, args=(db, args.sysid, args.synth_hz),
            daemon=True)
    t.start()

    # Print loop.
    t_start = time.time()
    last_print = 0.0
    try:
        while not stop[0]:
            if args.max_seconds > 0 and time.time() - t_start > args.max_seconds:
                break
            now = time.time()
            if now - last_print >= 5.0:
                cm = db.compute_composite(args.sysid)
                s = db.stats()
                print()
                print(f"[t+{now-t_start:5.1f}s]")
                print(f"  uav={s['tables']['uav_state']['count']:4d}  "
                      f"sensor={s['tables']['sensor_readings']['count']:4d}  "
                      f"composite={s['tables']['composite_state']['count']:4d}")
                print(f"  avg_rssi_5s={cm.avg_rssi_5s}  "
                      f"nlos={cm.nlos_detected}  "
                      f"targets={cm.target_count_5s} {cm.target_classes}  "
                      f"battery={cm.battery_pct_smoothed:.0f}%" if cm.battery_pct_smoothed
                      else f"  battery=None")
                last_print = now
            time.sleep(0.2)
    finally:
        db.stop_composite_engine()
        db.close()
        print(f"\n==> Final DB at {db_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
