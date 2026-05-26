#!/usr/bin/env bash
# Look for keytable struct definition (not just usage).
grep -n "DATA_VECTOR3F\|DATA_DOUBLE\|DATA_FLOAT" /tmp/SIM_JSON.cpp | head -30
echo "==="
# Look between lines for keytable = { ... }.
sed -n '40,150p' /tmp/SIM_JSON.cpp
