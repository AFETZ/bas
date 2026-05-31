#!/usr/bin/env python3
"""Live digital-twin publisher: Web GCS /api/state → ИССГР UAV upsert.

Полётный драйвер пишет в events.jsonl только команды, а не непрерывную
позицию, поэтому UAV в ИССГР (и в Admin Dashboard) не двигался. Этот
паблишер опрашивает live-позу дрона из Web GCS `/api/state` и upsert'ит её
в ИССГР `/collections/uavs/items` (тот же object_uuid → обновление, не
дубль). Результат: летишь на :8765 — двойник в ИССГР и Admin (:8810) ходит
за дроном в реальном времени.

  python scripts/gcs_to_issgr_publisher.py \
      --gcs-url http://127.0.0.1:8765 --issgr-url http://127.0.0.1:8770
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request

# Origin = SITL home по умолчанию (для NED→lat/lon, если GPS lat/lon нет).
_DEF_ORIGIN_LAT = -35.363262
_DEF_ORIGIN_LON = 149.165237


def get_state(gcs_url: str) -> dict:
    with urllib.request.urlopen(gcs_url.rstrip("/") + "/api/state", timeout=2.0) as r:
        return json.loads(r.read().decode("utf-8"))


def upsert_uav(issgr_url: str, payload: dict) -> None:
    url = issgr_url.rstrip("/") + "/collections/uavs/items"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=3.0).read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gcs-url", default="http://127.0.0.1:8765")
    ap.add_argument("--issgr-url", default="http://127.0.0.1:8770")
    ap.add_argument("--sysid", type=int, default=1)
    ap.add_argument("--name", default="Iris-Live-1")
    ap.add_argument("--object-uuid",
                    default="00000000-0000-0000-0000-000000000001")
    ap.add_argument("--period-s", type=float, default=0.5)
    ap.add_argument("--max-seconds", type=float, default=0.0)
    ap.add_argument("--origin-lat", type=float, default=_DEF_ORIGIN_LAT)
    ap.add_argument("--origin-lon", type=float, default=_DEF_ORIGIN_LON)
    args = ap.parse_args()

    t0 = time.time()
    n = 0
    print(f"[gcs->issgr] {args.gcs_url}/api/state → upsert UAV в "
          f"{args.issgr_url}/collections/uavs/items (every {args.period_s}s)",
          flush=True)
    while True:
        if args.max_seconds and (time.time() - t0) > args.max_seconds:
            break
        try:
            st = get_state(args.gcs_url)
        except Exception:
            time.sleep(args.period_s)
            continue
        lat = st.get("lat_deg")
        lon = st.get("lon_deg")
        if lat in (None, 0) or lon in (None, 0):
            # Нет GPS lat/lon (напр. demo-режим) → считаем из local NED + origin.
            loc = st.get("local") or {}
            if "north" in loc and "east" in loc:
                north = float(loc["north"])
                east = float(loc["east"])
                lat = args.origin_lat + north / 111_319.9
                lon = args.origin_lon + east / (
                    111_319.9 * max(math.cos(math.radians(args.origin_lat)), 0.01))
            else:
                time.sleep(args.period_s)
                continue
        payload = {
            "id": {"domain": "bas", "system": "gcs-live",
                   "object_uuid": args.object_uuid},
            "name": args.name, "sysid": args.sysid,
            "issgr_class": "operational_situation.uav.rotary_wing",
            "pose": {
                "latitude_deg": float(lat),
                "longitude_deg": float(lon),
                "altitude_m": float(st.get("altitude_m", 0.0) or 0.0),
                "heading_deg": float(st.get("heading_deg", 0.0) or 0.0),
            },
            "armed": bool(st.get("armed", False)),
            "flight_mode": str(st.get("current_mode", "UNKNOWN")),
        }
        try:
            upsert_uav(args.issgr_url, payload)
            n += 1
            if n == 1 or n % 20 == 0:
                print(f"[gcs->issgr] upsert #{n}: lat={lat:.6f} lon={lon:.6f} "
                      f"alt={payload['pose']['altitude_m']:.1f}m "
                      f"mode={payload['flight_mode']}", flush=True)
        except Exception as exc:
            if n % 20 == 0:
                print(f"[gcs->issgr] upsert err: {str(exc)[:90]}", flush=True)
        time.sleep(args.period_s)
    print(f"[gcs->issgr] done, {n} upserts", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
