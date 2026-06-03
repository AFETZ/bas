#!/usr/bin/env python3
"""Smoke: CARLA наземный транспорт (kinematic режим) → ИССГР-payload.

Offline/CI-friendly и БЕЗ тяжёлых зависимостей: вместо реального ИССГР-сервера
(FastAPI/uvicorn — нет в CI offline-окружении) поднимает крошечный stdlib
http.server-стаб, который принимает upsert-POST'ы от carla_ground_vehicle.py.
CARLA-движок гоняется в kinematic-режиме (велосипедная модель, без GPU/сервера).

Проверяет контракт движка CARLA:
  [A] машина шлёт upsert как operational_situation.ground_vehicle.*
  [B] ручное управление: flight_mode=MANUAL, armed=True
  [C] машина РЕАЛЬНО едет: смещение первый→последний замер > 10 м

Это доказывает, что движок CARLA (kinematic-путь) даёт тот же ИССГР-контракт,
что и ArduRover. Реальный ИССГР-сервер тестируется в live-наборе; live-путь
CARLA (реальный сервер :2000) — при наличии GPU-хоста.
"""
import http.server
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 8779   # тестовый стаб (не пересекается с демо :8770)

# Принятые upsert'ы (заполняется HTTP-стабом в фоне).
_received: list[dict] = []


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Принимает POST /collections/uavs/items и пишет payload в _received."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            _received.append(json.loads(body))
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_):   # тишина
        pass


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def ground_rows():
    """Замеры наземной машины из принятых upsert'ов."""
    out = []
    for p in _received:
        if "ground_vehicle" in p.get("issgr_class", ""):
            pose = p.get("pose") or {}
            out.append((pose.get("latitude_deg"), pose.get("longitude_deg"), p))
    return out


def main() -> int:
    print("[A] поднимаем stdlib ИССГР-стаб :%d" % PORT)
    httpd = http.server.HTTPServer(("127.0.0.1", PORT), _StubHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{PORT}"
    print("    ✓ стаб up")

    try:
        print("[B] carla_ground_vehicle.py --mode kinematic (12s)")
        rc = subprocess.call(
            [sys.executable, str(REPO / "scripts/carla_ground_vehicle.py"),
             "--mode", "kinematic", "--issgr-url", url,
             "--seconds", "12", "--period-s", "0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"    backend завершился rc={rc}")

        rows = ground_rows()
        if len(rows) < 2:
            print(f"    [!] недостаточно замеров наземной машины ({len(rows)})")
            return 1

        first, last = rows[0], rows[-1]
        moved = haversine_m(first[0], first[1], last[0], last[1])
        props = last[2]
        is_ground = "ground_vehicle" in props.get("issgr_class", "")
        is_manual = props.get("flight_mode") == "MANUAL"
        is_armed = bool(props.get("armed"))
        print(f"    upserts={len(rows)} проехала={moved:.1f}м "
              f"class_ok={is_ground} manual={is_manual} armed={is_armed}")

        print("\n=====================================================")
        print("  CARLA наземный транспорт (kinematic) → ИССГР-payload")
        print("=====================================================")
        print(f"  движок:   carla (kinematic bicycle model)")
        print(f"  класс:    {props.get('issgr_class')}")
        print(f"  режим:    {props.get('flight_mode')} armed={is_armed}")
        print(f"  upserts:  {len(rows)}")
        print(f"  проехала: {moved:.1f} м")
        print()
        if is_ground and is_manual and is_armed and moved > 10.0:
            print("  ALL CHECKS PASSED — CARLA-движок: ground_vehicle, MANUAL, едет")
            return 0
        print("  CHECK FAILED")
        return 1
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
