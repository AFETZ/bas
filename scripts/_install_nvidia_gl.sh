#!/usr/bin/env bash
echo "==> Installing libnvidia-gl-595 (matches Linux driver 595.71.05)"
echo 1337 | sudo -S apt-get update 2>&1 | tail -3
echo "---"
# Install gl-595 + compute-595 + common-595 матчинг version.
echo 1337 | sudo -S apt-get install -y libnvidia-gl-595 2>&1 | tail -15
echo "---"
echo "=== Verify ==="
dpkg -l 2>&1 | grep nvidia | grep -E "gl|optix|rtcore" | head -10
echo
echo "=== /usr/share/nvidia/nvoptix.bin after package install ==="
ls -la /usr/share/nvidia/nvoptix.bin 2>&1
echo
echo "=== /usr/lib/x86_64-linux-gnu/libnvoptix* ==="
ls -la /usr/lib/x86_64-linux-gnu/libnvoptix* /usr/lib/x86_64-linux-gnu/libnvidia-rtcore* 2>&1 | head -5
