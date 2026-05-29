#!/usr/bin/env python3
"""End-to-end smoke: real ArduPilot SITL `-f JSON` ↔ JsonFdmBridge.

Verifies следующие гарантии (закрывает limitation "ArduPilot SITL e2e
не verified"):

  [A] Wire protocol: SITL → 40-byte binary servo_packet_16 ↔ Bridge → JSON
      sensor packet (null-terminated, all mandatory fields). Bridge
      parses корректно, SITL accepts ("JSON received:
      timestamp imu:gyro imu:accel_body position quaternion velocity").
  [B] SITL boots в normal mode: HEARTBEAT, EKF init, GPS lock, ATTITUDE,
      GLOBAL_POSITION_INT все опубликованы через MAVLink TCP :5760.
  [C] Physics → SITL → MAVLink выдают consistent telemetry (drone на
      ground: alt_msl ≈ 584m, vel ≈ 0).
  [D] Real ArduPilot ARM + manual takeoff: STABILIZE → force ARM →
      RC throttle override → bridge receives motor PWM > hover and
      MAVLink GLOBAL_POSITION_INT.relative_alt rises above 0.5m.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARDUPILOT = Path("/home/afetz/ardupilot")
ARDUCOPTER = ARDUPILOT / "build/sitl/bin/arducopter"
LOG_DIR = Path(f"/tmp/_real_sitl_smoke_{int(time.time())}")
LOG_DIR.mkdir(parents=True, exist_ok=True)
PYTHON = REPO / ".venv/bin/python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

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


def main() -> int:
    if not ARDUCOPTER.exists():
        print(f"[ERR] arducopter binary missing: {ARDUCOPTER}")
        print("Run: bash scripts/install_ardupilot.sh")
        return 1
    print(f"==> arducopter: {ARDUCOPTER} ({ARDUCOPTER.stat().st_size:,}B)")
    print(f"==> JsonFdmBridge: {REPO}/scripts/arducopter_airsim_interface.py")
    print(f"==> python: {PYTHON}")
    print(f"==> logs: {LOG_DIR}")

    defaults_src = ARDUPILOT / "Tools/autotest/default_params/copter.parm"
    defaults_path = LOG_DIR / "copter_json_x.parm"
    defaults_text = defaults_src.read_text(encoding="utf-8")
    defaults_path.write_text(
        defaults_text
        + "\n# BAS JsonFdmBridge X-frame physics override\n"
        + "FRAME_CLASS 1\n"
        + "FRAME_TYPE 1\n"
        + "BRD_SAFETY_DEFLT 0\n",
        encoding="utf-8",
    )
    print(f"==> defaults: {defaults_path} (FRAME_TYPE=1/X)")

    # --- [A] Launch JsonFdmBridge first ---
    print("\n[A] Launch JsonFdmBridge (port 9002, real X-config 6DOF physics)")
    bridge_log = LOG_DIR / "bridge.log"
    bridge = subprocess.Popen(
        [str(PYTHON), str(REPO / "scripts/arducopter_airsim_interface.py"),
         "--mode=json_fdm", "--ardupilot-frame=json",
         "--no-airsim",
         "--log-file", str(LOG_DIR / "bridge_frames.jsonl"),
         "--max-seconds", "120"],
        stdout=bridge_log.open("wb"), stderr=subprocess.STDOUT,
    )
    PROCS.append(bridge)
    time.sleep(2)
    print(f"    bridge pid={bridge.pid}  log={bridge_log}")

    # --- Launch ArduCopter SITL с JSON frame ---
    print("\n[B] Launch arducopter SITL `--model json:127.0.0.1`")
    sitl_log = LOG_DIR / "sitl.log"
    cmd = [
        str(ARDUCOPTER),
        "--model", "json:127.0.0.1",
        "--speedup", "1",
        "--defaults", str(defaults_path),
        "--instance", "0",
        "-S",
    ]
    sitl = subprocess.Popen(
        cmd, stdout=sitl_log.open("wb"), stderr=subprocess.STDOUT,
        cwd=str(ARDUPILOT / "ArduCopter"),
    )
    PROCS.append(sitl)
    print(f"    SITL pid={sitl.pid}  log={sitl_log}")

    # --- Wait for MAVLink TCP 5760 ---
    print("\n[C] Wait for SITL MAVLink TCP :5760...")
    t0 = time.time()
    mav_ready = False
    for _ in range(60):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", 5760))
            s.close()
            mav_ready = True
            break
        except OSError:
            pass
        time.sleep(0.5)
    if not mav_ready:
        print("    [!] SITL didn't open TCP 5760 in 30s")
        cleanup()
        return 1
    dt = time.time() - t0
    print(f"    ✓ TCP 5760 up at t+{dt:.1f}s")

    # --- pymavlink connect + HEARTBEAT ---
    print("\n[D] pymavlink connect tcp:127.0.0.1:5760, wait HEARTBEAT")
    sys.path.insert(0, str(REPO / ".venv/lib/python3.12/site-packages"))
    from pymavlink import mavutil
    mav = mavutil.mavlink_connection("tcp:127.0.0.1:5760", source_system=255)
    hb_stop = threading.Event()

    def _gcs_heartbeat_loop() -> None:
        while not hb_stop.is_set():
            try:
                mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0,
                )
            except Exception:
                pass
            time.sleep(1.0)

    threading.Thread(target=_gcs_heartbeat_loop, daemon=True).start()
    hb = mav.wait_heartbeat(timeout=30)
    if not hb:
        print("    [!] no HEARTBEAT received")
        cleanup()
        return 1
    print(f"    ✓ HEARTBEAT: sysid={mav.target_system} type={hb.type} "
          f"autopilot={hb.autopilot} mode={hb.custom_mode}")

    # --- Request streams + wait EKF/GPS ready ---
    print("\n[E] Request MAV_DATA_STREAM_ALL @ 10Hz, wait ATTITUDE + valid GLOBAL_POSITION_INT")
    mav.mav.request_data_stream_send(
        mav.target_system, mav.target_component,
        0, 10, 1,
    )
    t0 = time.time()
    has_attitude = False
    has_position = False
    last_alt = None
    last_lat = None
    n_attitude = 0
    n_position = 0
    while time.time() - t0 < 45:
        msg = mav.recv_match(blocking=True, timeout=1.0)
        if not msg:
            continue
        mt = msg.get_type()
        if mt == "ATTITUDE":
            has_attitude = True
            n_attitude += 1
        elif mt == "GLOBAL_POSITION_INT":
            if msg.lat != 0 and msg.lon != 0:
                has_position = True
                n_position += 1
                last_alt = msg.alt / 1000.0
                last_lat = msg.lat / 1e7
        if has_attitude and has_position and n_attitude >= 5 and n_position >= 5:
            break
    print(f"    ✓ ATTITUDE: {n_attitude} msgs  valid GLOBAL_POSITION_INT: {n_position} msgs")
    if last_lat is not None:
        print(f"    ✓ lat={last_lat:.6f} (expected ≈-35.363262)  alt={last_alt:.2f}m (expected ≈584)")
    if not (has_attitude and has_position):
        print("    [!] EKF не выдал valid telemetry в 45s — bridge wire format broken?")
        cleanup()
        return 1

    # --- Verify bridge actually exchanged frames ---
    print("\n[F] Verify bridge frame_count > 100 (PWM round-trip с SITL)")
    bridge_frames_path = LOG_DIR / "bridge_frames.jsonl"
    if not bridge_frames_path.exists() or bridge_frames_path.stat().st_size == 0:
        print(f"    [!] bridge frames log empty — bridge не получал PWM от SITL")
        cleanup()
        return 1
    n_frames = sum(1 for _ in bridge_frames_path.open())
    print(f"    ✓ bridge_frames.jsonl: {n_frames} entries (1 entry = 240 PWM frames @ 1Hz log rate)")
    # Last frame contains pos/attitude info.
    last_frame_line = bridge_frames_path.read_text().strip().splitlines()[-1]
    last_frame = json.loads(last_frame_line)
    print(f"    ✓ latest physics state: pos={last_frame.get('pos')} "
          f"att_deg={last_frame.get('att_deg')} on_ground={last_frame.get('on_ground')}")

    # --- [G] Full arm + manual takeoff через real ArduPilot outputs ---
    print("\n[G] STABILIZE → force ARM → RC throttle → verify altitude>0.5m")
    print("    waiting EKF origin / tilt alignment status...")
    status_texts: list[str] = []
    ekf_origin_seen = has_position
    if not ekf_origin_seen:
        t0 = time.time()
        while time.time() - t0 < 45:
            msg = mav.recv_match(blocking=True, timeout=1.0)
            if not msg:
                continue
            if msg.get_type() == "STATUSTEXT":
                txt = msg.text if isinstance(msg.text, str) else msg.text.decode("utf-8", "ignore")
                status_texts.append(txt)
                if "origin set" in txt:
                    ekf_origin_seen = True
                    break
            elif msg.get_type() == "EKF_STATUS_REPORT":
                # flags=167 is the usual ready state here, but STATUSTEXT is more readable.
                pass
    print(f"    EKF origin seen: {ekf_origin_seen}")

    print("    setting mode STABILIZE...")
    mav.set_mode_apm("STABILIZE")
    mode_ok = False
    t0 = time.time()
    while time.time() - t0 < 10:
        msg = mav.recv_match(blocking=True, timeout=0.5)
        if not msg:
            continue
        if msg.get_type() == "COMMAND_ACK" and msg.command == 176 and msg.result == 0:
            mode_ok = True
        elif msg.get_type() == "HEARTBEAT" and msg.custom_mode == 0:
            mode_ok = True
            break
    if not mode_ok:
        print("    [!] STABILIZE mode was not acknowledged")
        cleanup()
        return 1

    print("    arming with force=21196...")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 21196, 0, 0, 0, 0, 0,
    )
    armed = False
    arm_t0 = time.time()
    while time.time() - arm_t0 < 15:
        msg = mav.recv_match(blocking=True, timeout=0.5)
        if not msg:
            continue
        mt = msg.get_type()
        if mt == "HEARTBEAT" and (
            msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        ):
            armed = True
            break
        if mt == "STATUSTEXT":
            txt = msg.text if isinstance(msg.text, str) else msg.text.decode("utf-8", "ignore")
            status_texts.append(txt)
    if not armed:
        print(f"    [!] ARM failed; last STATUS: {status_texts[-8:]}")
        cleanup()
        return 1
    print(f"    ✓ ARMED after {time.time()-arm_t0:.1f}s")

    print("    sending RC override throttle=1800 until climb is visible...")
    t0 = time.time()
    base_alt: float | None = None
    max_alt = -999.0
    while time.time() - t0 < 35:
        mav.mav.rc_channels_override_send(
            mav.target_system, mav.target_component,
            1500, 1500, 1800, 1500, 0, 0, 0, 0,
        )
        msg = mav.recv_match(blocking=True, timeout=0.2)
        if msg and msg.get_type() == "GLOBAL_POSITION_INT":
            rel_alt = msg.relative_alt / 1000.0
            if base_alt is None:
                base_alt = rel_alt
            if rel_alt > max_alt:
                max_alt = rel_alt
            if max_alt - base_alt > 0.5:
                print(f"    ✓ t+{time.time()-t0:.1f}s rel_alt_delta={max_alt-base_alt:.2f}m")
                break
        elif msg and msg.get_type() == "STATUSTEXT":
            txt = msg.text if isinstance(msg.text, str) else msg.text.decode("utf-8", "ignore")
            status_texts.append(txt)

    base_alt = base_alt if base_alt is not None else 0.0
    climb_delta = max_alt - base_alt
    print(f"    Max relative altitude: {max_alt:.2f}m (delta={climb_delta:.2f}m)")

    bridge_lines = bridge_frames_path.read_text().strip().splitlines()
    n_frames = len(bridge_lines)
    max_pwm_seen = 0
    for line in bridge_lines[-200:]:
        rec = json.loads(line)
        pwm = rec.get("pwm_first8") or []
        if pwm:
            max_pwm_seen = max(max_pwm_seen, max(pwm[:4]))
    print(f"    Max motor PWM seen in bridge log: {max_pwm_seen}")

    takeoff_ok = armed and climb_delta > 0.5 and max_pwm_seen > 1600
    if not takeoff_ok:
        print(f"    [!] takeoff failed; last STATUS: {status_texts[-8:]}")

    cleanup()
    hb_stop.set()
    print("\n=====================================================")
    print("  REAL ArduPilot SITL ↔ JsonFdmBridge VERIFIED")
    print("=====================================================")
    print(f"  Bridge:   {bridge_log}")
    print(f"  SITL:     {sitl_log}")
    print(f"  Frames:   {bridge_frames_path}  ({n_frames} log entries)")
    print(f"  HEARTBEAT received from sysid={mav.target_system}")
    print(f"  Telemetry: {n_attitude} ATTITUDE + {n_position} GLOBAL_POSITION_INT")
    print(f"  Reported alt: {last_alt:.2f}m, lat: {last_lat:.6f}")
    print(f"  ARMED: {armed}  Takeoff delta: {climb_delta:.2f}m  Max PWM: {max_pwm_seen}")
    print()
    if takeoff_ok:
        print("  ALL CHECKS PASSED — full arm + manual takeoff + altitude verified")
        return 0
    print("  CHECK FAILED — arm/takeoff did not produce verified climb")
    return 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    try:
        sys.exit(main())
    finally:
        cleanup()
