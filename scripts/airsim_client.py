#!/usr/bin/env python3
"""Минимальный msgpack-rpc client для AirSim / Cosys-AirSim.

Заменяет legacy `airsim` PyPI package (он не собирается под Python 3.12).
Реализует только тот subset RPC методов который нужен для bridge:
ping, getServerVersion, simSetVehiclePose, simGetImage, simGetLidarData,
getMultirotorState.

Совместим с любым AirSim-форком — Cosys-AirSim, оригинальным AirSim,
и любым msgpack-rpc сервером который имитирует AirSim API (включая наш
scripts/airsim_stub_server.py).

Default endpoint — `127.0.0.1:41451` (AirSim hardcoded).

Pattern: каждый RPC call отправляет msgpack array
  [type=0(request), msgid, method_name, [args]]
и получает обратно
  [type=1(response), msgid, error, result]
"""
from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass, field
from typing import Any

import msgpack


# msgpack-rpc типы сообщений.
MSGPACK_TYPE_REQUEST = 0
MSGPACK_TYPE_RESPONSE = 1
MSGPACK_TYPE_NOTIFICATION = 2


class AirSimRpcError(Exception):
    """Raised when AirSim returns error response."""


@dataclass
class Vector3r:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_msgpack(self) -> dict[str, float]:
        return {"x_val": self.x, "y_val": self.y, "z_val": self.z}

    @classmethod
    def from_msgpack(cls, data: dict[str, Any] | None) -> "Vector3r":
        if not data:
            return cls()
        return cls(
            x=float(data.get("x_val", 0.0)),
            y=float(data.get("y_val", 0.0)),
            z=float(data.get("z_val", 0.0)),
        )


@dataclass
class Quaternionr:
    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_msgpack(self) -> dict[str, float]:
        return {
            "w_val": self.w, "x_val": self.x,
            "y_val": self.y, "z_val": self.z,
        }

    @classmethod
    def from_msgpack(cls, data: dict[str, Any] | None) -> "Quaternionr":
        if not data:
            return cls()
        return cls(
            w=float(data.get("w_val", 1.0)),
            x=float(data.get("x_val", 0.0)),
            y=float(data.get("y_val", 0.0)),
            z=float(data.get("z_val", 0.0)),
        )


@dataclass
class Pose:
    """AirSim Pose: position в NED метрах + orientation в quaternion."""
    position: Vector3r = field(default_factory=Vector3r)
    orientation: Quaternionr = field(default_factory=Quaternionr)

    def to_msgpack(self) -> dict[str, Any]:
        return {
            "position": self.position.to_msgpack(),
            "orientation": self.orientation.to_msgpack(),
        }


class AirSimRpcClient:
    """Minimal blocking msgpack-rpc client for AirSim API.

    Не покрывает все RPC методы — только bridge-relevant subset. Полный
    список см. в Cosys-AirSim/AirLib/include/api/RpcLibClientBase.hpp.

    Соединение per-instance + thread-safe через _lock (один in-flight
    request за раз). Если AirSim не запущен, methods raise OSError на
    первом RPC call — bridge ловит это и переключается в stub режим.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 41451,
                 timeout_s: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self._msgid = 0
        self._lock = threading.Lock()
        self._unpacker = msgpack.Unpacker(raw=False)

    def connect(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        sock.connect((self.host, self.port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> "AirSimRpcClient":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _call(self, method: str, *args: Any) -> Any:
        with self._lock:
            self.connect()
            assert self._sock is not None
            self._msgid = (self._msgid + 1) & 0xFFFF_FFFF
            packed = msgpack.packb(
                [MSGPACK_TYPE_REQUEST, self._msgid, method, list(args)],
                use_bin_type=True,
            )
            self._sock.sendall(packed)

            # Wait for response with matching msgid.
            while True:
                resp = self._read_message()
                if resp is None:
                    raise AirSimRpcError(f"connection closed during {method}")
                if (len(resp) >= 4 and resp[0] == MSGPACK_TYPE_RESPONSE
                        and resp[1] == self._msgid):
                    error = resp[2]
                    result = resp[3]
                    if error is not None:
                        raise AirSimRpcError(
                            f"AirSim RPC {method}: {error}")
                    return result
                # Если пришёл notification или чужой response — игнорируем
                # и читаем следующий (sync-mode protocol).

    def _read_message(self) -> list[Any] | None:
        # Унакпэкер собирает msgpack stream по мере прихода.
        for unpacked in self._unpacker:
            return unpacked
        # Нет полного сообщения в буфере — дочитываем.
        assert self._sock is not None
        while True:
            chunk = self._sock.recv(16384)
            if not chunk:
                return None
            self._unpacker.feed(chunk)
            for unpacked in self._unpacker:
                return unpacked

    # ---- Public API methods --------------------------------------------------
    def ping(self) -> bool:
        return bool(self._call("ping"))

    def get_server_version(self) -> int:
        return int(self._call("getServerVersion"))

    def get_client_version(self) -> int:
        # Cosys-AirSim 5.5 client API version = 1
        return 1

    def list_scene_objects(self, name_regex: str = ".*") -> list[str]:
        return list(self._call("simListSceneObjects", name_regex) or [])

    def get_multirotor_state(self, vehicle_name: str = "") -> dict[str, Any]:
        return self._call("getMultirotorState", vehicle_name) or {}

    def set_vehicle_pose(self, pose: Pose, ignore_collision: bool = True,
                         vehicle_name: str = "") -> None:
        self._call(
            "simSetVehiclePose",
            pose.to_msgpack(), bool(ignore_collision), vehicle_name,
        )

    def get_image(self, camera_name: str, image_type: int = 0,
                  vehicle_name: str = "") -> bytes:
        """image_type: 0=Scene, 1=DepthPlanar, 2=DepthPerspective, ...

        Cosys-AirSim 3.3 RPC signature: simGetImage(camera_name, image_type,
        vehicle_name) — 3 args. В default-Blocks сцене работающие camera_name:
        "0", "1", "front_center", "front_left", "front_right", "fpv",
        "back_center", "bottom_center".
        """
        data = self._call(
            "simGetImage", camera_name, image_type, vehicle_name,
        )
        if data is None:
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        # Cosys-AirSim иногда возвращает list of bytes.
        if isinstance(data, list):
            return bytes(data)
        return b""

    def get_lidar_data(self, lidar_name: str = "",
                       vehicle_name: str = "") -> dict[str, Any]:
        return self._call("getLidarData", lidar_name, vehicle_name) or {}


# Simple smoke when run as a script: ping + version + scene listing.
def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=41451)
    args = ap.parse_args()
    try:
        with AirSimRpcClient(args.host, args.port, timeout_s=3.0) as c:
            print(f"ping: {c.ping()}")
            print(f"server version: {c.get_server_version()}")
            objs = c.list_scene_objects()
            print(f"scene objects ({len(objs)}): {objs[:8]}...")
        return 0
    except (OSError, AirSimRpcError) as exc:
        print(f"AirSim probe failed: {exc}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main())
