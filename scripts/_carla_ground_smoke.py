#!/usr/bin/env python3
"""Smoke: CARLA наземный транспорт (kinematic режим) → ИССГР.

Offline/CI-friendly: БЕЗ CARLA сервера и GPU — велосипедная модель машины.
Поднимает ИССГР, гоняет `carla_ground_vehicle.py --mode kinematic`, проверяет:
  [A] машина появилась в ИССГР как operational_situation.ground_vehicle.*
  [B] ручное управление: flight_mode=MANUAL, armed=True
  [C] машина РЕАЛЬНО едет: смещение между первым и последним замером > 10 м

Это доказывает, что движок CARLA (kinematic-путь) даёт тот же контракт в ИССГР,
что и ArduRover. Live-путь (реальный CARLA сервер) проверяется отдельно при
наличии GPU-хоста.
"""
import json
import math
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV = REPO / ".venv/bin/python"
PORT = 8779   # тестовый ИССГР (не пересекается с демо :8770)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def get_ground_vehicle(url):
    try:
        with urllib.request.urlopen(
                url + "/collections/uavs/items?limit=20", timeout=3) as r:
            d = json.load(r)
    except Exception:
        return None
    for f in d.get("features", []):
        props = f.get("properties") or {}
        if "ground_vehicle" in props.get("issgr_class", ""):
            c = f["geometry"]["coordinates"]
            return (c[1], c[0], props)
    return None


def main() -> int:
    procs = []
    url = f"http://127.0.0.1:{PORT}"
    try:
        print("[A] поднимаем ИССГР")
        issgr = subprocess.Popen(
            [str(VENV), str(REPO / "scripts/issgr_api_server.py"),
             "--port", str(PORT), "--seed-profile", "urban"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(issgr)
        ready = False
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "/collections", timeout=1)
                ready = True
                break
            except Exception:
                time.sleep(0.1)
        if not ready:
            print("    [!] ИССГР не поднялся")
            return 1
        print("    ✓ ИССГР up")

        print("[B] carla_ground_vehicle.py --mode kinematic (14s)")
        drive = subprocess.Popen(
            [str(VENV), str(REPO / "scripts/carla_ground_vehicle.py"),
             "--mode", "kinematic", "--issgr-url", url,
             "--seconds", "14", "--period-s", "0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(drive)

        # Первый замер позиции (после старта движения).
        first = None
        t0 = time.time()
        while time.time() - t0 < 6 and first is None:
            time.sleep(0.5)
            first = get_ground_vehicle(url)
        if first is None:
            print("    [!] машина не появилась в ИССГР")
            return 1
        print(f"    ✓ машина в ИССГР: класс={first[2].get('issgr_class')} "
              f"mode={first[2].get('flight_mode')} armed={first[2].get('armed')}")

        drive.wait(timeout=30)
        last = get_ground_vehicle(url)
        if last is None:
            print("    [!] машина пропала из ИССГР")
            return 1

        moved = haversine_m(first[0], first[1], last[0], last[1])
        is_ground = "ground_vehicle" in last[2].get("issgr_class", "")
        is_manual = last[2].get("flight_mode") == "MANUAL"
        is_armed = bool(last[2].get("armed"))
        print(f"    проехала {moved:.1f}м; class_ok={is_ground} "
              f"manual={is_manual} armed={is_armed}")

        print("\n=====================================================")
        print("  CARLA наземный транспорт (kinematic) → ИССГР")
        print("=====================================================")
        print(f"  движок:   carla (kinematic bicycle model)")
        print(f"  класс:    {last[2].get('issgr_class')}")
        print(f"  режим:    {last[2].get('flight_mode')} armed={is_armed}")
        print(f"  проехала: {moved:.1f} м")
        print()
        if is_ground and is_manual and is_armed and moved > 10.0:
            print("  ALL CHECKS PASSED — машина в ИССГР, ручное управление, едет")
            return 0
        print("  CHECK FAILED")
        return 1
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
