#!/usr/bin/env python3
"""Admin web integration smoke — wires ИССГР + OnBoardDB + admin end-to-end.

Сценарий:
  1. Start issgr_api_server :18770 с urban seed
  2. Создаём временную SQLite OnBoardDB + наполняем тестовыми данными
  3. Start admin_web_server :18811 с --issgr-url + --onboard-db
  4. Verify /api/admin/collections возвращает counts (uavs/obstacles/...)
  5. Verify /api/admin/items?c=obstacles возвращает urban catalog
  6. Verify /api/admin/onboard_stats содержит наши seed rows
  7. Verify /api/admin/onboard_composite latest metrics
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

REPO = Path("/home/afetz/bas-prototype")
ISSGR_PORT = 18770
ADMIN_PORT = 18811
ONBOARD_DB_PATH = Path("/tmp/_admin_integration_onboard.db")
ISSGR_LOG = Path("/tmp/_admin_integration_issgr.log")
ADMIN_LOG = Path("/tmp/_admin_integration_admin.log")


def _port_open(port: int, timeout: float = 0.2) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def _get_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def seed_onboard_db() -> None:
    """Создаём temp OnBoardDB и наполняем 5 UAV state + 4 sensor + 1 composite."""
    sys.path.insert(0, str(REPO / "orchestrator/src"))
    from orchestrator.issgr import (
        OnBoardDB, UAV, Pose, ObjectIdentifier, SensorReading, IssgrClass,
    )

    if ONBOARD_DB_PATH.exists():
        ONBOARD_DB_PATH.unlink()
    db = OnBoardDB(path=ONBOARD_DB_PATH, retention_seconds=3600,
                   composite_interval_s=1.0)
    uav_uuid = uuid.UUID("00000000-0000-0000-0000-000000000200")
    for i in range(5):
        uav = UAV(
            id=ObjectIdentifier(object_uuid=uav_uuid),
            name="Iris-Admin-Smoke", sysid=1,
            pose=Pose(latitude_deg=-35.363 + i * 0.0001,
                      longitude_deg=149.165, altitude_m=15.0 + i,
                      heading_deg=90.0),
            armed=True, flight_mode="AUTO",
            battery_v=12.5 - i * 0.1,
        )
        db.append_uav_state(uav)
    for v in (-70.0, -72.0, -68.0, -75.0):
        sr = SensorReading(
            id=ObjectIdentifier(),
            name="RSSI", source_uav_id=ObjectIdentifier(object_uuid=uav_uuid),
            sensor_type="rssi_lora",
            value={"rssi_dbm": v, "confidence": v},
        )
        db.append_sensor_reading(sr)
    db.compute_composite(sysid=1)
    db.close()


def main() -> int:
    for p in (ISSGR_LOG, ADMIN_LOG):
        if p.exists():
            p.unlink()

    # 1. Seed on-board DB.
    print("==> [1] seed on-board DB")
    seed_onboard_db()
    print(f"    {ONBOARD_DB_PATH} ({ONBOARD_DB_PATH.stat().st_size}B)")

    # 2. Start ISSGR.
    print("==> [2] start ISSGR :{}".format(ISSGR_PORT))
    issgr_proc = subprocess.Popen(
        ["python3", str(REPO / "scripts/issgr_api_server.py"),
         "--port", str(ISSGR_PORT), "--seed-profile", "urban"],
        stdout=open(ISSGR_LOG, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")
                          + ":" + str(REPO / "orchestrator/src")},
    )

    # 3. Start admin.
    print("==> [3] start admin :{}".format(ADMIN_PORT))
    admin_proc = subprocess.Popen(
        ["python3", str(REPO / "scripts/admin_web_server.py"),
         "--port", str(ADMIN_PORT), "--host", "127.0.0.1",
         "--issgr-url", f"http://127.0.0.1:{ISSGR_PORT}",
         "--onboard-db", str(ONBOARD_DB_PATH)],
        stdout=open(ADMIN_LOG, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")
                          + ":" + str(REPO / "orchestrator/src")},
    )

    try:
        # Wait both servers.
        for _ in range(60):
            if _port_open(ISSGR_PORT) and _port_open(ADMIN_PORT):
                break
            time.sleep(0.2)
        else:
            print("[err] servers didn't both bind in 12s")
            print("--- issgr log ---");  print(ISSGR_LOG.read_text())
            print("--- admin log ---");  print(ADMIN_LOG.read_text())
            return 1
        # Дать ISSGR seed загрузить.
        time.sleep(1.5)
        print("==> [4] both servers up")

        base = f"http://127.0.0.1:{ADMIN_PORT}"

        # /api/admin/collections.
        print("\n[5] /api/admin/collections")
        d = _get_json(f"{base}/api/admin/collections")
        print(f"    {d}")
        # ISSGR с urban profile = 1 GCS + 8 obstacles минимум.
        assert d.get("gcs", 0) >= 1, f"expected ≥1 gcs, got {d}"
        assert d.get("obstacles", 0) >= 6, f"expected ≥6 obstacles, got {d}"

        # /api/admin/items?c=obstacles.
        print("\n[6] /api/admin/items?c=obstacles")
        d = _get_json(f"{base}/api/admin/items?c=obstacles")
        n_ret = d.get("numberReturned", 0)
        print(f"    numberReturned={n_ret}, numberMatched={d.get('numberMatched', 0)}")
        assert n_ret >= 6
        # Hangar присутствует в urban catalog.
        names = [(f.get("properties") or {}).get("name", "") for f in d.get("features", [])]
        assert any("Hangar" in n for n in names), f"no Hangar in {names}"

        # /api/admin/onboard_stats.
        print("\n[7] /api/admin/onboard_stats")
        d = _get_json(f"{base}/api/admin/onboard_stats")
        print(f"    path={d.get('path')}")
        print(f"    tables.uav_state.count    = {d['tables']['uav_state']['count']}")
        print(f"    tables.sensor_readings    = {d['tables']['sensor_readings']['count']}")
        print(f"    tables.composite_state    = {d['tables']['composite_state']['count']}")
        assert d["tables"]["uav_state"]["count"] == 5
        assert d["tables"]["sensor_readings"]["count"] == 4
        assert d["tables"]["composite_state"]["count"] >= 4

        # /api/admin/onboard_composite.
        print("\n[8] /api/admin/onboard_composite")
        d = _get_json(f"{base}/api/admin/onboard_composite")
        m = d.get("metrics", [])
        print(f"    {len(m)} composite metrics")
        for row in m:
            print(f"    sysid={row['sysid']:>2}  {row['metric_name']:>22}  "
                  f"={row['metric_value']:.3f}")
        # Должны быть avg_rssi_5s, nlos_detected, target_count_5s, и т.д.
        names = {row["metric_name"] for row in m}
        assert "avg_rssi_5s" in names
        assert "nlos_detected" in names

        # /api/admin/tile_grid не зависит от backend.
        print("\n[9] /api/admin/tile_grid?n=20&e=20&size=1000")
        d = _get_json(f"{base}/api/admin/tile_grid?n=20&e=20&size=1000")
        assert d["total_tiles"] == 400
        assert abs(d["total_area_km2"] - 400.0) < 0.5
        print(f"    {d['total_tiles']} tiles, area={d['total_area_km2']:.0f}km²")

        print("\nALL CHECKS PASSED")
        return 0
    finally:
        for proc in (admin_proc, issgr_proc):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
