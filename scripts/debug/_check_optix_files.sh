#!/usr/bin/env bash
echo "=== WSL native libs (/usr/lib/wsl/lib/) ==="
ls -la /usr/lib/wsl/lib/ | grep -iE 'nvoptix|ptxjit|rtcore|gpucomp'
echo "=== nvoptix.bin (search) ==="
ls -la /usr/lib/wsl/lib/nvoptix.bin 2>&1 || echo "(not found in /usr/lib/wsl/lib)"
echo
echo "=== Windows-side (/mnt/c/Windows/System32/lxss/lib/) ==="
ls -la /mnt/c/Windows/System32/lxss/lib/ | grep -iE 'nvoptix|ptxjit|rtcore|gpucomp'
echo
echo "=== Driver version ==="
nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits
echo
echo "=== Available OptiX symbols in libnvoptix ==="
nm -D /usr/lib/wsl/lib/libnvoptix.so.1 2>/dev/null | grep -i optix | head -10
