#!/usr/bin/env python3
"""ArduPilot ↔ AirSim interface smoke — JSON-FDM round-trip без real SITL.

Имитирует ArduPilot SITL: шлёт JSON-FDM PWM packets в bridge,
проверяет что bridge отвечает sensor packets на правильном порту.
AirSim в этом тесте — отключён (--no-airsim), bridge работает в
"hover" mode и должен всё равно генерировать valid response.
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/scripts")

from arducopter_airsim_interface import (   # noqa: E402
    JsonFdmBridge, ARDUPILOT_AIRSIM_FDM_MAGIC,
)


def main() -> int:
    FDM_IN = 19003
    FDM_OUT = 19002
    LOG_PATH = Path("/tmp/_arducopter_airsim_smoke.jsonl")
    if LOG_PATH.exists():
        LOG_PATH.unlink()

    # Bridge без AirSim → fallback hover dynamics (zeros pose, gravity accel).
    bridge = JsonFdmBridge(
        fdm_in_port=FDM_IN, fdm_out_port=FDM_OUT,
        airsim=None, log_path=LOG_PATH,
    )

    # Bind SITL emulator socket BEFORE starting bridge (так bridge сможет
    # отправить response даже на первый кадр).
    sitl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sitl_sock.bind(("127.0.0.1", FDM_OUT))
    sitl_sock.settimeout(2.0)

    t = threading.Thread(target=bridge.run, daemon=True)
    t.start()
    time.sleep(0.3)

    # Synthetic SITL: шлём 50 PWM frames с increment'ом frame_count.
    pub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    N = 50
    responses = []
    for fc in range(N):
        pwm = [1500] * 16
        if fc < 25:
            # Stage 1: middle throttle (1500)
            pass
        else:
            # Stage 2: arm-throttle (1700)
            pwm = [1700] * 4 + [1500] * 12
        packet = {
            "magic": ARDUPILOT_AIRSIM_FDM_MAGIC,
            "frame_rate": 1200,
            "frame_count": fc,
            "pwm": pwm,
        }
        pub.sendto((json.dumps(packet) + "\n").encode(),
                   ("127.0.0.1", FDM_IN))
        # Try receive response.
        try:
            data, _ = sitl_sock.recvfrom(2048)
            resp = json.loads(data.decode())
            responses.append(resp)
        except (socket.timeout, json.JSONDecodeError):
            pass
        time.sleep(0.005)

    pub.close()
    time.sleep(0.3)
    bridge.stop()
    t.join(timeout=2.0)
    sitl_sock.close()

    print(f"Sent: {N} PWM frames")
    print(f"Received: {len(responses)} sensor responses")
    if responses:
        r0 = responses[0]
        rN = responses[-1]
        print(f"  First response: timestamp={r0.get('timestamp'):.4f}s "
              f"pos={r0.get('position')}")
        print(f"  Last response:  timestamp={rN.get('timestamp'):.4f}s "
              f"pos={rN.get('position')}")
        print(f"  IMU sample: gyro={r0.get('imu', {}).get('gyro')}  "
              f"accel={r0.get('imu', {}).get('accel_body')}")

    # Assertions: bridge должен ответить минимум на 80% packets
    # (UDP может терять; для loopback ожидаем 100%).
    assert len(responses) >= int(N * 0.8), \
        f"too few responses: {len(responses)} / {N}"
    # Каждый response должен содержать timestamp, imu, position, attitude, velocity.
    for r in responses:
        for k in ("timestamp", "imu", "position", "attitude", "velocity"):
            assert k in r, f"response missing key {k!r}: {r}"
        assert isinstance(r["imu"].get("gyro"), list)
        assert len(r["position"]) == 3
        assert len(r["attitude"]) == 3
        assert len(r["velocity"]) == 3
        # Sanity: accel_body z ≈ -9.81 (hover, no AirSim feedback).
        assert abs(r["imu"]["accel_body"][2] + 9.81) < 0.01, \
            f"accel z != -9.81: {r['imu']['accel_body']}"
    print(f"\nLog @ {LOG_PATH} ({LOG_PATH.stat().st_size} bytes)")
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
