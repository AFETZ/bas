#!/usr/bin/env python3
"""ArduPilot ↔ AirSim JSON-FDM smoke с реальной multirotor физикой.

Имитирует SITL: шлёт PWM frames через 3 фазы, проверяет что реальный
6DOF physics integrator делает то что ожидается:

  Phase 1: motors off → drone на земле (z_down ≈ 0)
  Phase 2: hover PWM + slight climb → vehicle поднимается на высоту
  Phase 3: differential CCW/CW thrust → yaw rotation

AirSim в этом тесте отключён (--no-airsim); физика реальная и
независимая от AirSim.
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
from multirotor_dynamics import MultirotorDynamics   # noqa: E402


def main() -> int:
    FDM_IN = 19003
    FDM_OUT = 19002
    LOG_PATH = Path("/tmp/_arducopter_airsim_smoke.jsonl")
    if LOG_PATH.exists():
        LOG_PATH.unlink()

    bridge = JsonFdmBridge(
        fdm_in_port=FDM_IN, fdm_out_port=FDM_OUT,
        airsim=None, log_path=LOG_PATH,
        airsim_visual_sync=False,
    )

    sitl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sitl_sock.bind(("127.0.0.1", FDM_OUT))
    sitl_sock.settimeout(2.0)

    t = threading.Thread(target=bridge.run, daemon=True)
    t.start()
    time.sleep(0.3)

    pub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responses: list[dict] = []

    def _send(pwm: list, fc: int) -> dict | None:
        packet = {"magic": ARDUPILOT_AIRSIM_FDM_MAGIC,
                  "frame_rate": 1200, "frame_count": fc, "pwm": pwm}
        pub.sendto((json.dumps(packet) + "\n").encode(), ("127.0.0.1", FDM_IN))
        try:
            data, _ = sitl_sock.recvfrom(2048)
            r = json.loads(data.decode())
            responses.append(r)
            return r
        except (socket.timeout, json.JSONDecodeError):
            return None

    hover_pwm = MultirotorDynamics().hover_pwm_estimate
    print(f"hover PWM estimate = {hover_pwm}")

    # --- Phase 1: motors off → ground rest ---
    print("\n[Phase 1] motors off (PWM=1000) × 40 ticks → expect z_down ≈ 0")
    for fc in range(40):
        _send([1000] * 16, fc)
        time.sleep(0.005)
    pos = responses[-1]["position"]
    print(f"    pos={pos}")
    assert abs(pos[2]) < 1.0, f"vehicle not at ground: z_down={pos[2]}"

    # --- Phase 2: climb (PWM > hover) → altitude gain ---
    # excess thrust = 400μs/1000μs × (4 × 6N) = 9.6N extra ≈ 6.4 m/s² accel.
    # 200 ticks @ 5ms = 1s → expect ~3.2m climb.
    print(f"\n[Phase 2] climb PWM={hover_pwm + 400} × 200 ticks → expect alt > 2m")
    for fc in range(40, 240):
        _send([hover_pwm + 400] * 4 + [1500] * 12, fc)
        time.sleep(0.005)
    pos = responses[-1]["position"]
    alt = -pos[2]
    print(f"    pos={pos}  alt={alt:.2f}m")
    assert alt > 2.0, f"didn't climb above 2m: alt={alt}"

    # --- Phase 3: yaw spin via differential motor thrust ---
    yaw_before = responses[-1]["attitude"][2]
    print(f"\n[Phase 3] yaw spin: M1+M2 (CCW)↑ M3+M4 (CW)↓ × 100 ticks")
    print(f"    yaw_before = {yaw_before:.3f} rad")
    for fc in range(240, 340):
        _send([hover_pwm + 300, hover_pwm + 300,
               hover_pwm + 100, hover_pwm + 100] + [1500] * 12, fc)
        time.sleep(0.005)
    yaw_after = responses[-1]["attitude"][2]
    yaw_delta = yaw_after - yaw_before
    print(f"    yaw_after  = {yaw_after:.3f} rad")
    print(f"    Δyaw       = {yaw_delta:+.3f} rad ({yaw_delta * 180 / 3.14159:+.1f}°)")
    assert abs(yaw_delta) > 0.05, f"no yaw rotation: Δ={yaw_delta}"

    pub.close()
    time.sleep(0.3)
    bridge.stop()
    t.join(timeout=2.0)
    sitl_sock.close()

    print(f"\nTotal: 340 PWM frames sent, {len(responses)} sensor responses")
    assert len(responses) >= 300, f"too few responses: {len(responses)}"
    for r in responses:
        for k in ("timestamp", "imu", "position", "attitude", "velocity"):
            assert k in r, f"response missing {k}: {r}"
        assert len(r["position"]) == 3
        assert len(r["attitude"]) == 3
        assert len(r["velocity"]) == 3

    print(f"Log @ {LOG_PATH} ({LOG_PATH.stat().st_size}B)")
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
