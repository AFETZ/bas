#!/usr/bin/env python3
"""ИССГР multicast sync subscriber — standalone entrypoint.

Слушает multicast 239.10.10.10:5500, декодирует входящие packet'ы 40/80
байт, печатает в stdout + опционально POST-ит в local ИССГР REST API
(имитатор second АСУ-узла который syncит state от первого).

Usage:
  ./.venv/bin/python scripts/issgr_sync_subscriber.py
  ./.venv/bin/python scripts/issgr_sync_subscriber.py --issgr-url http://127.0.0.1:8771
  ./.venv/bin/python scripts/issgr_sync_subscriber.py --no-post --log-file rx.tsv
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, UTC
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "orchestrator" / "src"))

from orchestrator.issgr.sync import (   # noqa: E402
    DEFAULT_MULTICAST_GROUP, DEFAULT_MULTICAST_PORT,
    MSG_HEARTBEAT, MSG_POSITION_L1, MSG_SENSOR_L2,
    MulticastSubscriber, decode_packet,
)


log = logging.getLogger("issgr-sync-sub")


def post_uav_upsert(issgr_url: str, pkt) -> bool:
    """Превратить L1Packet → UAV POST в local ИССГР.

    Hash-based identifier reconstructed deterministically: при collision
    разных domain/system всё-таки получим разные local UUID через namespace.
    """
    # Hash-derived deterministic UUID: NS UUIDv5 от "issgr-sync:<hash>".
    derived_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"issgr-sync:{pkt.domain_hash:08x}:{pkt.object_hash:08x}",
    )
    payload = {
        "id": {
            "domain": "bas-sync",
            "system": f"node-{pkt.domain_hash:08x}",
            "object_uuid": str(derived_uuid),
        },
        "name": f"UAV-sync-{pkt.object_hash:08x}",
        "issgr_class": "operational_situation.uav.rotary_wing",
        "sysid": 1,
        "pose": {
            "latitude_deg": pkt.lat_deg,
            "longitude_deg": pkt.lon_deg,
            "altitude_m": pkt.alt_m,
            "heading_deg": pkt.heading_deg,
        },
        "armed": pkt.armed,
        "flight_mode": "UNKNOWN",
        "properties": {
            "sync_source": "multicast",
            "domain_hash": pkt.domain_hash,
            "object_hash": pkt.object_hash,
            "sequence": pkt.sequence,
            "timestamp_ms": pkt.timestamp_ms,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{issgr_url}/collections/uavs/items",
        data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=2.0).read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        log.debug("ISSGR POST failed: %s", exc)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="ИССГР multicast sync subscriber")
    ap.add_argument("--group", default=DEFAULT_MULTICAST_GROUP)
    ap.add_argument("--port", type=int, default=DEFAULT_MULTICAST_PORT)
    ap.add_argument("--interface", default="0.0.0.0")
    ap.add_argument("--issgr-url", default="http://127.0.0.1:8771",
                    help="ИССГР API для POST upserts")
    ap.add_argument("--no-post", action="store_true",
                    help="Только декодировать + log, без POST")
    ap.add_argument("--log-file", default="",
                    help="TSV log входящих packets")
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    log_fp = open(args.log_file, "w") if args.log_file else None
    stats = {"HEARTBEAT": 0, "L1": 0, "L2": 0, "L1_posted": 0,
             "crc_bad": 0, "unknown": 0, "bytes_rx": 0}
    last_seq: dict[int, int] = {}   # object_hash → last seq для gap detection
    gaps_detected = 0

    def on_packet(data: bytes, addr: tuple[str, int]) -> None:
        nonlocal gaps_detected
        stats["bytes_rx"] += len(data)
        pkt = decode_packet(data)
        if pkt is None:
            stats["unknown"] += 1
            return
        base = pkt if not hasattr(pkt, "base") else pkt.base
        if not base.crc_ok:
            stats["crc_bad"] += 1
            log.warning("CRC bad from %s, len=%d", addr, len(data))
            return

        # Gap detection per-object.
        prev = last_seq.get(base.object_hash)
        if prev is not None and base.sequence not in (prev + 1, 1):
            if base.sequence > prev + 1:
                gaps_detected += 1
                log.warning("gap detected for obj=%08x: prev=%d new=%d (delta=%d)",
                            base.object_hash, prev, base.sequence,
                            base.sequence - prev - 1)
        last_seq[base.object_hash] = base.sequence

        if base.msg_type == MSG_HEARTBEAT:
            stats["HEARTBEAT"] += 1
            log.info("[%s] HEARTBEAT  domain=%08x obj=%08x seq=%d",
                     addr[0], base.domain_hash, base.object_hash,
                     base.sequence)
        elif base.msg_type == MSG_POSITION_L1:
            stats["L1"] += 1
            log.info("[%s] POSITION   obj=%08x seq=%d "
                     "lat=%.6f lon=%.6f alt=%.1fм hdg=%.0f° spd=%.1fm/s armed=%s",
                     addr[0], base.object_hash, base.sequence,
                     base.lat_deg, base.lon_deg, base.alt_m,
                     base.heading_deg, base.speed_mps, base.armed)
            if not args.no_post and post_uav_upsert(args.issgr_url, base):
                stats["L1_posted"] += 1
        elif base.msg_type == MSG_SENSOR_L2:
            stats["L2"] += 1
            l2 = pkt   # type: ignore[assignment]
            log.info("[%s] SENSOR L2  obj=%08x seq=%d "
                     "sensor_hash=%08x value=%.3f "
                     "ground=(%.6f,%.6f) conf=%.3f",
                     addr[0], base.object_hash, base.sequence,
                     l2.sensor_type_hash, l2.sensor_value,
                     l2.ground_lat_deg, l2.ground_lon_deg,
                     l2.confidence)
        else:
            stats["unknown"] += 1

        if log_fp is not None:
            log_fp.write(
                f"{time.time():.3f}\t{addr[0]}\t{len(data)}\t"
                f"{base.msg_type:02x}\t{base.sequence}\t{data.hex()}\n",
            )
            log_fp.flush()

    sub = MulticastSubscriber(callback=on_packet, group=args.group,
                              port=args.port, interface=args.interface)
    sub.start()
    log.info("listening multicast %s:%d (interface=%s)",
             args.group, args.port, args.interface)
    log.info("ISSGR POST endpoint: %s (no_post=%s)",
             args.issgr_url, args.no_post)

    start = time.time()
    try:
        while True:
            time.sleep(5.0)
            log.info("STATS: %s gaps=%d", stats, gaps_detected)
            if args.max_seconds and (time.time() - start) > args.max_seconds:
                log.info("max-seconds reached")
                break
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        sub.stop()
        if log_fp is not None:
            log_fp.close()
        log.info("FINAL STATS: %s gaps=%d", stats, gaps_detected)

    return 0


if __name__ == "__main__":
    sys.exit(main())
