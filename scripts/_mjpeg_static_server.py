#!/usr/bin/env python3
"""Mini MJPEG server that loops a single image file — для CV smoke testing.

Имитирует /camera.mjpg endpoint Web GCS. Используется в CI smoke когда
полный FPV stack не запущен.

Usage:
  python3 scripts/_mjpeg_static_server.py /path/to/frame.jpg --port 8765
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

BOUNDARY = "frame"


def serve(host: str, port: int, jpeg_bytes: bytes) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(8)
    print(f"[mjpeg-static] listening {host}:{port}, frame={len(jpeg_bytes)} bytes",
          flush=True)
    try:
        while True:
            client, addr = s.accept()
            threading.Thread(
                target=_handle_client, args=(client, addr, jpeg_bytes),
                daemon=True,
            ).start()
    finally:
        s.close()


def _handle_client(client: socket.socket, addr, jpeg_bytes: bytes) -> None:
    try:
        client.settimeout(5.0)
        client.recv(4096)   # read+discard HTTP request
        headers = (
            "HTTP/1.0 200 OK\r\n"
            f"Content-Type: multipart/x-mixed-replace; boundary={BOUNDARY}\r\n"
            "Cache-Control: no-cache\r\n\r\n"
        )
        client.sendall(headers.encode("ascii"))
        # Loop frames @ ~10 fps
        i = 0
        while True:
            part = (
                f"--{BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg_bytes)}\r\n\r\n"
            ).encode("ascii") + jpeg_bytes + b"\r\n"
            client.sendall(part)
            i += 1
            time.sleep(0.1)
    except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
        pass
    finally:
        client.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="JPEG path для повтора")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    path = Path(args.image)
    jpeg = path.read_bytes()
    # Если входное PNG — переcoded в JPEG через PIL.
    if path.suffix.lower() == ".png":
        try:
            from PIL import Image   # type: ignore
            import io
            img = Image.open(io.BytesIO(jpeg)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            jpeg = buf.getvalue()
            print(f"[mjpeg-static] converted PNG → JPEG ({len(jpeg)} bytes)",
                  flush=True)
        except ImportError:
            print("[mjpeg-static] WARN: PNG passed but PIL not available",
                  file=sys.stderr, flush=True)
    serve(args.host, args.port, jpeg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
