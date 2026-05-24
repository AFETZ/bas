#!/usr/bin/env python3
"""issgr_sync_publisher /stats endpoint smoke.

Boot ИССГР + publisher --stats-port, fetch /stats несколько раз,
verify keys и monotonic counter growth.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path("/home/afetz/bas-prototype")
ISSGR_PORT = 28770
STATS_PORT = 28811


def _port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def main() -> int:
    # Start ISSGR (seed urban даёт 8 obstacles но 0 UAVs).
    issgr_log = Path("/tmp/_sync_stats_issgr.log")
    pub_log = Path("/tmp/_sync_stats_pub.log")
    if issgr_log.exists(): issgr_log.unlink()
    if pub_log.exists(): pub_log.unlink()

    issgr = subprocess.Popen(
        ["python3", str(REPO / "scripts/issgr_api_server.py"),
         "--port", str(ISSGR_PORT), "--seed-profile", "urban"],
        stdout=open(issgr_log, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")
                          + ":" + str(REPO / "orchestrator/src")},
    )

    # Seed одного UAV для L1 packet generation.
    for _ in range(30):
        if _port_open(ISSGR_PORT):
            break
        time.sleep(0.2)
    time.sleep(1.0)
    import urllib.request as _ur
    try:
        req = _ur.Request(
            f"http://127.0.0.1:{ISSGR_PORT}/collections/uavs/items",
            data=json.dumps({
                "id": {"domain": "bas", "system": "stats-smoke",
                       "object_uuid": "00000000-0000-0000-0000-000000000abc"},
                "name": "Iris-Stats", "sysid": 1,
                "issgr_class": "operational_situation.uav.rotary_wing",
                "pose": {"latitude_deg": -35.363, "longitude_deg": 149.165,
                         "altitude_m": 10.0, "heading_deg": 90.0},
                "armed": True, "flight_mode": "GUIDED",
            }).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        _ur.urlopen(req, timeout=3).read()
        print("==> seeded 1 UAV")
    except Exception as e:
        print(f"[warn] uav seed failed: {e}")

    # Start publisher с --stats-port.
    pub = subprocess.Popen(
        ["python3", str(REPO / "scripts/issgr_sync_publisher.py"),
         "--issgr-url", f"http://127.0.0.1:{ISSGR_PORT}",
         "--port", "5500", "--ttl", "1",
         "--interval", "0.5",
         "--node-id", "stats-smoke-node",
         "--max-seconds", "10",
         "--stats-port", str(STATS_PORT)],
        stdout=open(pub_log, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")
                          + ":" + str(REPO / "orchestrator/src")},
    )

    try:
        for _ in range(30):
            if _port_open(STATS_PORT):
                break
            time.sleep(0.2)
        else:
            print("stats endpoint didn't bind:")
            print(pub_log.read_text())
            return 1
        print(f"==> stats endpoint up on :{STATS_PORT}")

        # First fetch.
        time.sleep(1.2)
        d1 = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{STATS_PORT}/stats", timeout=2).read())
        print(f"\n[t=1s] {json.dumps(d1, indent=2)}")

        # Second fetch — expect counters increased.
        time.sleep(2.0)
        d2 = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{STATS_PORT}/stats", timeout=2).read())
        print(f"\n[t=3s] totals={d2['totals']}  tracked={d2['tracked_objects']}  "
              f"uptime={d2['uptime_s']:.1f}s")

        # Verify keys.
        for k in ("ok", "node_id", "endpoint", "ttl", "interval_s",
                  "uptime_s", "issgr_url", "totals", "last_tick", "tracked_objects"):
            assert k in d2, f"missing key {k!r}"
        assert d2["ok"] is True
        assert d2["node_id"] == "stats-smoke-node"
        assert d2["totals"]["HEARTBEAT"] > d1["totals"]["HEARTBEAT"]
        assert d2["totals"]["L1"] >= d1["totals"]["L1"]
        assert d2["tracked_objects"] >= 1, "should track at least __node__"
        assert d2["uptime_s"] > d1["uptime_s"]
        print("\n  ✓ keys complete, counters monotonic, uptime monotonic")
        print("\nALL CHECKS PASSED")
        return 0
    finally:
        for p in (pub, issgr):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
