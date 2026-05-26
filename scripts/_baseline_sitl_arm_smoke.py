#!/usr/bin/env python3
"""Baseline test: ArduCopter SITL built-in physics (not JsonFdmBridge).

Если SITL `--model X` без `json:` arms успешно → SITL+pymavlink работают
→ issue isolated к JsonFdmBridge data format/values.
"""
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path("/home/afetz/bas-prototype")
ARDUPILOT = Path("/home/afetz/ardupilot")
ARDUCOPTER = ARDUPILOT / "build/sitl/bin/arducopter"

PROCS: list[subprocess.Popen] = []


def cleanup(*_):
    for p in PROCS:
        try: p.send_signal(signal.SIGTERM)
        except Exception: pass
    time.sleep(1)
    for p in PROCS:
        try: p.kill()
        except Exception: pass


def main() -> int:
    print("[1] Launch ArduCopter SITL --model quad (built-in physics)")
    sitl_log = Path("/tmp/baseline_sitl.log")
    sitl = subprocess.Popen(
        [str(ARDUCOPTER), "--model", "quad", "--speedup", "1",
         "--defaults", str(ARDUPILOT / "Tools/autotest/default_params/copter.parm"),
         "--instance", "0", "-S"],
        stdout=sitl_log.open("wb"), stderr=subprocess.STDOUT,
        cwd=str(ARDUPILOT / "ArduCopter"),
    )
    PROCS.append(sitl)
    print(f"    SITL pid={sitl.pid}")
    time.sleep(2)

    # Wait TCP 5760.
    for _ in range(60):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(0.5)
        try: s.connect(("127.0.0.1", 5760)); s.close(); break
        except OSError: pass
        time.sleep(0.5)
    print("[2] TCP 5760 up")

    sys.path.insert(0, str(REPO / ".venv/lib/python3.12/site-packages"))
    from pymavlink import mavutil
    mav = mavutil.mavlink_connection("tcp:127.0.0.1:5760", source_system=255)
    hb_stop = threading.Event()

    def _hb_loop():
        while not hb_stop.is_set():
            try:
                mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0,
                )
            except Exception:
                pass
            time.sleep(1)

    threading.Thread(target=_hb_loop, daemon=True).start()
    hb = mav.wait_heartbeat(timeout=30)
    if not hb:
        print("[!] no HEARTBEAT")
        cleanup(); return 1
    print(f"[3] HEARTBEAT sysid={mav.target_system}")

    # Request streams.
    mav.mav.request_data_stream_send(mav.target_system, mav.target_component, 0, 10, 1)

    # Wait valid global position.
    print("[4] wait valid GPS/global position + EKF...")
    t0 = time.time()
    gps_ok = False
    while time.time() - t0 < 45:
        msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1.0)
        if msg and msg.lat != 0:
            print(f"    ✓ GPS lock at t+{time.time()-t0:.1f}s lat={msg.lat/1e7:.6f}")
            gps_ok = True
            break
    if not gps_ok:
        print("    [!] no valid global position")
        cleanup(); return 1

    # ARM.
    print("[5] mode=STABILIZE → arm (force) → RC throttle takeoff")
    mav.set_mode_apm("STABILIZE")
    time.sleep(1)
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        400, 0, 1, 21196, 0, 0, 0, 0, 0,
    )
    armed = False
    t0 = time.time()
    arm_statuses = []
    while time.time() - t0 < 30:
        msg = mav.recv_match(blocking=True, timeout=0.5)
        if not msg: continue
        mt = msg.get_type()
        if mt == "HEARTBEAT" and (msg.base_mode & 0x80):
            armed = True; break
        if mt == "STATUSTEXT":
            txt = msg.text if isinstance(msg.text, str) else msg.text.decode("utf-8", "ignore")
            arm_statuses.append(txt)
    base_alt = 0.0
    max_alt = 0.0
    if armed:
        print(f"    ✓ ARMED at t+{time.time()-t0:.1f}s")
        t0 = time.time()
        base_alt = None
        max_alt = -999.0
        while time.time() - t0 < 20:
            mav.mav.rc_channels_override_send(
                mav.target_system, mav.target_component,
                1500, 1500, 1800, 1500, 0, 0, 0, 0,
            )
            msg = mav.recv_match(blocking=True, timeout=0.2)
            if msg and msg.get_type() == "GLOBAL_POSITION_INT":
                alt = msg.relative_alt / 1000.0
                if base_alt is None:
                    base_alt = alt
                max_alt = max(max_alt, alt)
                if max_alt - base_alt > 0.8:
                    print(f"    ✓ takeoff at t+{time.time()-t0:.1f}s delta={max_alt-base_alt:.2f}m")
                    break
        base_alt = base_alt if base_alt is not None else 0.0
        print(f"    Max alt delta: {max_alt-base_alt:.2f}m")
    else:
        print(f"    [!] not armed; last statuses: {arm_statuses[-5:]}")

    hb_stop.set()
    cleanup()
    return 0 if armed and max_alt - (base_alt or 0.0) > 0.8 else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cleanup); signal.signal(signal.SIGINT, cleanup)
    try: sys.exit(main())
    finally: cleanup()
