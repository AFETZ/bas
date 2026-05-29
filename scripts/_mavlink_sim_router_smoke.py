#!/usr/bin/env python3
"""MAVLink sim router smoke — fanout 1 source → N sinks, verify frame count.

Тест без реального ArduPilot: генерируем synthetic MAVLink v2
HEARTBEAT frames, отправляем в router source, проверяем что N sinks
получают одинаковое количество frames.
"""
import socket
import sys
import os
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mavlink_sim_router import (   # noqa: E402
    Router, SourceConfig, UdpOutSink, FileSink,
)


def craft_mavlink_v2_heartbeat(seq: int = 0) -> bytes:
    """Минимальный валидный MAVLink v2 HEARTBEAT (msgid=0, payload=9B).

    Wire format: STX(1) LEN(1) INC(1) CMP(1) SEQ(1) SYS(1) COMP(1) MSGID(3) PAYLOAD(9) CRC(2)
    Итого 20 байт.
    """
    payload = bytes([
        0x06, 0x00, 0x00, 0x00,   # custom_mode = 0
        0x02,                       # type = MAV_TYPE_QUADROTOR
        0x03,                       # autopilot = MAV_AUTOPILOT_ARDUPILOTMEGA
        0x00,                       # base_mode
        0x00,                       # system_status
        0x03,                       # mavlink_version
    ])
    header = bytes([
        0xFD,                  # STX v2
        len(payload),          # LEN
        0x00,                  # incompat_flags
        0x00,                  # compat_flags
        seq & 0xFF,            # seq
        0x01,                  # sysid
        0x01,                  # compid
        0x00, 0x00, 0x00,      # msgid (0 = HEARTBEAT, little-endian 3B)
    ])
    crc = bytes([0xAB, 0xCD])  # router не валидирует CRC; нам нужна только длина
    return header + payload + crc


def receiver(host: str, port: int, results: dict, key: str) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((host, port))
    s.settimeout(2.0)
    count = 0
    bytes_recv = 0
    while True:
        try:
            data, _addr = s.recvfrom(2048)
        except socket.timeout:
            break
        count += 1
        bytes_recv += len(data)
    s.close()
    results[key] = {"count": count, "bytes": bytes_recv}


def main() -> int:
    SRC_PORT = 17550
    SINK_A_PORT = 17551
    SINK_B_PORT = 17552
    FILE_LOG = Path("/tmp/_router_smoke.mav")
    if FILE_LOG.exists():
        FILE_LOG.unlink()

    sinks = [
        UdpOutSink("127.0.0.1", SINK_A_PORT),
        UdpOutSink("127.0.0.1", SINK_B_PORT),
        FileSink(FILE_LOG),
    ]
    router = Router(src=SourceConfig(spec="udpin:127.0.0.1", host="127.0.0.1", port=SRC_PORT),
                    sinks=sinks, stats_every_s=10.0)

    # Start receivers первыми.
    results: dict = {}
    rcv_a = threading.Thread(target=receiver,
                             args=("127.0.0.1", SINK_A_PORT, results, "A"))
    rcv_b = threading.Thread(target=receiver,
                             args=("127.0.0.1", SINK_B_PORT, results, "B"))
    rcv_a.start(); rcv_b.start()
    time.sleep(0.2)

    # Start router.
    t = threading.Thread(target=router.run, daemon=True)
    t.start()
    time.sleep(0.2)

    # Generate 25 HEARTBEATs.
    pub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    N = 25
    for seq in range(N):
        pub.sendto(craft_mavlink_v2_heartbeat(seq), ("127.0.0.1", SRC_PORT))
        time.sleep(0.01)
    pub.close()

    time.sleep(0.5)

    # Wait for receivers to time out (collects all).
    rcv_a.join(timeout=3.0)
    rcv_b.join(timeout=3.0)

    router.stop()
    t.join(timeout=2.0)

    a = results.get("A", {})
    b = results.get("B", {})
    file_bytes = FILE_LOG.stat().st_size if FILE_LOG.exists() else 0

    FRAME_BYTES = 21   # MAVLink v2 HEARTBEAT: 10B header + 9B payload + 2B CRC
    print(f"Sent: {N} frames × {FRAME_BYTES}B = {N*FRAME_BYTES}B")
    print(f"  Sink A (udp:{SINK_A_PORT}): count={a.get('count')}  bytes={a.get('bytes')}")
    print(f"  Sink B (udp:{SINK_B_PORT}): count={b.get('count')}  bytes={b.get('bytes')}")
    print(f"  Sink F (file): bytes={file_bytes}")
    print(f"  Router state:  frames_in={router._stats.n_frames_in}  bytes_in={router._stats.n_bytes_in}")

    # Assertions.
    assert router._stats.n_frames_in == N, \
        f"router got {router._stats.n_frames_in}, expected {N}"
    assert a.get("count") == N, f"sink A got {a.get('count')}, expected {N}"
    assert b.get("count") == N, f"sink B got {b.get('count')}, expected {N}"
    assert file_bytes == N * FRAME_BYTES, \
        f"file got {file_bytes}B, expected {N*FRAME_BYTES}"
    assert router._stats.n_resync == 0, \
        f"router resync events: {router._stats.n_resync} (expected 0)"
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
