#!/usr/bin/env bash
echo "=== ldconfig libcuda ==="
ldconfig -p | grep libcuda
echo
echo "=== ldd cuda lookup из drjit ==="
find /home/afetz/bas-prototype/sionna_env -name "drjit*.so*" 2>/dev/null | head -3
echo
echo "=== WSL native cuda ==="
ls -la /usr/lib/wsl/lib/libcuda* 2>&1
echo
echo "=== /etc/ld.so.conf.d/ld.wsl.conf ==="
cat /etc/ld.so.conf.d/ld.wsl.conf 2>&1
echo
echo "=== /lib/x86_64-linux-gnu/libcuda* ==="
ls -la /lib/x86_64-linux-gnu/libcuda* /usr/lib/x86_64-linux-gnu/libcuda* 2>&1 | head -10
echo
echo "=== which libcuda is loaded by python ==="
/home/afetz/bas-prototype/sionna_env/bin/python -c "import ctypes; print(ctypes.CDLL('libcuda.so.1')._name)" 2>&1
