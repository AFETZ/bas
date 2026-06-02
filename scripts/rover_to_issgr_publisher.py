#!/usr/bin/env python3
"""Наземный транспорт (ArduRover) → ИССГР цифровой двойник.

Подписывается на MAVLink телеметрию ArduRover SITL (GLOBAL_POSITION_INT +
HEARTBEAT) и upsert-ит наземную машину как объект ИССГР с классом
`operational_situation.ground_vehicle.*`. Так наземный транспорт появляется
в едином цифровом двойнике рядом с БАС (та же витрина/АСУ-клиент).

Это интерфейс «наземный транспорт ↔ ИССГР» по аналогии с
gcs_to_issgr_publisher.py для БАС.

Usage:
  ./.venv/bin/python scripts/rover_to_issgr_publisher.py \
      --mavlink tcp:127.0.0.1:5770 --issgr-url http://127.0.0.1:8770
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".venv/lib/python3.12/site-packages"))

# Класс наземного транспорта: wheeled=дорожный, offroad=вне дорог.
FRAME_TO_CLASS = {
    "rover": "operational_situation.ground_vehicle.wheeled",
    "rover-skid": "operational_situation.ground_vehicle.offroad",
    "rover-vectored": "operational_situation.ground_vehicle.wheeled",
}


def upsert_rover(issgr_url: str, obj_uuid: str, name: str, issgr_class: str,
                 sysid: int, lat: float, lon: float, alt: float,
                 heading: float, armed: bool, mode: str,
                 speed_mps: float) -> bool:
    payload = {
        "id": {"domain": "bas", "system": "ardurover",
               "object_uuid": obj_uuid},
        "name": name, "sysid": sysid,
        "issgr_class": issgr_class,
        "pose": {"latitude_deg": lat, "longitude_deg": lon,
                 "altitude_m": alt, "heading_deg": heading},
        "armed": armed, "flight_mode": mode,
        "battery_v": 12.4,
        "velocity_ned": [round(speed_mps, 2), 0.0, 0.0],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        issgr_url.rstrip("/") + "/collections/uavs/items",
        data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3.0).read()
        return True
    except Exception:
        return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ArduRover → ИССГР publisher")
    ap.add_argument("--mavlink", default="tcp:127.0.0.1:5770")
    ap.add_argument("--issgr-url", default="http://127.0.0.1:8770")
    ap.add_argument("--frame", default="rover", choices=list(FRAME_TO_CLASS))
    ap.add_argument("--sysid", type=int, default=2)
    ap.add_argument("--name", default="Rover-Ground-1")
    ap.add_argument("--period-s", type=float, default=0.4)
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args(argv)

    from pymavlink import mavutil
    issgr_class = FRAME_TO_CLASS[args.frame]
    obj_uuid = "00000000-0000-0000-0000-0000000000a1"   # стабильный → upsert
    print(f"[rover-issgr] connect {args.mavlink} → ИССГР {args.issgr_url} "
          f"class={issgr_class}", flush=True)

    mav = mavutil.mavlink_connection(args.mavlink, source_system=251)
    mav.wait_heartbeat(timeout=30)
    mav.mav.request_data_stream_send(mav.target_system, mav.target_component,
                                     0, 5, 1)

    armed = False
    mode = "MANUAL"
    last_post = 0.0
    n = 0
    t_start = time.time()
    while True:
        if args.max_seconds and (time.time() - t_start) > args.max_seconds:
            break
        msg = mav.recv_match(blocking=True, timeout=1.0)
        if msg is None:
            continue
        mt = msg.get_type()
        if mt == "HEARTBEAT":
            # SITL роутит GCS-heartbeat драйвера (sysid 255, type=GCS) на этот
            # телеметрийный линк. Берём ТОЛЬКО heartbeat автопилота (наземная
            # машина), иначе armed/mode читаются с GCS и всегда False/0.
            if msg.type == mavutil.mavlink.MAV_TYPE_GCS:
                continue
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            try:
                mode = mavutil.mode_string_v10(msg)
            except Exception:
                mode = str(msg.custom_mode)
        elif mt == "GLOBAL_POSITION_INT" and msg.lat and msg.lon:
            now = time.time()
            if now - last_post < args.period_s:
                continue
            last_post = now
            lat, lon = msg.lat / 1e7, msg.lon / 1e7
            alt = msg.alt / 1000.0
            hdg = (msg.hdg / 100.0) if msg.hdg != 65535 else 0.0
            speed = (msg.vx ** 2 + msg.vy ** 2) ** 0.5 / 100.0
            if upsert_rover(args.issgr_url, obj_uuid, args.name, issgr_class,
                            args.sysid, lat, lon, alt, hdg, armed, mode, speed):
                n += 1
                if n % 10 == 0:
                    print(f"[rover-issgr] {n} upserts; last ({lat:.6f},{lon:.6f}) "
                          f"{mode} armed={armed} {speed:.1f} m/s", flush=True)

    print(f"[rover-issgr] done — {n} upserts", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
