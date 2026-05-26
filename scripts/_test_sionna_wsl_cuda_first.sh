#!/usr/bin/env bash
# Force WSL libcuda.so first (apt-installed Linux libcuda doesn't work in WSL2).
export LD_LIBRARY_PATH=/usr/lib/wsl/lib:$LD_LIBRARY_PATH
unset DRJIT_LIBOPTIX_PATH

cd /home/afetz/bas-prototype
echo "=== LD_LIBRARY_PATH = $LD_LIBRARY_PATH ==="
echo
echo "==> Mitsuba minimal CUDA test"
sionna_env/bin/python -c "
import mitsuba as mi
mi.set_variant('cuda_ad_mono')
print(f'variant: {mi.variant()}')
" 2>&1 | tail -5

echo
echo "==> Sionna cuda_ad_mono minimal scene (libnvidia-gl-595 + WSL libcuda)"
sionna_env/bin/python scripts/_sionna_cuda_minimal.py 2>&1 | tail -80
