#!/usr/bin/env bash
# Verify OptiX init работает после nvoptix.bin install.
export DRJIT_LIBOPTIX_PATH=/opt/optix-real/libnvoptix.so.1
export LD_LIBRARY_PATH=/opt/optix-real:/usr/lib/wsl/lib
export MITSUBA_VARIANT=cuda_ad_mono

echo "=== Env ==="
echo "DRJIT_LIBOPTIX_PATH=$DRJIT_LIBOPTIX_PATH"
echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
echo "MITSUBA_VARIANT=$MITSUBA_VARIANT"
echo

echo "=== [1] Mitsuba variant test ==="
/home/afetz/bas-prototype/sionna_env/bin/python <<EOF
import mitsuba as mi
mi.set_variant("cuda_ad_mono")
print("variant:", mi.variant())
EOF

echo
echo "=== [2] Sionna live tile (cuda_ad_mono) ==="
cd /home/afetz/bas-prototype
sionna_env/bin/python scripts/sionna_real_tile.py \
    --mode live --tile-i 0 --tile-j 0 --tile-size-m 100 \
    --cell-size-m 20 --freq-mhz 2400 2>&1 | tail -25
