#!/usr/bin/env bash
curl -sf https://raw.githubusercontent.com/ArduPilot/ardupilot/master/libraries/SITL/SIM_JSON.h -o /tmp/SIM_JSON.h
echo "=== header file size ==="
ls -la /tmp/SIM_JSON.h
echo
echo "=== matches ==="
grep -n -E "keytable|imu_|gyro|accel_body|VECTOR3F|VECTOR3D|DATA_FLOAT|attitude" /tmp/SIM_JSON.h | head -50
echo
echo "=== full file ==="
cat /tmp/SIM_JSON.h
