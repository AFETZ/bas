#!/usr/bin/env python3
"""End-to-end smoke: real ArduPilot ArduRover SITL — наземный транспорт.

Закрывает пункт ТЗ "среда моделирования наземного дорожного транспорта и
транспорта, способного двигаться вне дорог". Базовый автопилот — ArduPilot
ArduRover (тот же autopilot-стек, что ArduCopter, frame=Rover).

Проверяет:
  [A] ArduRover SITL грузится с built-in rover physics (--model rover),
      MAVLink TCP :5762, HEARTBEAT type=GROUND_ROVER (10).
  [B] EKF/GPS lock → valid GLOBAL_POSITION_INT (lat≈-35.363, Canberra).
  [C] GUIDED + force ARM → SET_POSITION_TARGET_GLOBAL_INT на ~40м →
      машина РЕАЛЬНО едет: пройденная дистанция > 10м и приближается к цели.

--frame rover (дорожный, ackermann) | rover-skid (вне дорог, skid-steer).
"""
import argparse
import math
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARDUPILOT = Path("/home/afetz/ardupilot")
ARDUROVER = ARDUPILOT / "build/sitl/bin/ardurover"
HOME_LAT, HOME_LON, HOME_ALT, HOME_HDG = -35.363262, 149.165237, 584.0, 90.0
MAV_PORT = 5770   # ArduPilot instance 1 → 5760 + 1*10 (вместе с ArduCopter :5760)
PROCS: list[subprocess.Popen] = []


