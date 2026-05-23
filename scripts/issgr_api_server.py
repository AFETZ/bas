#!/usr/bin/env python3
"""ИССГР REST/OGC API server — standalone entrypoint.

Поднимает FastAPI приложение из orchestrator.issgr.api с опциональным
live-tailer'ом orchestrator events.jsonl: каждое flight event обновляет
UAV в repository, что даёт АСУ-клиентам real-time state дрона через
стандартный OGC API Features `/collections/uavs/items`.

Также из `configs/issgr_seed.json` (опционально) загружаются static объекты —
GCS, obstacles из RF demo сцены. Так АСУ-клиент сразу видит ангар, башню и
GCS mast без необходимости POST-ить их вручную.

Usage:
  ./.venv/bin/python scripts/issgr_api_server.py
  ./.venv/bin/python scripts/issgr_api_server.py --port 8770
  ./.venv/bin/python scripts/issgr_api_server.py --events logs/<run>/events.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "orchestrator" / "src"))

from orchestrator.issgr import (   # noqa: E402
    GCS, IssgrClass, IssgrRepository, Obstacle, ObjectIdentifier,
    PolygonGeometry, Pose, UAV,
)
from orchestrator.issgr.api import create_app   # noqa: E402


log = logging.getLogger("issgr_server")


# Default seed: GCS + ангар + башня из RF demo сцены (rf_obstacles), привязанные
# к Canberra-origin (-35.363262, 149.165237). Используются как
# "наземная часть ИССГР" пока АСУ-клиент не POST-нет свои.
DEFAULT_GCS = GCS(
    id=ObjectIdentifier(domain="bas", system="fizulin-rig",
                        object_uuid=__import__("uuid").UUID("00000000-0000-0000-0000-000000000001")),
    name="Operator GCS",
    pose=Pose(latitude_deg=-35.363262 - 0.00054,   # ~60 м южнее origin
              longitude_deg=149.165237,
              altitude_m=1.5),
    operator_callsign="OP-01",
)


def _make_obstacle_polygon(
    center_lat: float, center_lon: float,
    size_north_m: float, size_east_m: float,
) -> PolygonGeometry:
    """Approx rectangle в lat/lon вокруг center, размером в метрах."""
    deg_per_m_lat = 1.0 / 111_319.9
    import math
    deg_per_m_lon = 1.0 / (111_319.9 * max(math.cos(math.radians(center_lat)), 0.01))
    half_n = (size_north_m / 2) * deg_per_m_lat
    half_e = (size_east_m / 2) * deg_per_m_lon
    return PolygonGeometry(coordinates=[[
        [center_lon - half_e, center_lat - half_n],
        [center_lon + half_e, center_lat - half_n],
        [center_lon + half_e, center_lat + half_n],
        [center_lon - half_e, center_lat + half_n],
        [center_lon - half_e, center_lat - half_n],
    ]])


def seed_repository(repo: IssgrRepository) -> int:
    """Default seed для RF demo сцены."""
    n_loaded = 0

    repo.upsert("gcs", DEFAULT_GCS)
    n_loaded += 1

    # Hangar: 45N, 0E (от origin), 20×32×18м
    origin_lat, origin_lon = -35.363262, 149.165237
    deg_per_m_lat = 1.0 / 111_319.9
    deg_per_m_lon = 1.0 / (111_319.9 * 0.817)   # cos(-35.36) ≈ 0.817

    hangar = Obstacle(
        id=ObjectIdentifier(
            domain="bas", system="fizulin-rig",
            object_uuid=__import__("uuid").UUID("00000000-0000-0000-0000-000000000010"),
        ),
        name="Hangar",
        issgr_class=IssgrClass.GEO_HANGAR,
        geometry_polygon=_make_obstacle_polygon(
            origin_lat + 45 * deg_per_m_lat,
            origin_lon,
            20.0, 32.0,
        ),
        height_m=18.0,
        material="metal",
        properties={"rf_signature": "high-reflectivity"},
    )
    repo.upsert("obstacles", hangar)
    n_loaded += 1

    tower = Obstacle(
        id=ObjectIdentifier(
            domain="bas", system="fizulin-rig",
            object_uuid=__import__("uuid").UUID("00000000-0000-0000-0000-000000000011"),
        ),
        name="Control Tower",
        issgr_class=IssgrClass.GEO_TOWER,
        geometry_polygon=_make_obstacle_polygon(
            origin_lat + 82 * deg_per_m_lat,
            origin_lon + 32 * deg_per_m_lon,
            9.0, 9.0,
        ),
        height_m=24.0,
        material="concrete",
    )
    repo.upsert("obstacles", tower)
    n_loaded += 1

    log.info("seeded %d ISSGR objects (1 GCS + 2 obstacles)", n_loaded)
    return n_loaded


def tail_events_into_repo(events_path: Path, repo: IssgrRepository) -> None:
    """Background thread: tail orchestrator events.jsonl → UAV upsert."""
    fp = None
    last_uav_id: ObjectIdentifier | None = None

    log.info("[events-tailer] watching %s", events_path)

    while True:
        if not events_path.exists():
            time.sleep(0.5)
            continue
        if fp is None:
            fp = events_path.open("r", encoding="utf-8")
            fp.seek(0, os.SEEK_END)   # only new events
        line = fp.readline()
        if not line:
            time.sleep(0.1)
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event_type") != "flight":
            continue

        pos = ev.get("position", {})
        if "lat" not in pos:
            continue

        # Сохраняем тот же UAV через UUID anchored at sysid=1 — overwrite
        # последнего state.
        import uuid as _uuid
        uav_uuid = _uuid.UUID("00000000-0000-0000-0000-000000000100")

        uav = UAV(
            id=ObjectIdentifier(
                domain="bas", system="fizulin-rig", object_uuid=uav_uuid,
            ),
            name=pos.get("vehicle_name", "UAV-1"),
            sysid=int(pos.get("sysid", 1)),
            pose=Pose(
                latitude_deg=float(pos["lat"]),
                longitude_deg=float(pos["lon"]),
                altitude_m=float(pos.get("alt_rel_m", 0.0)),
                heading_deg=float(pos.get("heading_deg", 0.0)),
            ),
            armed=bool(pos.get("armed", False)),
            flight_mode=str(pos.get("flight_mode", "UNKNOWN")),
            battery_v=pos.get("battery_v"),
            velocity_ned=pos.get("velocity_ned"),
        )
        try:
            repo.upsert("uavs", uav)
            if last_uav_id != uav.id:
                last_uav_id = uav.id
                log.info("[events-tailer] first UAV upsert: %s sysid=%d",
                         uav.id.as_string(), uav.sysid)
        except Exception as exc:
            log.error("[events-tailer] upsert failed: %s", exc)


def main() -> int:
    ap = argparse.ArgumentParser(description="ИССГР REST/OGC API server")
    ap.add_argument("--host", default=os.environ.get("BAS_ISSGR_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("BAS_ISSGR_PORT", "8770")))
    ap.add_argument("--events", default=os.environ.get("BAS_ISSGR_EVENTS", ""),
                    help="Путь к orchestrator events.jsonl для live tailing UAV")
    ap.add_argument("--persist", default=os.environ.get("BAS_ISSGR_PERSIST", ""),
                    help="JSONL persistence path (optional)")
    ap.add_argument("--no-seed", action="store_true",
                    help="Не loaded default GCS+obstacles")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    persist_path = Path(args.persist) if args.persist else None
    repo = IssgrRepository(persist_path=persist_path)

    if not args.no_seed:
        seed_repository(repo)

    if args.events:
        events_path = Path(args.events).resolve()
        threading.Thread(
            target=tail_events_into_repo,
            args=(events_path, repo),
            daemon=True, name="events-tailer",
        ).start()

    app = create_app(repo=repo)

    import uvicorn
    log.info("ИССГР API server starting on %s:%d (collections=%s)",
             args.host, args.port, repo.collections())
    log.info("  OpenAPI: http://%s:%d/openapi.json", args.host, args.port)
    log.info("  Swagger UI: http://%s:%d/docs", args.host, args.port)
    log.info("  Digital twin: http://%s:%d/digital_twin", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
