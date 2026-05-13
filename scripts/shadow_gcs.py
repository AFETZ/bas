"""Shadow GCS для этапа 1.5.0.

Запускается ВНУТРИ netns bas-ctrl-far и подключается к SITL через ns-3 радио-канал
(адрес host-gateway 10.10.0.99:5760 на br-ctrl-near). Пишет полученные MAVLink
сообщения в JSONL, добавляя расчётное wall-задержку между tx и rx (где tx-время
есть в сообщении SYSTEM_TIME/SCALED_PRESSURE/etc).

Назначение: сравнить с host-side MAVLink-листенером (который тоже подключён к
тому же SITL, но напрямую через 127.0.0.1:5760) — увидеть как ns-3 деградирует
телеметрию.

Использование:
    python3 shadow_gcs.py --endpoint tcp:10.10.0.99:5760 --out logs/<run_id>/shadow_gcs.jsonl --duration 180
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from pymavlink import mavutil


def write_event(fp, event: dict) -> None:
    fp.write(json.dumps(event, ensure_ascii=False) + "\n")
    fp.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="tcp:10.10.0.99:5760",
                        help="MAVLink endpoint для подключения (через ns-3 радио).")
    parser.add_argument("--out", required=True, type=Path,
                        help="Путь к JSONL для shadow событий.")
    parser.add_argument("--duration", type=float, default=180.0,
                        help="Сколько секунд слушать (потом выход).")
    parser.add_argument("--source-system", type=int, default=200,
                        help="MAVLink source_system для GCS (отличается от host-GCS 254).")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fp = args.out.open("w", encoding="utf-8")

    write_event(fp, {
        "event_type": "component", "component": "shadow_gcs", "phase": "connecting",
        "endpoint": args.endpoint, "wall_time": time.time(),
    })
    try:
        mav = mavutil.mavlink_connection(args.endpoint, source_system=args.source_system)
    except Exception as exc:
        write_event(fp, {
            "event_type": "component", "component": "shadow_gcs", "phase": "connect_failed",
            "error": repr(exc), "wall_time": time.time(),
        })
        fp.close()
        return 1

    # HEARTBEAT приходит первым делом.
    deadline = time.time() + 30
    hb = None
    while time.time() < deadline:
        hb = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
        if hb is not None:
            break
    if hb is None:
        write_event(fp, {
            "event_type": "component", "component": "shadow_gcs", "phase": "no_heartbeat",
            "wall_time": time.time(),
        })
        fp.close()
        return 2

    write_event(fp, {
        "event_type": "component", "component": "shadow_gcs", "phase": "connected",
        "sys_id": hb.get_srcSystem(), "comp_id": hb.get_srcComponent(),
        "wall_time": time.time(),
    })

    # Запросить максимальную частоту телеметрии.
    mav.mav.request_data_stream_send(
        hb.get_srcSystem(), hb.get_srcComponent(),
        mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1,
    )

    start = time.time()
    msg_count = {}
    end = start + args.duration
    last_pos = None

    while time.time() < end:
        msg = mav.recv_match(blocking=True, timeout=0.5)
        if msg is None:
            continue
        t = msg.get_type()
        msg_count[t] = msg_count.get(t, 0) + 1
        now = time.time()

        if t == "HEARTBEAT":
            write_event(fp, {
                "event_type": "control_telemetry",
                "shadow": True,
                "sim_time": now - start,
                "wall_time": now,
                "message_type": "HEARTBEAT",
                "sys_id": msg.get_srcSystem(),
                "base_mode": int(msg.base_mode),
                "custom_mode": int(msg.custom_mode),
                "flight_mode": mavutil.mode_string_v10(msg),
            })
        elif t == "GLOBAL_POSITION_INT":
            pos = {
                "lat": msg.lat / 1e7,
                "lon": msg.lon / 1e7,
                "alt_amsl_m": msg.alt / 1000.0,
                "alt_rel_m": msg.relative_alt / 1000.0,
            }
            last_pos = pos
            write_event(fp, {
                "event_type": "flight",
                "shadow": True,
                "sim_time": now - start,
                "wall_time": now,
                "vehicle_id": "uav-1",
                "position": pos,
                "velocity_mps": {"vx": msg.vx / 100.0, "vy": msg.vy / 100.0, "vz": msg.vz / 100.0},
                "heading_deg": msg.hdg / 100.0 if msg.hdg != 65535 else None,
            })
        elif t == "STATUSTEXT":
            text = msg.text.decode("utf-8", errors="replace") if isinstance(msg.text, bytes) else str(msg.text)
            write_event(fp, {
                "event_type": "control_telemetry",
                "shadow": True,
                "sim_time": now - start,
                "wall_time": now,
                "message_type": "STATUSTEXT",
                "severity": int(msg.severity),
                "text": text,
            })

    # Финальный summary.
    write_event(fp, {
        "event_type": "component", "component": "shadow_gcs", "phase": "summary",
        "wall_time": time.time(),
        "duration_s": time.time() - start,
        "messages_received": sum(msg_count.values()),
        "by_type": dict(sorted(msg_count.items(), key=lambda x: -x[1])),
        "last_position": last_pos,
    })
    fp.close()
    mav.close()
    print(f"shadow_gcs: {sum(msg_count.values())} messages over {time.time() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
