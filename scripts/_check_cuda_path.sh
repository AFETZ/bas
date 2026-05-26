#!/usr/bin/env bash
echo "=== libcuda search ==="
ldconfig -p | grep -E "libcuda|libnvidia" | head -20
echo
echo "=== /etc/ld.so.conf.d/ ==="
ls /etc/ld.so.conf.d/ 2>&1
echo
echo "=== /etc/ld.so.conf.d/x86_64-linux-gnu_GL.conf or wsl.conf ==="
cat /etc/ld.so.conf.d/*wsl* 2>&1 || echo "no wsl conf"
cat /etc/ld.so.conf.d/x86_64-linux-gnu_GL.conf 2>&1 || echo "no GL conf"
echo
echo "=== nvidia-smi ==="
nvidia-smi 2>&1 | head -5
echo
echo "=== ldd /usr/lib/x86_64-linux-gnu/libnvoptix.so.1 ==="
ldd /usr/lib/x86_64-linux-gnu/libnvoptix.so.1 2>&1 | head -10