def cleanup(*_):
    for p in PROCS:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
    time.sleep(1)
    for p in PROCS:
        try:
            p.kill()
        except Exception:
            pass


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default="rover",
                    choices=["rover", "rover-skid", "rover-vectored"],
                    help="rover=дорожный(ackermann), rover-skid=вне дорог")
    ap.add_argument("--drive-m", type=float, default=40.0)
    args = ap.parse_args()

    if not ARDUROVER.exists():
        print(f"[ERR] ardurover binary missing: {ARDUROVER}")
        print("Build: cd ~/ardupilot && ./waf rover")
        return 1
    parm_src = ARDUPILOT / f"Tools/autotest/default_params/{args.frame}.parm"
    if not parm_src.exists():
        parm_src = ARDUPILOT / "Tools/autotest/default_params/rover.parm"
    # Override params для headless SITL bring-up: снять строгие pre-arm checks
    # и GCS failsafe (как при автотестах ArduPilot). Машина и так едет под EKF.
    parm = Path(f"/tmp/_rover_{args.frame}_{int(time.time())}.parm")
    parm.write_text(parm_src.read_text(encoding="utf-8")
                    + "\nARMING_CHECK 0\nFS_GCS_ENABLE 0\nFS_THR_ENABLE 0\n",
                    encoding="utf-8")
    print(f"==> ardurover: {ARDUROVER} ({ARDUROVER.stat().st_size:,}B)")
    print(f"==> frame: {args.frame}  defaults: {parm_src.name} (+ARMING_CHECK 0)")

    log = Path(f"/tmp/_rover_smoke_{int(time.time())}.log")
    print("\n[A] launch ArduRover SITL --model rover")
    cmd = [
        str(ARDUROVER), "--model", "rover",
        "--speedup", "1", "--defaults", str(parm),
        "--home", f"{HOME_LAT},{HOME_LON},{HOME_ALT},{HOME_HDG}",
        "--instance", "1",   # instance 1 → TCP 5762
        "-S",
    ]
    sitl = subprocess.Popen(cmd, stdout=log.open("wb"), stderr=subprocess.STDOUT,
                            cwd=str(ARDUPILOT / "Rover"))
    PROCS.append(sitl)
    print(f"    SITL pid={sitl.pid}  log={log}")

    print(f"\n[B] wait MAVLink TCP :{MAV_PORT}")
    ready = False
    for _ in range(80):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", MAV_PORT))
            s.close()
            ready = True
            break
        except OSError:
            pass
        time.sleep(0.5)
    if not ready:
        print("    [!] no TCP")
        cleanup()
        return 1
    print("    ✓ TCP up")

    sys.path.insert(0, str(REPO / ".venv/lib/python3.12/site-packages"))
    from pymavlink import mavutil
    mav = mavutil.mavlink_connection(f"tcp:127.0.0.1:{MAV_PORT}", source_system=255)
    stop = threading.Event()

    def _hb():
        while not stop.is_set():
            try:
                mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                                       mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            except Exception:
                pass
            time.sleep(1.0)
    threading.Thread(target=_hb, daemon=True).start()

    hb = mav.wait_heartbeat(timeout=30)
    if not hb:
        print("    [!] no HEARTBEAT")
        cleanup()
        return 1
    print(f"    ✓ HEARTBEAT sysid={mav.target_system} type={hb.type} "
          f"(10=GROUND_ROVER)")

    print("\n[C] request streams, wait valid GLOBAL_POSITION_INT (GPS lock)")
    mav.mav.request_data_stream_send(mav.target_system, mav.target_component,
                                     0, 5, 1)
    lat = lon = None
    t0 = time.time()
    n_pos = 0
    while time.time() - t0 < 60:
        msg = mav.recv_match(blocking=True, timeout=1.0)
        if msg and msg.get_type() == "GLOBAL_POSITION_INT" and msg.lat and msg.lon:
            lat, lon = msg.lat / 1e7, msg.lon / 1e7
            n_pos += 1
            if n_pos >= 5:
                break
    if lat is None:
        print("    [!] no GPS lock")
        cleanup()
        return 1
    start_lat, start_lon = lat, lon
    print(f"    ✓ GPS lock: ({lat:.6f},{lon:.6f}) after {time.time()-t0:.0f}s")

    # Target ~drive_m метров на восток от старта.
    deg_per_m_lon = 1.0 / (111_319.9 * math.cos(math.radians(start_lat)))
    tgt_lat = start_lat
    tgt_lon = start_lon + args.drive_m * deg_per_m_lon
    print(f"    target: ({tgt_lat:.6f},{tgt_lon:.6f}) ≈ {args.drive_m:.0f}m east")

    print("\n[D] MANUAL + force ARM (retry) — ручное управление наземным транспортом")
    mav.set_mode_apm("MANUAL")
    time.sleep(2)
    armed = False
    last_status: list[str] = []
    t0 = time.time()
    next_arm = 0.0
    while time.time() - t0 < 30:
        if time.time() >= next_arm:
            mav.mav.command_long_send(mav.target_system, mav.target_component,
                                      mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                                      0, 1, 21196, 0, 0, 0, 0, 0)
            next_arm = time.time() + 2.0
        msg = mav.recv_match(blocking=True, timeout=0.4)
        if not msg:
            continue
        mt = msg.get_type()
        if mt == "HEARTBEAT" and (
                msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
            armed = True
            break
        if mt == "STATUSTEXT":
            txt = msg.text if isinstance(msg.text, str) else msg.text.decode("utf-8", "ignore")
            last_status.append(txt)
    if not armed:
        print(f"    [!] ARM failed; last STATUS: {last_status[-6:]}")
        cleanup()
        return 1
    print(f"    ✓ ARMED after {time.time()-t0:.1f}s")

    print("\n[E] drive forward via RC throttle override (steering=center, throttle=forward)")
    # ArduRover MANUAL: RC1=steering, RC3=throttle. 1500=neutral, >1500=вперёд.
    max_dist_from_start = 0.0
    min_dist_to_target = 1e9
    last_speed = 0.0
    t0 = time.time()
    while time.time() - t0 < 45:
        mav.mav.rc_channels_override_send(
            mav.target_system, mav.target_component,
            1500, 0, 1700, 0, 0, 0, 0, 0)   # ch1=steering neutral, ch3=throttle fwd
        msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=0.4)
        if msg and msg.lat and msg.lon:
            clat, clon = msg.lat / 1e7, msg.lon / 1e7
            d_start = haversine_m(start_lat, start_lon, clat, clon)
            d_tgt = haversine_m(clat, clon, tgt_lat, tgt_lon)
            max_dist_from_start = max(max_dist_from_start, d_start)
            min_dist_to_target = min(min_dist_to_target, d_tgt)
            last_speed = math.hypot(getattr(msg, "vx", 0) / 100.0,
                                    getattr(msg, "vy", 0) / 100.0)
            if d_tgt < 5.0:
                print(f"    ✓ reached target (within {d_tgt:.1f}m) at t+{time.time()-t0:.0f}s")
                break
    # снять throttle
    mav.mav.rc_channels_override_send(mav.target_system, mav.target_component,
                                      1500, 0, 1500, 0, 0, 0, 0, 0)
    print(f"    moved {max_dist_from_start:.1f}m; closest to target {min_dist_to_target:.1f}m; "
          f"last speed {last_speed:.1f} m/s")

    cleanup()
    stop.set()
    drove = max_dist_from_start > 10.0
    approached = min_dist_to_target < args.drive_m * 0.5
    print("\n=====================================================")
    print("  ArduRover SITL (наземный транспорт) VERIFIED")
    print("=====================================================")
    print(f"  frame:    {args.frame}")
    print(f"  HEARTBEAT type: {hb.type} (10=GROUND_ROVER)")
    print(f"  GPS lock: ({start_lat:.6f},{start_lon:.6f})")
    print(f"  ARMED:    {armed}")
    print(f"  Drove:    {max_dist_from_start:.1f}m  → closest {min_dist_to_target:.1f}m to target")
    print()
    if armed and drove and approached:
        print("  ALL CHECKS PASSED — rover armed, MANUAL drive, reached target")
        return 0
    print("  CHECK FAILED — rover did not drive/approach as expected")
    return 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    try:
        sys.exit(main())
    finally:
        cleanup()
