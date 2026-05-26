#!/usr/bin/env bash
sed -n '170,260p' /tmp/SIM_JSON.cpp
echo
echo "=== where is sensor data parsed (line 340-370) ==="
sed -n '340,370p' /tmp/SIM_JSON.cpp
