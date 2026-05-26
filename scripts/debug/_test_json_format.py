#!/usr/bin/env python3
"""Confirm exact bytes bridge sends к SITL."""
import json
payload = {
    "timestamp": 0.005,
    "imu": {"gyro": [0.0, 0.0, 0.0], "accel_body": [0.0, 0.0, -9.81]},
    "position": [0.0, 0.0, 0.0],
    "attitude": [0.0, 0.0, 0.0],
    "velocity": [0.0, 0.0, 0.0],
}
data = (json.dumps(payload) + "\n").encode() + b"\x00"
print(f"size: {len(data)}")
print(f"hex first 80: {data[:80].hex()}")
print(f"text first 200: {data[:200]!r}")
print()
# Test parse_array logic manually на этом тексте:
text = data.decode("utf-8", errors="replace")
print(f"strstr 'imu' at pos {text.find('imu')}")
print(f"strstr 'gyro' at pos {text.find('gyro')}")
imu_p = text.find("imu") + 4   # +strlen("imu")+1
print(f"after 'imu' + 4: text[{imu_p}:{imu_p+10}!r] = {text[imu_p:imu_p+10]!r}")
gyro_p = text.find("gyro", imu_p) + 6   # +strlen("gyro")+2
print(f"after 'gyro' + 6: text[{gyro_p}:{gyro_p+15}!r] = {text[gyro_p:gyro_p+15]!r}")
