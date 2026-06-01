#!/usr/bin/env python3
"""BAS Admin Dashboard — extended web interface.

Закрывает пункт ТЗ "Веб-интерфейс расширенный" (зона Федотенкова А.А.)
как single-page admin UI поверх существующих API:

  * ИССГР REST (OGC API Features 1.0) — proxy к /collections/*
  * OnBoardDB SQLite (опц., если configured) — stats + composite metrics
  * TileGrid GeoJSON генератор для tile-map визуализации >20×20 км
  * Activity log от orchestrator events.jsonl tail

Не подменяет существующий Stage 2.4 Web GCS (manual flight control);
дополняет его административной dashboard для multi-UAV / multi-tile /
ИССГР browse.

Архитектура — pure stdlib HTTP server (без FastAPI dep): /web/admin/
static + admin JSON endpoints. Опционально подцепляет
OnBoardDB файл и/или ИССГР URL для combining.

Запуск:
  ./scripts/admin_web_server.py \
      --port 8810 \
      --issgr-url http://127.0.0.1:8770 \
      --onboard-db /var/lib/onboard/bas.db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator/src"))

from orchestrator.issgr.large_map import TileGrid   # noqa: E402

# Опциональный import OnBoardDB — если файла нет, server работает в
# degraded mode.
try:
    from orchestrator.issgr.onboard import OnBoardDB
    HAS_ONBOARD = True
except ImportError:
    HAS_ONBOARD = False


WEB_DIR = REPO_ROOT / "web" / "admin"

# Global runtime state.
ISSGR_URL: str | None = None
GCS_URL: str | None = None       # Web GCS для команд из витрины (deep integration A)
ONBOARD_DB: Any = None
SYNC_STATS_URL: str | None = None
ACTIVITY: deque = deque(maxlen=200)
ORIGIN_LAT = -35.363262
ORIGIN_LON = 149.165237


def log_activity(event: str, detail: str = "") -> None:
    ts = time.strftime("%H:%M:%S", time.gmtime())
    ACTIVITY.append({"ts": ts, "event": event, "detail": detail})


def fetch_issgr(path: str, timeout: float = 3.0) -> dict[str, Any]:
    """GET ISSGR URL + path → JSON. Возвращает {} on error."""
    if not ISSGR_URL:
        return {}
    url = ISSGR_URL.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log_activity("issgr_fetch_fail", f"{path}: {e}")
        return {}


def fetch_gcs(path: str, timeout: float = 2.0) -> dict[str, Any]:
    """GET Web GCS path (напр. /api/state) → JSON; {} on error."""
    if not GCS_URL:
        return {}
    try:
        with urllib.request.urlopen(GCS_URL.rstrip("/") + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return {}


# Deep integration D: кибер-алерты от cyber_defense_monitor (NDJSON-файл).
# Витрина показывает их живьём баннером + spoof-метку на карте. Файл общий
# с пультом (Web GCS), путь — BAS_CYBER_ALERTS (demo-обёртка ставит).
CYBER_ALERTS_PATH = os.environ.get("BAS_CYBER_ALERTS", "/tmp/bas_cyber_alerts.jsonl")
CYBER_ALERT_WINDOW_S = 90.0


def tail_cyber_alerts(window_s: float = CYBER_ALERT_WINDOW_S,
                      limit: int = 20) -> list[dict[str, Any]]:
    """Свежие алерты из NDJSON (cyber_defense_monitor --log-file), новые сверху."""
    path = Path(CYBER_ALERTS_PATH)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    now = time.time()
    out: list[dict[str, Any]] = []
    for line in lines[-300:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if now - float(rec.get("ts", 0.0)) > window_s:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("ts", 0.0), reverse=True)
    return out[:limit]


def forward_to_gcs(path: str, payload: dict, timeout: float = 8.0) -> tuple[int, dict]:
    """POST payload → Web GCS path (напр. /api/goto, /api/command).

    Это сердце deep-integration A: команда из админ-витрины проксируется на
    пульт Web GCS, а тот шлёт её в MAVProxy/SITL. Браузер ходит только в
    admin (same-origin) — никакого CORS.
    """
    if not GCS_URL:
        return 503, {"ok": False, "error": "GCS не настроен (запусти admin с --gcs-url)"}
    url = GCS_URL.rstrip("/") + path
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "error": f"GCS HTTP {e.code}"}
        return e.code, body
    except (urllib.error.URLError, OSError) as e:
        return 502, {"ok": False, "error": f"GCS недоступен: {e}"}


def tile_grid_geojson(n_north: int, n_east: int, size_m: float) -> dict:
    """Сгенерить FeatureCollection с polygon для каждого tile'а сетки."""
    grid = TileGrid(
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
        tile_size_m=size_m, n_tiles_north=n_north, n_tiles_east=n_east,
    )
    features = []
    for tile in grid.iter_all():
        b = grid.bounds(tile)
        features.append({
            "type": "Feature",
            "id": tile.as_str(),
            "geometry": b.to_geojson_polygon(),
            "properties": {
                "tile_id": tile.as_str(),
                "i_north": tile.i, "j_east": tile.j,
                "n_min_m": b.n_min_m, "n_max_m": b.n_max_m,
                "e_min_m": b.e_min_m, "e_max_m": b.e_max_m,
            },
        })
    return {
        "total_tiles": grid.total_tiles,
        "total_area_km2": grid.total_area_km2,
        "coverage_km_north": grid.coverage_north_km,
        "coverage_km_east": grid.coverage_east_km,
        "tile_size_m": size_m,
        "geojson": {"type": "FeatureCollection", "features": features},
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
class AdminHandler(BaseHTTPRequestHandler):
    server_version = "BasAdmin/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet default access log.
        pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self) -> None:
        """Deep integration A — команды дрону ИЗ админ-витрины (→ Web GCS)."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/admin/control/command":
            payload = self._read_json()
            status, body = forward_to_gcs("/api/command", payload)
            log_activity("control_command", str(payload.get("action", "")))
            return self._send_json(body, status=status)
        if path == "/api/admin/control/goto":
            payload = self._read_json()
            status, body = forward_to_gcs("/api/goto", payload)
            log_activity(
                "control_goto",
                f"N={payload.get('north')} E={payload.get('east')}")
            return self._send_json(body, status=status)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # Static.
        if path == "/" or path == "/index.html":
            return self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._send_file(WEB_DIR / "app.js", "application/javascript")
        if path == "/styles.css":
            return self._send_file(WEB_DIR / "styles.css", "text/css")

        # API endpoints.
        if path == "/api/admin/health":
            return self._send_json({"ok": True, "ts": time.time()})

        if path == "/api/admin/issgr_url":
            return self._send_json({"url": ISSGR_URL or ""})

        if path == "/api/admin/config":
            return self._send_json({
                "issgr_url": ISSGR_URL or "",
                "has_onboard_db": bool(ONBOARD_DB),
                "has_sync_stats": bool(SYNC_STATS_URL),
                "sync_stats_url": SYNC_STATS_URL or "",
                "has_gcs": bool(GCS_URL),
                "gcs_url": GCS_URL or "",
                "origin_lat": ORIGIN_LAT,
                "origin_lon": ORIGIN_LON,
            })

        # Deep integration A: live состояние дрона из Web GCS (для пульта витрины).
        if path == "/api/admin/control_state":
            st = fetch_gcs("/api/state")
            return self._send_json({"ok": bool(st), "state": st,
                                    "has_gcs": bool(GCS_URL)})

        # Deep integration D: кибер-алерты от defense monitor (общий файл с пультом).
        if path == "/api/admin/alerts":
            alerts = tail_cyber_alerts()
            return self._send_json({"ok": True, "count": len(alerts),
                                    "alerts": alerts})

        if path == "/api/admin/collections":
            top = fetch_issgr("/collections")
            colls = (top.get("collections") or [])
            out: dict[str, int] = {}
            for c in colls:
                name = c.get("id") or c.get("name")
                if not name:
                    continue
                # ISSGR API возвращает numberMatched = len(returned features),
                # не "total available". Запросим с большим лимитом.
                fc = fetch_issgr(f"/collections/{name}/items?limit=10000")
                out[name] = len(fc.get("features") or [])
            return self._send_json(out)

        if path == "/api/admin/items":
            c = (qs.get("c") or ["uavs"])[0]
            limit = (qs.get("limit") or ["100"])[0]
            data = fetch_issgr(f"/collections/{c}/items?limit={limit}")
            return self._send_json(data)

        if path == "/api/admin/onboard_stats":
            if not ONBOARD_DB:
                return self._send_json({"error": "no on-board DB configured"}, status=404)
            s = ONBOARD_DB.stats()
            s["path"] = getattr(ONBOARD_DB, "path", ":memory:")
            return self._send_json(s)

        if path == "/api/admin/onboard_composite":
            if not ONBOARD_DB:
                return self._send_json({"metrics": [], "error": "no on-board DB"}, status=200)
            # Возвращаем latest per (sysid, metric_name) через SQL.
            with ONBOARD_DB._lock:
                rows = ONBOARD_DB._conn.execute(
                    """SELECT cs.sysid, cs.metric_name, cs.metric_value,
                              cs.extra_json, cs.ts_ms
                       FROM composite_state cs
                       WHERE cs.ts_ms = (
                         SELECT MAX(ts_ms) FROM composite_state
                         WHERE sysid=cs.sysid AND metric_name=cs.metric_name
                       )
                       ORDER BY cs.sysid, cs.metric_name"""
                ).fetchall()
            return self._send_json({
                "metrics": [
                    {"sysid": r[0], "metric_name": r[1], "metric_value": r[2],
                     "extra_json": r[3], "ts_ms": r[4]}
                    for r in rows
                ],
            })

        if path == "/api/admin/tile_grid":
            try:
                n = int((qs.get("n") or ["10"])[0])
                e = int((qs.get("e") or ["10"])[0])
                sz = float((qs.get("size") or ["2000"])[0])
            except ValueError:
                return self._send_json({"error": "bad params"}, status=400)
            n = max(1, min(50, n))
            e = max(1, min(50, e))
            sz = max(500.0, min(10_000.0, sz))
            return self._send_json(tile_grid_geojson(n, e, sz))

        if path == "/api/admin/sync_stats":
            if not SYNC_STATS_URL:
                return self._send_json({
                    "info": "sync stats URL not configured",
                    "hint": "запустите admin с --sync-stats-url=http://host:port/stats",
                })
            try:
                with urllib.request.urlopen(SYNC_STATS_URL, timeout=2.0) as r:
                    raw = r.read().decode("utf-8")
                return self._send_json(json.loads(raw))
            except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                return self._send_json({
                    "error": f"sync_publisher unreachable: {e}",
                    "url": SYNC_STATS_URL,
                }, status=502)

        if path == "/api/admin/activity":
            return self._send_json({"log": list(ACTIVITY)})

        self.send_error(HTTPStatus.NOT_FOUND, f"not found: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    global ISSGR_URL, GCS_URL, ONBOARD_DB, SYNC_STATS_URL
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8810)
    p.add_argument("--issgr-url",
                   help="ИССГР REST endpoint (e.g. http://127.0.0.1:8770)")
    p.add_argument("--gcs-url",
                   help="Web GCS endpoint для команд из витрины "
                        "(e.g. http://127.0.0.1:8765) — включает управление дроном")
    p.add_argument("--onboard-db", type=Path,
                   help="SQLite file для on-board metrics tab")
    p.add_argument("--sync-stats-url",
                   help="Multicast publisher /stats endpoint "
                        "(e.g. http://127.0.0.1:8811/stats)")
    args = p.parse_args()

    if args.issgr_url:
        ISSGR_URL = args.issgr_url.rstrip("/")
        log_activity("config", f"issgr={ISSGR_URL}")
    if args.gcs_url:
        GCS_URL = args.gcs_url.rstrip("/")
        log_activity("config", f"gcs={GCS_URL}")
    if args.onboard_db and HAS_ONBOARD:
        ONBOARD_DB = OnBoardDB(path=args.onboard_db)
        log_activity("config", f"onboard={args.onboard_db}")
    elif args.onboard_db:
        print(f"[warn] onboard requested but OnBoardDB import failed")
    if args.sync_stats_url:
        SYNC_STATS_URL = args.sync_stats_url
        log_activity("config", f"sync_stats={SYNC_STATS_URL}")

    server = ThreadingHTTPServer((args.host, args.port), AdminHandler)
    bind = server.server_address
    print(f"[admin] BAS Admin Dashboard at http://{bind[0]}:{bind[1]}/")
    print(f"        ISSGR proxy: {ISSGR_URL or '(none)'}")
    print(f"        On-board DB: {args.onboard_db or '(none)'}")
    print(f"        Sync stats:  {SYNC_STATS_URL or '(none)'}")
    print(f"        Static: {WEB_DIR}")
    log_activity("startup", f"listen={bind[0]}:{bind[1]}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("[admin] interrupted")
    finally:
        server.shutdown()
        server.server_close()
        if ONBOARD_DB:
            ONBOARD_DB.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
