#!/usr/bin/env bash
# Search for "keytable" pattern
grep -n "keytable" /tmp/SIM_JSON.cpp
echo "==="
grep -n "DATA_VECTOR3F\|DATA_DOUBLE\|DATA_FLOAT\|imu\|gyro\|accel" /tmp/SIM_JSON.h 2>/dev/null | head -30
echo "==="
sed -n '1,80p' /tmp/SIM_JSON.h | head -80
