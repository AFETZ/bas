#!/usr/bin/env bash
# Final fix per mitsuba-renderer/mitsuba3#1665 + drjit#355:
#   1. nvoptix.bin → /usr/share/nvidia/ (default OptiX search path)
#   2. libnvidia-rtcore.so + libnvoptix.so → /usr/lib/x86_64-linux-gnu/
#   3. apt install libnvidia-gl-XXX matching driver if available
#   4. Test Sionna live mode
set -e

OPTIX_SRC=/opt/optix-real
TARGET_BIN_DIR=/usr/share/nvidia
TARGET_LIB_DIR=/usr/lib/x86_64-linux-gnu

if [ ! -d "$OPTIX_SRC" ]; then
    echo "[!] $OPTIX_SRC not found — run install_mitsuba_optix_wsl.sh first"
    exit 1
fi

echo "==> [1] Stage nvoptix.bin → $TARGET_BIN_DIR/ (Mitsuba/OptiX default path)"
echo 1337 | sudo -S mkdir -p "$TARGET_BIN_DIR" 2>/dev/null
echo 1337 | sudo -S cp -v "$OPTIX_SRC/nvoptix.bin" "$TARGET_BIN_DIR/nvoptix.bin"

echo "==> [2] Stage OptiX libs → $TARGET_LIB_DIR/"
for f in libnvoptix.so.1 libnvidia-rtcore.so.595.71.05 libnvidia-ptxjitcompiler.so.1; do
    if [ -f "$OPTIX_SRC/$f" ]; then
        echo 1337 | sudo -S cp -v "$OPTIX_SRC/$f" "$TARGET_LIB_DIR/$f"
    fi
done
echo 1337 | sudo -S ln -sf libnvidia-rtcore.so.595.71.05 "$TARGET_LIB_DIR/libnvidia-rtcore.so.1" 2>/dev/null || true

echo "==> [3] ldconfig refresh"
echo 1337 | sudo -S ldconfig

echo "==> [4] Verify Mitsuba RadioMapSolver pipeline minimal scene"
cd /home/afetz/bas-prototype
# Don't override DRJIT_LIBOPTIX_PATH — let it find default /usr/lib path.
export MITSUBA_VARIANT=cuda_ad_mono
sionna_env/bin/python scripts/_sionna_cuda_minimal.py 2>&1 | tail -15
