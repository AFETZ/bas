#!/usr/bin/env bash
grep -n -B 1 -A 20 "parse_array\|keytable" /tmp/SIM_JSON.cpp | head -100
