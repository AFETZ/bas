#!/usr/bin/env bash
# Test Sionna live mode после libnvidia-gl-595 install.
# Unset DRJIT_LIBOPTIX_PATH — let Mitsuba find default /usr/lib path.
unset DRJIT_LIBOPTIX_PATH
unset LD_LIBRARY_PATH

cd /home/afetz/bas-prototype
echo "==> Mitsuba minimal CUDA test"
sionna_env/bin/python -c "
import mitsuba as mi
mi.set_variant('cuda_ad_mono')
print(f'variant: {mi.variant()}')
print(f'version: {mi.MI_VERSION}')
" 2>&1 | tail -5

echo
echo "==> Sionna cuda_ad_mono minimal scene test"
sionna_env/bin/python scripts/_sionna_cuda_minimal.py 2>&1 | tail -20
