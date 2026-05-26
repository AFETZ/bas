#!/usr/bin/env bash
grep -n -B 1 -A 10 "sock.bind\|interface_set\|JSON::recv\|recv_fdm" /tmp/SIM_JSON.cpp | head -60
echo "==="
# Also check default ports.
grep -n -E "port_in|9002|9003|9001|target_port" /tmp/SIM_JSON.cpp | head -30
echo "==="
# Get header
curl -sf https://raw.githubusercontent.com/ArduPilot/ardupilot/master/libraries/SITL/SIM_JSON.h > /tmp/SIM_JSON.h
grep -n -E "port|9002|9003|9001" /tmp/SIM_JSON.h
echo "==="
# Check parent class for default ports
curl -sf https://raw.githubusercontent.com/ArduPilot/ardupilot/master/libraries/SITL/SIM_Aircraft.cpp > /tmp/SIM_Aircraft.cpp
grep -n -E "port_in|port_out|set_interface|9002|9003" /tmp/SIM_Aircraft.cpp | head -20
