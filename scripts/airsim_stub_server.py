#!/usr/bin/env python3
"""Минимальный AirSim msgpack-rpc stub server.

Имитирует Cosys-AirSim API endpoint для headless smoke без необходимости
запуска полноценного UE5 binary. Используется в CI и при отсутствии
Cosys-AirSim Editor / packaged build.

Реализует только bridge-relevant RPC methods:
  - ping → True
  - getServerVersion → 1
  - simListSceneObjects → mock list
  - getMultirotorState → mock state
  - simSetVehiclePose → no-op (записывает в pose log)
  - simGetImage → empty bytes
  - getLidarData → empty list

Не пытается имитировать физику или сенсоры. Цель — позволить airsim_bridge
запуститься end-to-end и пройти smoke тест на CI / в WSL2 без GPU.

Pattern: один TCP server, обрабатывает соединение в thread'е, парсит
msgpack stream через Unpacker, эмитит response с тем же msgid.
"""
from __future__ import annotations

import argparse
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Any

import msgpack


MSGPACK_TYPE_REQUEST = 0
MSGPACK_TYPE_RESPONSE = 1


# Глобальное состояние stub'а — pose-history для bridge verification.
STATE_LOCK = threading.Lock()
LAST_POSE: dict[str, Any] = {}
POSE_HISTORY: list[dict[str, Any]] = []
SPAWN_HISTORY: list[dict[str, Any]] = []
POSE_LOG_PATH: Path | None = None
SPAWN_LOG_PATH: Path | None = None


def _emit_pose_log(record: dict[str, Any]) -> None:
    """Запись простого NDJSON о каждом setVehiclePose — для отладки и
    integration smoke (можно сверить с Gazebo pose из events.jsonl)."""
    if POSE_LOG_PATH is None:
        return
    import json
    try:
        with POSE_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def dispatch(method: str, args: list[Any]) -> Any:
    """Возвращает результат для одного RPC method-call."""
    if method == "ping":
        return True
    if method == "getServerVersion":
        return 1
    if method == "getClientVersion":
        return 1
    if method == "simListSceneObjects":
        # Mock сцена: iris + ground plane + два obstacle'а как в RF demo
        # + любые spawned objects из текущей session.
        base = ["BP_iris_uav1", "RF_Hangar", "RF_ControlTower", "Floor"]
        with STATE_LOCK:
            spawned = [s.get("object_name", "?") for s in SPAWN_HISTORY]
        return base + spawned
    if method == "simSpawnObject":
        # args = [object_name, asset_name, pose, scale, physics_enabled, is_blueprint]
        # Cosys-AirSim returns object_name (string) на успех.
        if not args:
            return ""
        rec = {
            "wall_time": time.time(),
            "object_name": args[0] if len(args) > 0 else "",
            "asset_name": args[1] if len(args) > 1 else "",
            "pose": args[2] if len(args) > 2 else None,
            "scale": args[3] if len(args) > 3 else None,
            "physics_enabled": args[4] if len(args) > 4 else False,
            "is_blueprint": args[5] if len(args) > 5 else False,
        }
        with STATE_LOCK:
            SPAWN_HISTORY.append(rec)
        if SPAWN_LOG_PATH is not None:
            import json
            try:
                with SPAWN_LOG_PATH.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            except OSError:
                pass
        return rec["object_name"]
    if method == "simDestroyObject":
        # args = [object_name]
        if not args:
            return False
        target = args[0]
        with STATE_LOCK:
            before = len(SPAWN_HISTORY)
            SPAWN_HISTORY[:] = [s for s in SPAWN_HISTORY
                                if s.get("object_name") != target]
            removed = before - len(SPAWN_HISTORY)
        return removed > 0
    if method == "simListAssets":
        # Минимальный asset list (типичный для Cosys-AirSim Blocks scene).
        return ["Cube", "Cylinder", "Sphere", "Cone", "Plane"]
    if method == "getMultirotorState":
        with STATE_LOCK:
            pos = LAST_POSE.get("position", {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0})
            ori = LAST_POSE.get("orientation", {"w_val": 1.0, "x_val": 0.0,
                                                "y_val": 0.0, "z_val": 0.0})
        return {
            "kinematics_estimated": {
                "position": pos, "orientation": ori,
                "linear_velocity": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "angular_velocity": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
            },
            "landed_state": 1,   # 1 = OnGround
            "rc_data": {"is_initialized": False},
            "gps_location": {
                "latitude": -35.363262, "longitude": 149.165237,
                "altitude": 584.0,
            },
            "timestamp": int(time.time() * 1e9),
        }
    if method == "simSetVehiclePose":
        # args = [pose_dict, ignore_collision, vehicle_name]
        if not args:
            return None
        pose = args[0] or {}
        with STATE_LOCK:
            LAST_POSE.update({
                "position": pose.get("position", {}),
                "orientation": pose.get("orientation", {}),
            })
            POSE_HISTORY.append({
                "wall_time": time.time(),
                "vehicle": args[2] if len(args) > 2 else "",
                "pose": pose,
            })
        _emit_pose_log({
            "wall_time": time.time(),
            "method": method,
            "vehicle": args[2] if len(args) > 2 else "",
            "pose": pose,
        })
        return None
    if method == "simGetImage":
        # Возвращаем заглушку — 1×1 PNG (RGB 255,255,255) base of any
        # AirSim image consumer. bridge сам разберётся что это empty
        # frame и не запишет.
        return b""
    if method == "getLidarData":
        return {
            "point_cloud": [],   # без точек
            "time_stamp": int(time.time() * 1e9),
            "pose": {
                "position": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                "orientation": {"w_val": 1.0, "x_val": 0.0,
                                "y_val": 0.0, "z_val": 0.0},
            },
        }
    # Любой неизвестный метод — None (AirSim воспринимает как success/no-op).
    return None


