#!/usr/bin/env bash
curl -sf https://raw.githubusercontent.com/ArduPilot/ardupilot/master/libraries/SITL/SIM_JSON.cpp \
    > /tmp/SIM_JSON.cpp 2>&1
echo "=== Lines with port / bind / sendto in SIM_JSON.cpp ==="
grep -n -E "port|bind|sendto|recvfrom|9002|9003" /tmp/SIM_JSON.cpp | head -40
echo
echo "=== Init / constructor ==="
grep -n -A 5 "JSON::JSON\|sock_in\|sock_out" /tmp/SIM_JSON.cpp | head -40
