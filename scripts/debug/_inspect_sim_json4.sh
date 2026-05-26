#!/usr/bin/env bash
echo "=== JSON parser in SIM_JSON ==="
grep -n -B 1 -A 8 "parse\|json\|imu\|gyro\|accel_body\|timestamp" /tmp/SIM_JSON.cpp | head -80
echo
echo "=== example send back format ==="
grep -n -B 2 -A 30 "parse_sensors\|sensors_buffer\|payload" /tmp/SIM_JSON.cpp | head -100
