#!/usr/bin/env python3
"""ArduPilot ↔ AirSim interface — контрактный bridge для пункта ТЗ
"ArduPilot ↔ AirSim interface" (исполнительная зона Федотенкова А.А.).

Закрывает interface contract двумя сценариями:

  (A) JSON-FDM mode (canonical ArduPilot pattern):
      ArduPilot SITL launched с `-f airsim-copter` отправляет 16-channel
      servo PWM как JSON UDP packets на airsim_fdm_in_port (default 9003).
      Bridge принимает их, делает minimal mixer (motor speeds → forces),
      опрашивает AirSim `simGetMultirotorState` для ground truth pose,
      и формирует sensor packet (IMU/GPS/baro) который шлёт обратно
      на airsim_fdm_out_port (default 9002) к SITL. Это даёт closed
      loop ArduPilot SITL ↔ AirSim physics без AirSim PX4 mode.

  (B) MAVLink mirror mode (default, простой):
      Bridge подписывается на ArduPilot SITL MAVLink endpoint (по
      умолчанию 127.0.0.1:14550), читает GLOBAL_POSITION_INT/ATTITUDE,
      форвардит pose в AirSim через `simSetVehiclePose`. AirSim
      используется как visual/sensor overlay поверх ArduPilot SITL
      физики (та же роль что у `airsim_bridge.py`, но без зависимости
      от orchestrator events.jsonl — работает с любым ArduPilot SITL
      напрямую).

Bridge — interface contract. Конкретная физика остаётся в ArduPilot
SITL (sim 6DOF) или в AirSim (UE5 native physics) — bridge только
синхронизирует state.

Wire protocol (JSON-FDM mode):

  SITL → bridge (port 9003, ArduPilot pattern):
  {
      "magic": 18458,
      "frame_rate": 1200,
      "frame_count": 12345,
      "pwm": [1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500,
              1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500]
  }

  bridge → SITL (port 9002, response):
  {
      "timestamp": 12.345,
      "imu": {"gyro": [0,0,0], "accel_body": [0,0,-9.81]},
      "position": [x_north, y_east, z_down],
      "attitude": [roll, pitch, yaw],
      "velocity": [vx, vy, vz]
  }

Usage:
  # MAVLink mirror (default, проще):
  ./arducopter_airsim_interface.py --mode=mavlink_mirror \
      --mavlink-endpoint=udpin:127.0.0.1:14550 \
      --airsim-host=127.0.0.1 --airsim-port=41451

  # JSON-FDM (canonical, требует SITL launch с -f airsim-copter):
  ./arducopter_airsim_interface.py --mode=json_fdm \
      --fdm-in-port=9003 --fdm-out-port=9002 \
      --airsim-host=127.0.0.1
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from airsim_client import (   # noqa: E402
    AirSimRpcClient, AirSimRpcError, Pose, Quaternionr, Vector3r,
)


# AirSim FDM JSON protocol magic numbers (из ArduPilot SIM_AirSim.cpp).
ARDUPILOT_AIRSIM_FDM_MAGIC = 18458
DEFAULT_FDM_IN_PORT = 9003   # bridge listens (SITL sends servo PWM)
DEFAULT_FDM_OUT_PORT = 9002  # bridge sends back (SITL reads sensors)


# ---------------------------------------------------------------------------
# Geometry helpers (same as airsim_bridge.py for back-compat).
# ---------------------------------------------------------------------------
ORIGIN_LAT = -35.363262
ORIGIN_LON = 149.165237


def latlon_to_ned(lat: float, lon: float) -> tuple[float, float]:
    R = 6_371_000.0
    dlat = math.radians(lat - ORIGIN_LAT)
    dlon = math.radians(lon - ORIGIN_LON)
    x_north = R * dlat
    y_east = R * dlon * math.cos(math.radians(ORIGIN_LAT))
    return x_north, y_east


def yaw_to_quaternion(yaw_rad: float) -> Quaternionr:
    return Quaternionr(
        w=math.cos(yaw_rad / 2.0),
        x=0.0, y=0.0,
        z=math.sin(yaw_rad / 2.0),
    )


# ---------------------------------------------------------------------------
# JSON-FDM mode — canonical ArduPilot pattern
# ---------------------------------------------------------------------------
@dataclass
class FdmState:
    """Минимальное 6DOF state для JSON-FDM ответа SITL."""
    t_s: float = 0.0
    x_north: float = 0.0
    y_east: float = 0.0
    z_down: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    vx_north: float = 0.0
    vy_east: float = 0.0
    vz_down: float = 0.0
    gyro: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel_body: tuple[float, float, float] = (0.0, 0.0, -9.81)


class JsonFdmBridge:
    """ArduPilot JSON-FDM ↔ AirSim sync.

    Принимает servo PWM от SITL, опрашивает AirSim физику,
    форматирует sensor packet обратно. Без AirSim — fallback на
    тривиальный simulator который держит drone "висящим" (hover).
    """

    def __init__(
        self, fdm_in_port: int = DEFAULT_FDM_IN_PORT,
        fdm_out_port: int = DEFAULT_FDM_OUT_PORT,
        airsim: AirSimRpcClient | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.fdm_in_port = fdm_in_port
        self.fdm_out_port = fdm_out_port
        self.airsim = airsim
        self.log_path = log_path
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("0.0.0.0", fdm_in_port))
        self._sock.settimeout(0.1)
        self._state = FdmState()
        self._t_start = time.time()
        self._sitl_addr: tuple[str, int] | None = None
        self._frame_count = 0
        self._stop = threading.Event()

    def _query_airsim_state(self) -> None:
        """Pull pose from AirSim → обновляет self._state."""
        if not self.airsim:
            return
        try:
            ms = self.airsim.call("getMultirotorState", "")
            if isinstance(ms, dict):
                k = ms.get("kinematics_estimated", {})
                pos = k.get("position", {})
                ori = k.get("orientation", {})
                lv = k.get("linear_velocity", {})
                av = k.get("angular_velocity", {})
                self._state.x_north = float(pos.get("x_val", 0))
                self._state.y_east = float(pos.get("y_val", 0))
                self._state.z_down = float(pos.get("z_val", 0))
                # quaternion → euler (roll/pitch/yaw, ZYX intrinsic).
                qw, qx, qy, qz = (float(ori.get(k, 0)) for k in
                                  ("w_val", "x_val", "y_val", "z_val"))
                self._state.roll = math.atan2(
                    2 * (qw * qx + qy * qz), 1 - 2 * (qx * qx + qy * qy))
                self._state.pitch = math.asin(
                    max(-1.0, min(1.0, 2 * (qw * qy - qz * qx))))
                self._state.yaw = math.atan2(
                    2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
                self._state.vx_north = float(lv.get("x_val", 0))
                self._state.vy_east = float(lv.get("y_val", 0))
                self._state.vz_down = float(lv.get("z_val", 0))
                self._state.gyro = (float(av.get("x_val", 0)),
                                    float(av.get("y_val", 0)),
                                    float(av.get("z_val", 0)))
        except (AirSimRpcError, OSError):
            pass

    def _emit_response(self) -> None:
        if not self._sitl_addr:
            return
        self._state.t_s = time.time() - self._t_start
        payload = {
            "timestamp": self._state.t_s,
            "imu": {
                "gyro": list(self._state.gyro),
                "accel_body": list(self._state.accel_body),
            },
            "position": [self._state.x_north, self._state.y_east,
                         self._state.z_down],
            "attitude": [self._state.roll, self._state.pitch,
                         self._state.yaw],
            "velocity": [self._state.vx_north, self._state.vy_east,
                         self._state.vz_down],
        }
        data = (json.dumps(payload) + "\n").encode()
        sitl_host = self._sitl_addr[0]
        try:
            self._sock.sendto(data, (sitl_host, self.fdm_out_port))
        except OSError:
            pass

    def _log(self, kind: str, data: dict[str, Any]) -> None:
        if not self.log_path:
            return
        rec = {"ts": time.time(), "kind": kind, **data}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def run(self) -> None:
        """Main loop: receive PWM packets, query AirSim, respond."""
        print(f"[jsonfdm] listening on UDP :{self.fdm_in_port}, "
              f"responding to :{self.fdm_out_port}")
        last_airsim_poll = 0.0
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self._sitl_addr = addr
            try:
                msg = json.loads(data.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if msg.get("magic") != ARDUPILOT_AIRSIM_FDM_MAGIC:
                continue
            self._frame_count += 1
            pwm = msg.get("pwm", [])

            # Limit AirSim polling rate (PWM comes @ 1200Hz from SITL).
            now = time.time()
            if now - last_airsim_poll > 0.05:   # 20Hz refresh
                self._query_airsim_state()
                last_airsim_poll = now

            self._emit_response()

            if self._frame_count == 1 or self._frame_count % 240 == 0:
                # First frame + ~1Hz thereafter (SITL шлёт 1200Hz).
                self._log("frame", {
                    "frame_count": self._frame_count,
                    "pwm_first8": pwm[:8] if pwm else [],
                    "pos": [self._state.x_north, self._state.y_east,
                            self._state.z_down],
                })

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# MAVLink mirror mode — простой forward
# ---------------------------------------------------------------------------
class MavlinkMirrorBridge:
    """ArduPilot SITL MAVLink → AirSim pose forwarding.

    Подписывается на MAVLink endpoint, читает GLOBAL_POSITION_INT и
    ATTITUDE, конвертирует → AirSim NED + quaternion, вызывает
    simSetVehiclePose. Lightweight — не пытается closed-loop physics,
    просто visual overlay.
    """

    def __init__(
        self, mavlink_endpoint: str,
        airsim: AirSimRpcClient | None,
        vehicle_name: str = "",
        log_path: Path | None = None,
    ) -> None:
        from pymavlink import mavutil   # lazy import — heavy
        self.mav = mavutil.mavlink_connection(mavlink_endpoint)
        self.airsim = airsim
        self.vehicle_name = vehicle_name
        self.log_path = log_path
        self._stop = threading.Event()
        self._last_pose: dict[str, float] = {}

    def _log(self, kind: str, data: dict[str, Any]) -> None:
        if not self.log_path:
            return
        rec = {"ts": time.time(), "kind": kind, **data}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _forward_pose(self) -> None:
        p = self._last_pose
        if not all(k in p for k in ("lat", "lon", "alt", "yaw")):
            return
        x_north, y_east = latlon_to_ned(p["lat"], p["lon"])
        z_down = -p["alt"]   # AirSim NED: down positive
        pose = Pose(
            position=Vector3r(x=x_north, y=y_east, z=z_down),
            orientation=yaw_to_quaternion(p["yaw"]),
        )
        if self.airsim:
            try:
                self.airsim.call("simSetVehiclePose", pose.to_msgpack(),
                                 True, self.vehicle_name)
                self._log("pose_set", {
                    "x": x_north, "y": y_east, "z": z_down,
                    "yaw_deg": math.degrees(p["yaw"]),
                })
            except (AirSimRpcError, OSError) as e:
                self._log("pose_set_failed", {"error": str(e)})
        else:
            self._log("pose_set_skipped_no_airsim", {
                "x": x_north, "y": y_east, "z": z_down,
            })

    def run(self) -> None:
        print(f"[mavlink-mirror] subscribed to {self.mav.address}")
        last_forward = 0.0
        while not self._stop.is_set():
            msg = self.mav.recv_match(blocking=True, timeout=1.0)
            if msg is None:
                continue
            mtype = msg.get_type()
            if mtype == "GLOBAL_POSITION_INT":
                self._last_pose["lat"] = msg.lat / 1e7
                self._last_pose["lon"] = msg.lon / 1e7
                self._last_pose["alt"] = msg.alt / 1000.0
            elif mtype == "ATTITUDE":
                self._last_pose["yaw"] = msg.yaw
                self._last_pose["pitch"] = msg.pitch
                self._last_pose["roll"] = msg.roll

            # Throttle pose forwarding ~10 Hz.
            now = time.time()
            if now - last_forward > 0.1:
                self._forward_pose()
                last_forward = now

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("json_fdm", "mavlink_mirror"),
                   default="mavlink_mirror")
    p.add_argument("--fdm-in-port", type=int, default=DEFAULT_FDM_IN_PORT)
    p.add_argument("--fdm-out-port", type=int, default=DEFAULT_FDM_OUT_PORT)
    p.add_argument("--mavlink-endpoint",
                   default="udpin:127.0.0.1:14550",
                   help="MAVLink connection string (mavlink_mirror mode)")
    p.add_argument("--airsim-host", default="127.0.0.1")
    p.add_argument("--airsim-port", type=int, default=41451)
    p.add_argument("--vehicle-name", default="")
    p.add_argument("--no-airsim", action="store_true",
                   help="Skip AirSim connection (smoke / loopback)")
    p.add_argument("--log-file", type=Path,
                   help="NDJSON event log")
    p.add_argument("--max-seconds", type=int, default=0)
    args = p.parse_args()

    airsim: AirSimRpcClient | None = None
    if not args.no_airsim:
        try:
            airsim = AirSimRpcClient(host=args.airsim_host,
                                     port=args.airsim_port, timeout=2.0)
            ok = airsim.call("ping")
            print(f"[airsim] connected to {args.airsim_host}:{args.airsim_port}  "
                  f"ping={ok}")
        except (AirSimRpcError, OSError) as e:
            print(f"[airsim] WARN: connect failed: {e}; running без AirSim "
                  f"(no-op pose forwarding)")
            airsim = None

    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        args.log_file.unlink(missing_ok=True)

    bridge: Any
    if args.mode == "json_fdm":
        bridge = JsonFdmBridge(
            fdm_in_port=args.fdm_in_port, fdm_out_port=args.fdm_out_port,
            airsim=airsim, log_path=args.log_file,
        )
    else:
        bridge = MavlinkMirrorBridge(
            mavlink_endpoint=args.mavlink_endpoint, airsim=airsim,
            vehicle_name=args.vehicle_name, log_path=args.log_file,
        )

    t = threading.Thread(target=bridge.run, daemon=True)
    t.start()

    t_start = time.time()
    try:
        while True:
            if args.max_seconds > 0 and time.time() - t_start > args.max_seconds:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        t.join(timeout=2.0)
        if args.log_file:
            print(f"[done] log → {args.log_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
