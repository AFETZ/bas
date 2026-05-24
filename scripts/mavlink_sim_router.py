#!/usr/bin/env python3
"""MAVLink ↔ Gazebo / AirSim sim router.

Закрывает пункт ТЗ "MAVLink ↔ Gazebo / AirSim" (зона Федотенкова А.А.).

Архитектура (single-source fanout):

  ArduPilot SITL  ┐
  (udpin:14550)   │
                  ▼
          ┌─────────────────────┐
          │  mavlink_sim_router │
          │  - parse heartbeat  │
          │  - filter sysid     │
          │  - mirror N→N       │
          └─────────────────────┘
            │     │     │     │
            ▼     ▼     ▼     ▼
          GCS  Gazebo AirSim  Recorder
          MAVP plugin adapter (JSONL)
          (14551)(14552)(14553)(file)

Router принимает MAVLink frames с одного source endpoint и эмиттит их
на N sink endpoints (UDP unicast, broadcast, multicast или файл).
Это базовый pattern для headless multi-component sim запуска без
mavlink-router C++ dependency (легче собрать / в Python venv).

Каждый sink — независимый. Если AirSim adapter упал, GCS продолжает
получать поток. Heartbeat считаются для observability.

Wire transparency: router НЕ парсит payload и НЕ изменяет MAVLink
frames; только демультиплексирует. MAVLink v1 и v2 поддерживаются
одинаково (определяются по first byte: 0xFE = v1, 0xFD = v2).

Использование:

  # Fanout SITL → MAVProxy GCS + Gazebo + AirSim adapter + log:
  ./mavlink_sim_router.py \
      --source=udpin:127.0.0.1:14550 \
      --sink=udpout:127.0.0.1:14551 \
      --sink=udpout:127.0.0.1:14552 \
      --sink=udpout:127.0.0.1:14553 \
      --sink=file:logs/router_$(date +%s).mav

  # Stage 2.4 production setup:
  #   - SITL → 14550 (autopilot output)
  #   - sink 14551 → MAVProxy (operator console)
  #   - sink 14552 → ardupilot_gazebo plugin
  #   - sink 14553 → mavlink_airsim_adapter.py
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAVLINK_V1_STX = 0xFE
MAVLINK_V2_STX = 0xFD


# ---------------------------------------------------------------------------
# Sink abstractions
# ---------------------------------------------------------------------------
class Sink:
    """Interface for one output destination."""
    def send(self, frame: bytes) -> None: ...
    def close(self) -> None: ...
    @property
    def descr(self) -> str: ...


class UdpOutSink(Sink):
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._n_sent = 0

    def send(self, frame: bytes) -> None:
        try:
            self._sock.sendto(frame, (self.host, self.port))
            self._n_sent += 1
        except OSError:
            pass

    def close(self) -> None:
        self._sock.close()

    @property
    def descr(self) -> str:
        return f"udpout:{self.host}:{self.port}  (sent={self._n_sent})"


class FileSink(Sink):
    """Append raw MAVLink frames to file (binary append, no decoration).
    Useful для post-mortem замены через mavlogdump или mavparser."""
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("ab")
        self._n_written = 0

    def send(self, frame: bytes) -> None:
        try:
            self._fh.write(frame)
            self._n_written += 1
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._fh.flush()
            self._fh.close()
        except OSError:
            pass

    @property
    def descr(self) -> str:
        return f"file:{self.path}  (written={self._n_written})"


def parse_sink_spec(spec: str) -> Sink:
    """spec: 'udpout:host:port' | 'file:/path/to/file'."""
    if spec.startswith("udpout:"):
        rest = spec[len("udpout:"):]
        host, _, port = rest.rpartition(":")
        return UdpOutSink(host or "127.0.0.1", int(port))
    if spec.startswith("file:"):
        return FileSink(Path(spec[len("file:"):]))
    raise ValueError(f"unknown sink spec: {spec!r} (use udpout:host:port or file:path)")


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------
@dataclass
class SourceConfig:
    spec: str
    host: str = "0.0.0.0"
    port: int = 14550


def parse_source_spec(spec: str) -> SourceConfig:
    """spec: 'udpin:host:port' — bind для приёма SITL."""
    if not spec.startswith("udpin:"):
        raise ValueError(f"only udpin: source supported, got {spec!r}")
    rest = spec[len("udpin:"):]
    host, _, port = rest.rpartition(":")
    return SourceConfig(spec=spec, host=host or "0.0.0.0", port=int(port))


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------
def _frame_length(buf: bytes) -> int | None:
    """Возвращает длину frame'а если есть полный, иначе None.

    MAVLink v1 frame: STX(1) LEN(1) SEQ(1) SYS(1) COMP(1) MSGID(1) PAYLOAD(LEN) CRC(2)
    Итого 8 + LEN байт.

    MAVLink v2 frame: STX(1) LEN(1) INC_FLG(1) CMP_FLG(1) SEQ(1) SYS(1) COMP(1) MSGID(3) PAYLOAD(LEN) CRC(2) [SIG(13)]
    Итого 12 + LEN + (13 если incompat_flags & 0x01) байт.
    """
    if not buf:
        return None
    stx = buf[0]
    if stx == MAVLINK_V1_STX:
        if len(buf) < 2:
            return None
        return 8 + buf[1]
    if stx == MAVLINK_V2_STX:
        if len(buf) < 3:
            return None
        plen = buf[1]
        incompat = buf[2]
        total = 12 + plen + (13 if (incompat & 0x01) else 0)
        return total
    return -1   # invalid STX, caller should advance 1 byte


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
@dataclass
class RouterStats:
    n_frames_in: int = 0
    n_bytes_in: int = 0
    n_resync: int = 0
    by_msgid: dict[int, int] = field(default_factory=dict)


class Router:
    def __init__(self, src: SourceConfig, sinks: list[Sink],
                 stats_every_s: float = 5.0) -> None:
        self.src = src
        self.sinks = sinks
        self.stats_every_s = stats_every_s
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((src.host, src.port))
        self._sock.settimeout(0.5)
        self._buf = bytearray()
        self._stats = RouterStats()
        self._stop = threading.Event()
        self._t_last_stats = time.time()

    def _fanout(self, frame: bytes) -> None:
        # Parse msgid for stats (transparent for forwarding).
        if frame and frame[0] == MAVLINK_V1_STX and len(frame) >= 6:
            msgid = frame[5]
        elif frame and frame[0] == MAVLINK_V2_STX and len(frame) >= 10:
            msgid = frame[7] | (frame[8] << 8) | (frame[9] << 16)
        else:
            msgid = -1
        self._stats.by_msgid[msgid] = self._stats.by_msgid.get(msgid, 0) + 1
        for sk in self.sinks:
            sk.send(frame)

    def _drain_buffer(self) -> None:
        while self._buf:
            n = _frame_length(bytes(self._buf[:13]))
            if n is None:
                return   # need more bytes
            if n < 0:
                # Invalid STX — skip one byte.
                self._buf.pop(0)
                self._stats.n_resync += 1
                continue
            if len(self._buf) < n:
                return
            frame = bytes(self._buf[:n])
            del self._buf[:n]
            self._fanout(frame)
            self._stats.n_frames_in += 1

    def _maybe_log_stats(self) -> None:
        now = time.time()
        if now - self._t_last_stats < self.stats_every_s:
            return
        self._t_last_stats = now
        top = sorted(self._stats.by_msgid.items(),
                     key=lambda kv: -kv[1])[:5]
        topstr = " ".join(f"id={mid}:{n}" for mid, n in top)
        print(f"[router] frames_in={self._stats.n_frames_in}  "
              f"bytes_in={self._stats.n_bytes_in}  "
              f"resync={self._stats.n_resync}  top: {topstr}")
        for sk in self.sinks:
            print(f"  → {sk.descr}")

    def run(self) -> None:
        print(f"[router] listening on {self.src.spec}, "
              f"{len(self.sinks)} sinks:")
        for sk in self.sinks:
            print(f"  → {sk.descr}")
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(65535)
            except socket.timeout:
                self._maybe_log_stats()
                continue
            except OSError:
                break
            self._stats.n_bytes_in += len(data)
            self._buf.extend(data)
            self._drain_buffer()
            self._maybe_log_stats()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        for sk in self.sinks:
            sk.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source", default="udpin:127.0.0.1:14550",
                   help="MAVLink source (only udpin: supported)")
    p.add_argument("--sink", action="append", default=[],
                   help="Sink spec: udpout:host:port или file:path. Можно несколько раз.")
    p.add_argument("--max-seconds", type=int, default=0)
    p.add_argument("--stats-every-s", type=float, default=5.0)
    args = p.parse_args()

    if not args.sink:
        print("ERROR: no --sink specified", file=sys.stderr)
        return 2

    src = parse_source_spec(args.source)
    sinks = [parse_sink_spec(s) for s in args.sink]
    router = Router(src=src, sinks=sinks, stats_every_s=args.stats_every_s)

    t = threading.Thread(target=router.run, daemon=True)
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
        router.stop()
        t.join(timeout=2.0)
        print(f"\n[done] total frames={router._stats.n_frames_in}  "
              f"bytes={router._stats.n_bytes_in}  resync={router._stats.n_resync}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
