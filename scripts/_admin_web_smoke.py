#!/usr/bin/env python3
"""Admin web server smoke — start server, hit endpoints, verify JSON shape.

Boots admin_web_server в subprocess, hits ~7 API endpoints, verifies:
  * /api/admin/health → {ok: True}
  * static files (index/app/styles) served
  * /api/admin/tile_grid → FeatureCollection с N=100 polygons (10×10)
  * /api/admin/activity → log[] есть startup event
  * /api/admin/onboard_stats → 404 (no DB configured by default)
  * /api/admin/collections → {} (нет live ISSGR)
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
PORT = 18810   # fresh ephemeral
STUB_LOG = Path("/tmp/_admin_web_smoke_server.log")


def _port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def _get(path: str, timeout: float = 2.0) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, 'read') else b'', dict(e.headers or {})


def main() -> int:
    if STUB_LOG.exists():
        STUB_LOG.unlink()
    proc = subprocess.Popen(
        ["python3", str(REPO / "scripts/admin_web_server.py"),
         "--port", str(PORT), "--host", "127.0.0.1"],
        stdout=open(STUB_LOG, "wb"), stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO / ".venv/lib/python3.12/site-packages")
                          + ":" + str(REPO / "orchestrator/src")},
    )
    try:
        for _ in range(30):
            if _port_open(PORT):
                break
            time.sleep(0.1)
        else:
            print(f"server didn't bind in 3s; log:")
            print(STUB_LOG.read_text())
            return 1
        print(f"==> server up on :{PORT}")

        # --- 1. /api/admin/health ---
        print("\n[1] /api/admin/health")
        st, body, _ = _get("/api/admin/health")
        assert st == 200, f"health → {st}"
        d = json.loads(body)
        assert d.get("ok") is True
        print(f"    {d}")

        # --- 2. Static index.html ---
        print("\n[2] / (index.html)")
        st, body, hdrs = _get("/")
        assert st == 200
        assert b"BAS Admin Dashboard" in body
        assert hdrs.get("Content-Type", "").startswith("text/html")
        print(f"    {len(body)} bytes, content-type ok")

        # --- 3. Static app.js + styles.css ---
        print("\n[3] /app.js + /styles.css")
        for p, ct in [("/app.js", "application/javascript"),
                      ("/styles.css", "text/css")]:
            st, body, hdrs = _get(p)
            assert st == 200, f"{p} → {st}"
            assert hdrs.get("Content-Type", "").startswith(ct), \
                f"{p} content-type: {hdrs.get('Content-Type')}"
            print(f"    {p}: {len(body)}B  {ct}")

        # --- 4. /api/admin/tile_grid?n=10&e=10&size=2000 ---
        print("\n[4] /api/admin/tile_grid (10×10, 2km)")
        st, body, _ = _get("/api/admin/tile_grid?n=10&e=10&size=2000")
        assert st == 200
        d = json.loads(body)
        assert d["total_tiles"] == 100, d
        assert abs(d["total_area_km2"] - 400.0) < 0.5, d
        assert d["geojson"]["type"] == "FeatureCollection"
        assert len(d["geojson"]["features"]) == 100
        f0 = d["geojson"]["features"][0]
        assert f0["geometry"]["type"] == "Polygon"
        assert len(f0["geometry"]["coordinates"][0]) == 5   # closed ring
        print(f"    total_tiles={d['total_tiles']}  area={d['total_area_km2']:.0f}km²  "
              f"coverage={d['coverage_km_north']}×{d['coverage_km_east']} km")

        # --- 5. /api/admin/tile_grid clamp guard ---
        print("\n[5] tile_grid bounds clamp (n=999 → max 50)")
        st, body, _ = _get("/api/admin/tile_grid?n=999&e=999&size=99999")
        assert st == 200
        d = json.loads(body)
        assert d["total_tiles"] == 50 * 50, f"got {d['total_tiles']}"
        print(f"    clamped to {d['total_tiles']} tiles, tile_size_m={d['tile_size_m']}")

        # --- 6. /api/admin/activity ---
        print("\n[6] /api/admin/activity")
        st, body, _ = _get("/api/admin/activity")
        assert st == 200
        d = json.loads(body)
        assert "log" in d
        events = [e.get("event") for e in d["log"]]
        assert "startup" in events, f"missing startup event: {events}"
        print(f"    {len(d['log'])} events, e.g. {events[:3]}")

        # --- 7. /api/admin/onboard_stats — no DB → 404 ---
        print("\n[7] /api/admin/onboard_stats (no DB → 404)")
        st, body, _ = _get("/api/admin/onboard_stats")
        assert st == 404, f"got {st}"
        d = json.loads(body)
        assert "error" in d
        print(f"    {st}: {d}")

        # --- 8. /api/admin/collections — no ISSGR → {} ---
        print("\n[8] /api/admin/collections (no ISSGR → {})")
        st, body, _ = _get("/api/admin/collections")
        assert st == 200
        d = json.loads(body)
        # Empty без ISSGR backend.
        assert d == {}, f"expected {{}}, got {d}"
        print(f"    {d}")

        # --- 9. /api/admin/items (no ISSGR → {} fallback) ---
        print("\n[9] /api/admin/items?c=uavs (no ISSGR)")
        st, body, _ = _get("/api/admin/items?c=uavs")
        assert st == 200
        d = json.loads(body)
        # Empty без ISSGR backend.
        assert isinstance(d, dict)
        print(f"    {d}")

        # --- 10. /api/admin/sync_stats placeholder ---
        print("\n[10] /api/admin/sync_stats placeholder")
        st, body, _ = _get("/api/admin/sync_stats")
        assert st == 200
        d = json.loads(body)
        assert "info" in d
        print(f"    {d}")

        print("\nALL CHECKS PASSED")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
