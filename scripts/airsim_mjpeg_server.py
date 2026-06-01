#!/usr/bin/env python3
"""AirSim camera → MJPEG TCP server → пульт FPV window (integration E2).

Подключается к Cosys-AirSim RPC (airsim_client.AirSimRpcClient), берёт кадр
front-камеры и отдаёт raw multipart MJPEG (boundary spionkop) на TCP-порт —
ровно тот формат, что gcs_web_ui_server._proxy_fpv транслирует в браузерный
`<img src="/camera.mjpg">`. Достаточно указать пульту upstream:

    BAS_FPV_UPSTREAM_HOST=127.0.0.1 BAS_FPV_UPSTREAM_PORT=<этот порт>

Когда AirSim недоступен — отдаёт синтетический кадр-заглушку (статус +
crosshair + время), так что FPV-конвейер демонстрируется и без живого AirSim,
а при запущенном Cosys-AirSim на Windows-GPU в окно идёт реальная картинка.

Запуск:
  ./.venv/bin/python scripts/airsim_mjpeg_server.py --port 8767
  ./.venv/bin/python scripts/airsim_mjpeg_server.py --no-airsim   # только заглушка
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from airsim_client import AirSimRpcClient   # noqa: E402

try:
    import cv2   # type: ignore
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False

BOUNDARY = os.environ.get("BAS_FPV_BOUNDARY", "spionkop")
JPEG_QUALITY = int(os.environ.get("BAS_FPV_JPEG_QUALITY", "80"))


def _encode_jpeg(img_bgr: "np.ndarray") -> bytes | None:
    ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buf.tobytes() if ok else None


def placeholder_jpeg(status: str, sub: str, w: int = 640, h: int = 360) -> bytes:
    """Синтетический FPV-кадр (cv2): тёмный фон, crosshair, статус, время."""
    frame = np.zeros((h, w, 3), np.uint8)
    frame[:] = (20, 18, 16)
    cx, cy = w // 2, h // 2
    g = (70, 90, 70)
    cv2.line(frame, (cx - 34, cy), (cx - 12, cy), g, 1)
    cv2.line(frame, (cx + 12, cy), (cx + 34, cy), g, 1)
    cv2.line(frame, (cx, cy - 34), (cx, cy - 12), g, 1)
    cv2.line(frame, (cx, cy + 12), (cx, cy + 34), g, 1)
    cv2.circle(frame, (cx, cy), 46, g, 1)
    cv2.putText(frame, "AIRSIM FPV", (16, 32),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 210, 150), 2)
    cv2.putText(frame, status, (16, h - 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, (90, 130, 245), 2)
    cv2.putText(frame, sub, (16, h - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 170, 170), 1)
    cv2.putText(frame, time.strftime("%H:%M:%S"), (w - 120, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 210, 210), 1)
    out = _encode_jpeg(frame)
    return out or b""


def png_to_jpeg(png_bytes: bytes) -> bytes | None:
    if not png_bytes:
        return None
    arr = np.frombuffer(png_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    return _encode_jpeg(img)


class AirSimCamera:
    """Держит RPC-client, лениво (пере)подключается, отдаёт JPEG-кадр."""

    def __init__(self, host: str, port: int, camera: str,
                 vehicle: str, timeout_s: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.camera = camera
        self.vehicle = vehicle
        self.timeout_s = timeout_s
        self._client: AirSimRpcClient | None = None
        self._lock = threading.Lock()
        self._last_ok = 0.0
        self._next_retry = 0.0

    def _ensure(self) -> bool:
        if self._client is not None:
            return True
        if time.time() < self._next_retry:
            return False
        try:
            c = AirSimRpcClient(self.host, self.port, self.timeout_s)
            c.connect()
            self._client = c
            return True
        except OSError:
            self._client = None
            self._next_retry = time.time() + 2.0   # не долбим порт каждый кадр
            return False

    def _drop(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._next_retry = time.time() + 2.0

    def jpeg(self) -> bytes | None:
        with self._lock:
            if not self._ensure():
                return None
            try:
                png = self._client.get_image(self.camera, 0, self.vehicle)
            except Exception:
                self._drop()
                return None
            jpg = png_to_jpeg(png) if png else None
            if jpg:
                self._last_ok = time.time()
            return jpg

    @property
    def online(self) -> bool:
        return (time.time() - self._last_ok) < 3.0


def serve_client(conn: socket.socket, cam: AirSimCamera | None, fps: float) -> None:
    period = 1.0 / max(1.0, fps)
    conn.settimeout(8.0)
    try:
        while True:
            jpg = cam.jpeg() if cam is not None else None
            if jpg is None:
                if cam is not None:
                    status = "NO SIGNAL — запусти Cosys-AirSim (Windows GPU)"
                    sub = f"AirSim RPC {cam.host}:{cam.port} · cam={cam.camera}"
                else:
                    status = "PLACEHOLDER MODE (--no-airsim)"
                    sub = "FPV-конвейер: MJPEG → пульт /camera.mjpg → браузер"
                jpg = placeholder_jpeg(status, sub)
            if not jpg:
                time.sleep(period)
                continue
            part = (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                + jpg + b"\r\n"
            )
            conn.sendall(part)
            time.sleep(period)
    except (OSError, socket.timeout):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="AirSim camera → MJPEG TCP server (integration E2)")
    ap.add_argument("--host", default="0.0.0.0", help="bind host для MJPEG TCP")
    ap.add_argument("--port", type=int, default=8767,
                    help="bind port (пульту: BAS_FPV_UPSTREAM_PORT)")
    ap.add_argument("--airsim-host",
                    default=os.environ.get("BAS_AIRSIM_HOST", "127.0.0.1"))
    ap.add_argument("--airsim-port", type=int,
                    default=int(os.environ.get("BAS_AIRSIM_PORT", "41451")))
    ap.add_argument("--camera",
                    default=os.environ.get("BAS_AIRSIM_CAMERA", "front_center"))
    ap.add_argument("--vehicle", default=os.environ.get("BAS_AIRSIM_VEHICLE", ""))
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--no-airsim", action="store_true",
                    help="только заглушка (тест FPV-конвейера без AirSim)")
    args = ap.parse_args(argv)

    if not HAS_CV2:
        print("[airsim-mjpeg] cv2 недоступен — нужен для JPEG-кодирования",
              file=sys.stderr)
        return 2

    cam = None
    if not args.no_airsim:
        cam = AirSimCamera(args.airsim_host, args.airsim_port,
                           args.camera, args.vehicle)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(8)
    tag = " [placeholder-only]" if args.no_airsim else ""
    print(f"[airsim-mjpeg] MJPEG on {args.host}:{args.port} (boundary={BOUNDARY}) "
          f"← AirSim {args.airsim_host}:{args.airsim_port} cam={args.camera}{tag}",
          flush=True)
    print(f"[airsim-mjpeg] укажи пульту: "
          f"BAS_FPV_UPSTREAM_HOST=127.0.0.1 BAS_FPV_UPSTREAM_PORT={args.port}",
          flush=True)
    try:
        while True:
            conn, _addr = srv.accept()
            threading.Thread(target=serve_client, args=(conn, cam, args.fps),
                             daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
