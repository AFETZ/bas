#!/usr/bin/env python3
"""ИССГР multicast sync publisher — standalone entrypoint.

Подписывается на local ИССГР REST API (/digital_twin), периодически
emit-ит UAV positions + recent SensorReadings как multicast UDP пакеты
40/80 байт. Реализует пункт ТЗ "Синхронизация БД: автоматический запуск
синхронизации".

Usage:
  ./.venv/bin/python scripts/issgr_sync_publisher.py
  ./.venv/bin/python scripts/issgr_sync_publisher.py --port 5500 --interval 0.5
  ./.venv/bin/python scripts/issgr_sync_publisher.py --issgr-url http://node1:8770
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
import uuid
from datetime import datetime, UTC
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "orchestrator" / "src"))

from orchestrator.issgr.sync import (   # noqa: E402
    DEFAULT_MULTICAST_GROUP, DEFAULT_MULTICAST_PORT,
    MulticastPublisher, encode_heartbeat, encode_position_l1,
    encode_sensor_l2,
)
from orchestrator.issgr.models import (   # noqa: E402
    ObjectIdentifier, Pose, SensorReading, UAV,
)
from orchestrator.issgr.classifier import IssgrClass   # noqa: E402


log = logging.getLogger("issgr-sync-pub")


def fetch_geojson(url: str, timeout: float = 3.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("ISSGR fetch %s failed: %s", url, exc)
        return None


def _parse_id_str(s: str) -> ObjectIdentifier:
    parts = s.split(":")
    if len(parts) != 3:
        return ObjectIdentifier()
    return ObjectIdentifier(
        domain=parts[0], system=parts[1], object_uuid=uuid.UUID(parts[2]),
    )


def feature_to_uav(feature: dict) -> UAV | None:
    """Конвертирует GeoJSON Feature обратно в UAV model для encode_position_l1."""
    p = feature.get("properties", {})
    g = feature.get("geometry", {})
    coords = g.get("coordinates", [])
    if not coords or len(coords) < 2:
        return None
    alt = coords[2] if len(coords) >= 3 else p.get("altitude_m", 0.0)
    try:
        return UAV(
            id=_parse_id_str(feature.get("id", "bas:fizulin-rig:00000000-0000-0000-0000-000000000000")),
            name=p.get("name", "UAV"),
            issgr_class=IssgrClass.from_string(
                p.get("issgr_class", "operational_situation.uav.rotary_wing")),
            sysid=int(p.get("sysid", 1)),
            pose=Pose(
                latitude_deg=float(coords[1]),
                longitude_deg=float(coords[0]),
                altitude_m=float(alt),
                heading_deg=float(p.get("heading_deg") or 0.0),
            ),
            armed=bool(p.get("armed", False)),
            flight_mode=str(p.get("flight_mode", "UNKNOWN")),
            battery_v=p.get("battery_v"),
            velocity_ned=p.get("velocity_ned"),
        )
    except Exception as exc:
        log.debug("feature_to_uav failed: %s", exc)
        return None


def feature_to_sensor(feature: dict) -> SensorReading | None:
    p = feature.get("properties", {})
    val = p.get("value", {})
    if not isinstance(val, dict):
        return None
    try:
        return SensorReading(
            id=_parse_id_str(feature.get("id", "")),
            name=p.get("name", "Sensor"),
            issgr_class=IssgrClass.from_string(
                p.get("issgr_class", "functional_objects.sensor.ground_station")),
            source_uav_id=_parse_id_str(p.get("source_uav_id", "")),
            sensor_type=str(p.get("sensor_type", "unknown")),
            value=val,
        )
    except Exception as exc:
        log.debug("feature_to_sensor failed: %s", exc)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ИССГР multicast sync publisher")
    ap.add_argument("--issgr-url", default="http://127.0.0.1:8770",
                    help="Local ИССГР REST API")
    ap.add_argument("--group", default=DEFAULT_MULTICAST_GROUP)
    ap.add_argument("--port", type=int, default=DEFAULT_MULTICAST_PORT)
    ap.add_argument("--ttl", type=int, default=1)
    ap.add_argument("--interval", type=float, default=1.0,
                    help="Snapshot interval секунд (1 Hz default)")
    ap.add_argument("--node-id",
                    default=f"node-{uuid.uuid4().hex[:8]}",
                    help="Идентификатор этого publisher узла (для heartbeat)")
    ap.add_argument("--max-seconds", type=float, default=0.0)
    ap.add_argument("--log-file", default="",
                    help="Сохранять все sent packets (hex) в файл")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    publisher = MulticastPublisher(group=args.group, port=args.port, ttl=args.ttl)
    log.info("multicast publisher: %s:%d ttl=%d, polling ISSGR %s every %.1fs, node=%s",
             args.group, args.port, args.ttl, args.issgr_url,
             args.interval, args.node_id)

    log_fp = open(args.log_file, "w") if args.log_file else None
    start = time.time()
    seq_map: dict[str, int] = {}
    last_packets = {"L1": 0, "L2": 0, "HEARTBEAT": 0}

    try:
        while True:
            tick_start = time.time()

            # 1. Heartbeat от этого узла (presence ping для других subscribers).
            heartbeat_seq = seq_map.setdefault("__node__", 0) + 1
            seq_map["__node__"] = heartbeat_seq
            hb = encode_heartbeat(
                domain="bas-sync", system=args.node_id,
                object_uuid=args.node_id, sequence=heartbeat_seq,
            )
            publisher.send(hb)
            last_packets["HEARTBEAT"] += 1
            if log_fp:
                log_fp.write(f"{tick_start:.3f}\tHEARTBEAT\t{hb.hex()}\n")

            # 2. Snapshot UAVs.
            fc = fetch_geojson(f"{args.issgr_url}/collections/uavs/items")
            n_l1 = 0
            if fc and "features" in fc:
                for feat in fc["features"]:
                    uav = feature_to_uav(feat)
                    if uav is None:
                        continue
                    key = str(uav.id.object_uuid)
                    seq = seq_map.get(key, 0) + 1
                    seq_map[key] = seq & 0xFFFFFFFF
                    pkt = encode_position_l1(uav, sequence=seq_map[key])
                    publisher.send(pkt)
                    last_packets["L1"] += 1
                    n_l1 += 1
                    if log_fp:
                        log_fp.write(f"{tick_start:.3f}\tL1\t{pkt.hex()}\n")

            # 3. Recent sensor readings (CV detections, RSSI).
            fc2 = fetch_geojson(
                f"{args.issgr_url}/collections/sensor_readings/items?limit=30")
            n_l2 = 0
            if fc2 and "features" in fc2:
                for feat in fc2["features"]:
                    sr = feature_to_sensor(feat)
                    if sr is None:
                        continue
                    key = str(sr.id.object_uuid)
                    seq = seq_map.get(key, 0) + 1
                    seq_map[key] = seq & 0xFFFFFFFF
                    pkt = encode_sensor_l2(sr, sequence=seq_map[key])
                    publisher.send(pkt)
                    last_packets["L2"] += 1
                    n_l2 += 1
                    if log_fp:
                        log_fp.write(f"{tick_start:.3f}\tL2\t{pkt.hex()}\n")
                if log_fp:
                    log_fp.flush()

            log.info("tick: HEARTBEAT=1 L1=%d L2=%d (totals: %s)",
                     n_l1, n_l2, last_packets)

            elapsed = time.time() - tick_start
            sleep_s = max(0.0, args.interval - elapsed)
            time.sleep(sleep_s)

            if args.max_seconds and (time.time() - start) > args.max_seconds:
                log.info("max-seconds reached, exiting (sent: %s)", last_packets)
                break
    except KeyboardInterrupt:
        log.info("interrupted (sent: %s)", last_packets)
    finally:
        publisher.close()
        if log_fp is not None:
            log_fp.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