class _RPCHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock: socket.socket = self.request
        sock.settimeout(60.0)
        unpacker = msgpack.Unpacker(raw=False)
        peer = self.client_address
        print(f"[stub] new client: {peer}", flush=True)
        try:
            while True:
                try:
                    data = sock.recv(16384)
                except socket.timeout:
                    continue
                if not data:
                    break
                unpacker.feed(data)
                for msg in unpacker:
                    if not isinstance(msg, (list, tuple)) or len(msg) < 4:
                        continue
                    msg_type, msgid, method, args = msg[:4]
                    if msg_type != MSGPACK_TYPE_REQUEST:
                        continue
                    try:
                        result = dispatch(str(method), list(args or []))
                        error = None
                    except Exception as exc:
                        result = None
                        error = str(exc)
                    response = msgpack.packb(
                        [MSGPACK_TYPE_RESPONSE, msgid, error, result],
                        use_bin_type=True,
                    )
                    sock.sendall(response)
        finally:
            print(f"[stub] client gone: {peer}", flush=True)


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    global POSE_LOG_PATH, SPAWN_LOG_PATH

    ap = argparse.ArgumentParser(description="AirSim msgpack-rpc stub server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=41451)
    ap.add_argument("--pose-log",
                    help="NDJSON путь для записи всех simSetVehiclePose")
    ap.add_argument("--spawn-log",
                    help="NDJSON путь для записи всех simSpawnObject")
    args = ap.parse_args()

    if args.pose_log:
        POSE_LOG_PATH = Path(args.pose_log)
        POSE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        POSE_LOG_PATH.write_text("")
    if args.spawn_log:
        SPAWN_LOG_PATH = Path(args.spawn_log)
        SPAWN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        SPAWN_LOG_PATH.write_text("")

    server = _ThreadedTCPServer((args.host, args.port), _RPCHandler)
    bind_host, bind_port = server.server_address[:2]
    print(f"[stub] AirSim stub listening on {bind_host}:{bind_port}",
          flush=True)
    print(f"[stub] pose log:  {POSE_LOG_PATH or '<disabled>'}", flush=True)
    print(f"[stub] spawn log: {SPAWN_LOG_PATH or '<disabled>'}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("[stub] interrupted", flush=True)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
