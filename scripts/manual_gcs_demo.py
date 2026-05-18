"""Diagnostic-only lightweight Python mini-GCS for Stage 2.4.

This script is not the Stage 2.4 acceptance path. It is kept only as a
diagnostic MAVLink probe/fallback. The Stage 2.4 requirement "manual control
of one BAS through GCS" is closed by scripts/run_stage_2_4_mavproxy_gcs.sh,
where the only command sender is MAVProxy command-line GCS.

The commands below are live GUIDED MAVLink commands, not mission upload, but
because they are sent directly through pymavlink they do not prove the GCS/NPU
command path.

Запускается из bas-ctrl-far netns:
    ip netns exec bas-ctrl-far .venv/bin/python scripts/manual_gcs_demo.py \
        --endpoint udpout:10.10.0.2:14550

Diagnostic link: probe -> ns-3 control channel -> mavbridge -> SITL primary
serial.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pymavlink import mavutil


def log(msg: str) -> None:
    print(f"[gcs] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def set_mode(mav, mode_name: str) -> None:
    """ArduCopter MAV_MODE_CUSTOM через mavutil.set_mode_apm."""
    log(f"set_mode → {mode_name}")
    mav.set_mode_apm(mode_name)


def arm(mav, force: bool = True) -> bool:
    """ARM через COMMAND_LONG MAV_CMD_COMPONENT_ARM_DISARM с force=21196."""
    log(f"ARM (force={force})")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1.0,                                       # param1: 1 = arm
        21196.0 if force else 0.0,                 # param2: 21196 = bypass pre-arm
        0.0, 0.0, 0.0, 0.0, 0.0,
    )
    ack = mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=5)
    if ack is None:
        log("  ✗ no ACK on ARM")
        return False
    ok = ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
    log(f"  {'✓' if ok else '✗'} ARM ACK result={ack.result}")
    return ok


def takeoff(mav, alt_m: float) -> bool:
    """MAV_CMD_NAV_TAKEOFF к alt_m (relative, в metres)."""
    log(f"TAKEOFF → alt={alt_m}m")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0,
        0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, float(alt_m),
    )
    ack = mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=5)
    if ack is None:
        log("  ✗ no ACK on TAKEOFF")
        return False
    ok = ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
    log(f"  {'✓' if ok else '✗'} TAKEOFF ACK result={ack.result}")
    return ok


def goto_local(mav, north_m: float, east_m: float, alt_m: float) -> None:
    """SET_POSITION_TARGET_LOCAL_NED — лететь к точке (north, east, -alt) от
    EKF origin (= home). Это live GUIDED-команда, отдаётся отдельно от
    mission upload."""
    log(f"GOTO local: north={north_m}m east={east_m}m alt={alt_m}m")
    # type_mask: ignore velocities/accels/yaw — только position.
    POSITION_TARGET_TYPEMASK_VX_IGNORE = 0b0000111111111000
    type_mask = (
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
    )
    mav.mav.set_position_target_local_ned_send(
        0,                                                  # time_boot_ms
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_LOCAL_NED,
        type_mask,
        float(north_m), float(east_m), float(-alt_m),       # NED: z down
        0, 0, 0,                                            # vx, vy, vz
        0, 0, 0,                                            # ax, ay, az
        0, 0,                                               # yaw, yaw_rate
    )
    # Нет ACK для SET_POSITION_TARGET (это streaming command).


def wait_alt(mav, target_alt_m: float, tolerance_m: float = 2.0,
             timeout_s: float = 60.0) -> bool:
    """Дождаться когда GLOBAL_POSITION_INT.relative_alt близка к target."""
    log(f"wait alt {target_alt_m}±{tolerance_m}m (timeout {timeout_s}s)")
    deadline = time.time() + timeout_s
    last_alt = 0.0
    while time.time() < deadline:
        msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
        if msg is None:
            continue
        last_alt = msg.relative_alt / 1000.0  # mm → m
        if abs(last_alt - target_alt_m) < tolerance_m:
            log(f"  ✓ alt={last_alt:.1f}m (target {target_alt_m})")
            return True
    log(f"  ✗ alt timeout, last={last_alt:.1f}m")
    return False


def wait_position(mav, target_north: float, target_east: float,
                  tolerance_m: float = 3.0, timeout_s: float = 60.0) -> bool:
    """Дождаться когда LOCAL_POSITION_NED близка к (north, east)."""
    log(f"wait position ({target_north},{target_east})±{tolerance_m}m")
    deadline = time.time() + timeout_s
    last = (0.0, 0.0)
    while time.time() < deadline:
        msg = mav.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=2)
        if msg is None:
            continue
        last = (msg.x, msg.y)
        dist = math.hypot(msg.x - target_north, msg.y - target_east)
        if dist < tolerance_m:
            log(f"  ✓ pos=({msg.x:.1f},{msg.y:.1f}) dist={dist:.1f}m")
            return True
    log(f"  ✗ position timeout, last=({last[0]:.1f},{last[1]:.1f})")
    return False


def wait_disarmed(mav, timeout_s: float = 120.0) -> bool:
    """Дождаться auto-disarm после LAND (HEARTBEAT.armed=False)."""
    log(f"wait disarmed (timeout {timeout_s}s)")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
        if msg is None:
            continue
        armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if not armed:
            log("  ✓ disarmed")
            return True
    log("  ✗ disarm timeout")
    return False


def request_streams(mav) -> None:
    """REQUEST_DATA_STREAM как делает MAVProxy при старте — без этого
    ArduPilot SITL шлёт только HEARTBEAT (SR1_* params = 0 default)."""
    log("request_data_stream STREAM_ALL @ 10 Hz")
    mav.mav.request_data_stream_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_ALL,
        10,                                 # rate Hz
        1,                                  # start
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", required=True,
                   help="MAVLink endpoint (udp:HOST:PORT)")
    p.add_argument("--takeoff-alt", type=float, default=30.0)
    p.add_argument("--square-side", type=float, default=50.0,
                   help="side of mission square in meters")
    args = p.parse_args()

    log(f"connecting to {args.endpoint}")
    mav = mavutil.mavlink_connection(args.endpoint, source_system=255)
    # ВАЖНО: с udpout: pymavlink ничего не шлёт сам. mavbridge socat
    # UDP4-LISTEN запомнит peer только когда получит первый packet от
    # GCS. Поэтому сначала ШЛЁМ GCS HEARTBEAT (несколько раз, на случай
    # ns-3 transit drops), затем ждём UAV HEARTBEAT.
    log("sending GCS HEARTBEATs to open socat return path...")
    for _ in range(5):
        try:
            mav.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        except Exception:
            pass
        time.sleep(0.5)

    hb = mav.wait_heartbeat(timeout=30)
    if hb is None:
        log("✗ no HEARTBEAT in 30s")
        sys.exit(2)
    log(f"✓ HEARTBEAT got: sys={mav.target_system} comp={mav.target_component}")
    # Продолжаем шлать HEARTBEAT в фоне — некоторые SITL ожидают.
    mav.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)

    # Шаг 1: запросить data streams (GPS, ATTITUDE, etc.).
    request_streams(mav)
    time.sleep(2)

    # Шаг 2: переключить в GUIDED.
    set_mode(mav, "GUIDED")
    time.sleep(2)

    # Шаг 3: ARM (force=21196 bypass pre-arm — SITL без RC).
    if not arm(mav, force=True):
        log("✗ ARM failed")
        sys.exit(3)
    time.sleep(2)

    # Шаг 4: TAKEOFF.
    if not takeoff(mav, args.takeoff_alt):
        log("✗ TAKEOFF rejected")
        sys.exit(4)
    if not wait_alt(mav, args.takeoff_alt, tolerance_m=2.0, timeout_s=60.0):
        log("✗ TAKEOFF altitude timeout")
        sys.exit(5)

    # Шаг 5: live position targets — square.
    side = args.square_side
    alt = args.takeoff_alt
    waypoints = [
        (side, 0.0),       # north +50m
        (side, side),      # east +50m
        (0.0, side),       # south back
        (0.0, 0.0),        # home
    ]
    for i, (n, e) in enumerate(waypoints, 1):
        log(f"--- waypoint {i}/{len(waypoints)} ---")
        goto_local(mav, n, e, alt)
        wait_position(mav, n, e, tolerance_m=3.0, timeout_s=45.0)
        time.sleep(2)

    # Шаг 6: LAND.
    set_mode(mav, "LAND")
    time.sleep(2)
    if not wait_disarmed(mav, timeout_s=90.0):
        log("✗ LAND/disarm timeout")
        sys.exit(6)

    log("=== MISSION COMPLETE: takeoff → 4 waypoints → land → disarm ===")
    sys.exit(0)


if __name__ == "__main__":
    main()
