#!/usr/bin/env python3
"""Ручное управление наземным транспортом (ArduRover) через RC override.

Демонстрирует ручное вождение машины: MANUAL mode + force ARM + RC throttle/
steering. Едет вперёд с лёгким S-маневром (видно траекторию в двойнике),
потом отпускает throttle. Используется в run_stage_5_ground_vehicle_demo.sh.

Usage:
  ./.venv/bin/python scripts/rover_manual_drive.py \
      --mavlink tcp:127.0.0.1:5770 --seconds 30
"""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".venv/lib/python3.12/site-packages"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ArduRover ручное вождение")
    ap.add_argument("--mavlink", default="tcp:127.0.0.1:5770")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--throttle", type=int, default=1700)
    args = ap.parse_args(argv)

    from pymavlink import mavutil
    # КРИТИЧНО: source_system=255 = SYSID_MYGCS по умолчанию. ArduPilot
    # принимает RC_CHANNELS_OVERRIDE ТОЛЬКО от GCS с sysid == SYSID_MYGCS;
    # с любым другим sysid машина армится, но газ игнорируется и она не едет.
    mav = mavutil.mavlink_connection(args.mavlink, source_system=255)
    mav.wait_heartbeat(timeout=30)
    mav.mav.request_data_stream_send(mav.target_system, mav.target_component,
                                     0, 4, 1)

    # КЛЮЧЕВОЕ: шлём GCS HEARTBEAT в фоне (1 Гц). Без живого GCS-линка
    # ArduPilot считает RC_CHANNELS_OVERRIDE устаревшим и НЕ применяет газ —
    # машина армится, но не едет. Этот поток держит линк живым.
    stop = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop.is_set():
            try:
                mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            except Exception:
                pass
            time.sleep(1.0)

    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    # Ждём GPS lock.
    t0 = time.time()
    while time.time() - t0 < 60:
        m = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1.0)
        if m and m.lat and m.lon:
            break
    print(f"[drive] GPS ok, MANUAL + ARM", flush=True)

    mav.set_mode_apm("MANUAL")
    time.sleep(1.5)
    t0 = time.time()
    armed = False
    while time.time() - t0 < 25 and not armed:
        mav.mav.command_long_send(
            mav.target_system, mav.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, 1, 21196, 0, 0, 0, 0, 0)
        m = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1.5)
        if m and (m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
            armed = True
    if not armed:
        print("[drive] ARM failed", flush=True)
        return 1
    print(f"[drive] ARMED — еду {args.seconds:.0f}s (throttle={args.throttle})",
          flush=True)

    t0 = time.time()
    while time.time() - t0 < args.seconds:
        # Лёгкий S-маневр: steering колеблется ±150 вокруг центра.
        steer = 1500 + int(150 * math.sin((time.time() - t0) * 0.5))
        mav.mav.rc_channels_override_send(
            mav.target_system, mav.target_component,
            steer, 0, args.throttle, 0, 0, 0, 0, 0)
        # Прокачиваем входящий буфер, чтобы линк оставался здоровым.
        mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=0.2)
    # Стоп.
    mav.mav.rc_channels_override_send(
        mav.target_system, mav.target_component, 1500, 0, 1500, 0, 0, 0, 0, 0)
    stop.set()
    print("[drive] done (throttle released)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
