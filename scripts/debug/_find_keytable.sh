#!/usr/bin/env bash
grep -n -B 1 -A 30 "keytable\[" /tmp/SIM_JSON.cpp | head -60
