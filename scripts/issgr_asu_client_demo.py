#!/usr/bin/env python3
"""Демонстрационный клиент имитатора АСУ для ИССГР REST/OGC API.

Показывает типичный workflow внешнего консьюмера:

  1. Discover collections (OGC API Features `/collections`)
  2. Получить static objects (obstacles, GCS) — наземная часть ИССГР
  3. Live-poll UAV state через `/collections/uavs/items` (бортовая часть)
  4. POST новой Mission через REST → orchestrator его подбирает
  5. POST custom Obstacle (например obstacle обнаруженный CV-обработкой)
  6. GET digital_twin для overlay в внешней карте (QGIS/leaflet)

Использует только stdlib (urllib + json), без зависимостей — может быть
запущен на любой Python 3 машине которая видит API endpoint.

Запуск (после `sudo bash scripts/run_stage_3_issgr_demo.sh`):

  python3 scripts/issgr_asu_client_demo.py
  python3 scripts/issgr_asu_client_demo.py --host 192.168.1.91 --port 8770
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def get_json(url: str, timeout: float = 5.0) -> dict:
    """GET + parse JSON."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: float = 5.0) -> dict:
    """POST application/json + parse response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def step_1_discover(base_url: str) -> list[str]:
    """Discover collections."""
    print("\n=== Step 1: GET /collections ===")
    data = get_json(f"{base_url}/collections")
    collections = []
    for c in data["collections"]:
        print(f"  {c['id']:20s}  {c['title']:60s}  n={c['n_objects']}")
        collections.append(c["id"])
    return collections


def step_2_obstacles(base_url: str) -> None:
    """Получить static obstacles — наземная часть ИССГР."""
    print("\n=== Step 2: GET /collections/obstacles/items ===")
    fc = get_json(f"{base_url}/collections/obstacles/items")
    for feat in fc["features"]:
        p = feat["properties"]
        coords = feat["geometry"]["coordinates"][0]   # polygon outer ring
        centroid = (
            sum(c[0] for c in coords) / len(coords),
            sum(c[1] for c in coords) / len(coords),
        )
        print(f"  - {p['name']:15s}  class={p['issgr_class']:35s}"
              f"  height={p['height_m']:5.1f}м  material={p['material']:10s}"
              f"  centroid=({centroid[0]:.5f}, {centroid[1]:.5f})")


def step_3_poll_uav(base_url: str, n_polls: int = 5, interval_s: float = 2.0) -> None:
    """Live-poll UAV state."""
    print(f"\n=== Step 3: poll /collections/uavs/items {n_polls}× ===")
    for i in range(n_polls):
        try:
            fc = get_json(f"{base_url}/collections/uavs/items")
            if not fc["features"]:
                print(f"  [{i+1}/{n_polls}] no UAVs yet (orchestrator не публикует?)")
            for feat in fc["features"]:
                p = feat["properties"]
                geom = feat["geometry"]
                print(f"  [{i+1}/{n_polls}] {p['name']:10s} sysid={p['sysid']}"
                      f"  armed={p['armed']!s:5s} mode={p['flight_mode']:8s}"
                      f"  alt={p['altitude_m']:5.1f}м"
                      f"  pos=({geom['coordinates'][0]:.5f},"
                      f" {geom['coordinates'][1]:.5f})")
        except urllib.error.URLError as exc:
            print(f"  [{i+1}/{n_polls}] connection error: {exc}")
        if i < n_polls - 1:
            time.sleep(interval_s)


def step_4_post_mission(base_url: str, target_uav_id: str) -> str | None:
    """Создать новую Mission через REST."""
    print("\n=== Step 4: POST /collections/missions/items ===")
    mission = {
        "id": {
            "domain": "asw-imitator",   # АСУ источник
            "system": "asu-client-demo",
            "object_uuid": str(uuid.uuid4()),
        },
        "name": "АСУ-задача-1",
        "issgr_class": "operational_situation.mission.waypoint_route",
        "target_uav_id": _parse_id(target_uav_id),
        "state": "pending",
        "waypoints": [
            {"seq": 0, "action": "takeoff", "altitude_m": 10.0},
            {"seq": 1, "action": "waypoint",
             "latitude_deg": -35.36285, "longitude_deg": 149.16550,
             "altitude_m": 10.0},
            {"seq": 2, "action": "waypoint",
             "latitude_deg": -35.36300, "longitude_deg": 149.16600,
             "altitude_m": 15.0},
            {"seq": 3, "action": "land"},
        ],
        "properties": {
            "tasked_by": "АСУ Тестовый",
            "priority": "medium",
        },
    }
    try:
        resp = post_json(f"{base_url}/collections/missions/items", mission)
        print(f"  Created mission id={resp['id']}")
        return resp["id"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"  HTTP {exc.code}: {body[:300]}")
        return None


def step_5_post_obstacle(base_url: str) -> str | None:
    """Имитация CV-detected obstacle: например дерево обнаруженное dronemerd."""
    print("\n=== Step 5: POST /collections/obstacles/items (CV-detected) ===")
    tree = {
        "id": {
            "domain": "cv-detector",
            "system": "asu-client-demo",
            "object_uuid": str(uuid.uuid4()),
        },
        "name": "Tree (CV-detected)",
        "issgr_class": "geospatial_objects.terrain.vegetation",
        "geometry_polygon": {
            "type": "Polygon",
            "coordinates": [[
                [149.16590, -35.36275],
                [149.16595, -35.36275],
                [149.16595, -35.36270],
                [149.16590, -35.36270],
                [149.16590, -35.36275],
            ]],
        },
        "height_m": 12.0,
        "material": "wood",
        "properties": {
            "detector": "yolov8n",
            "confidence": 0.87,
            "detected_at": "2026-05-23T11:35:00Z",
        },
    }
    try:
        resp = post_json(f"{base_url}/collections/obstacles/items", tree)
        print(f"  Created obstacle id={resp['id']}")
        return resp["id"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"  HTTP {exc.code}: {body[:300]}")
        return None


def step_6_digital_twin(base_url: str) -> None:
    """Получить unified GeoJSON FeatureCollection всех ИССГР объектов."""
    print("\n=== Step 6: GET /digital_twin ===")
    fc = get_json(f"{base_url}/digital_twin")
    print(f"  numberMatched={fc['numberMatched']}, numberReturned={fc['numberReturned']}")
    by_class: dict[str, int] = {}
    for f in fc["features"]:
        top = f["properties"].get("issgr_class_top", "?")
        by_class[top] = by_class.get(top, 0) + 1
    for k, v in sorted(by_class.items()):
        print(f"    {k:30s}  count={v}")


def _parse_id(s: str) -> dict:
    """domain:system:uuid → dict для Pydantic ObjectIdentifier."""
    parts = s.split(":")
    if len(parts) != 3:
        return {"domain": "bas", "system": "asu-demo",
                "object_uuid": str(uuid.uuid4())}
    return {"domain": parts[0], "system": parts[1],
            "object_uuid": parts[2]}


def main() -> int:
    ap = argparse.ArgumentParser(description="ИССГР АСУ-имитатор demo client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-poll", action="store_true",
                    help="Skip UAV live-poll (для быстрого smoke)")
    args = ap.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    print(f"BAS ИССГР АСУ-имитатор demo — API at {base_url}")

    try:
        landing = get_json(base_url)
        print(f"\nServer landing: {landing.get('title')}")
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"FATAL: API не отвечает на {base_url}: {exc}")
        return 2

    collections = step_1_discover(base_url)
    if "obstacles" in collections:
        step_2_obstacles(base_url)
    if not args.no_poll:
        step_3_poll_uav(base_url, n_polls=3, interval_s=2.0)

    # Возьмём первый UAV id для POST mission, либо synthetic если нет.
    uavs = get_json(f"{base_url}/collections/uavs/items").get("features", [])
    if uavs:
        target_id = uavs[0]["id"]
    else:
        target_id = "bas:fizulin-rig:00000000-0000-0000-0000-000000000100"
    step_4_post_mission(base_url, target_id)
    step_5_post_obstacle(base_url)
    step_6_digital_twin(base_url)

    print("\nDONE. Open http://{}:{}/docs in browser для interactive Swagger UI.".format(
        args.host, args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
