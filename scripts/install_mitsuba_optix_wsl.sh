#!/usr/bin/env bash
# Install OptiX libraries for Mitsuba 3 / Sionna RT in WSL2.
#
# WSL2 ships libnvoptix.so.1 как 10KB loader stub, который не имеет
# `optixQueryFunctionTable` symbol — нужны бинарные части из Linux
# NVIDIA driver того же version что Windows host.
#
# Mitsuba docs:
#   https://mitsuba.readthedocs.io/en/stable/src/optix_setup.html
#
# Steps:
#   1. detect Windows-side NVIDIA driver version (from nvidia-smi)
#   2. download matching Linux .run installer from NVIDIA archive
#   3. extract без install: `bash NVIDIA-Linux-...run -x --target driver`
#   4. copy libnvoptix.so.*, libnvidia-ptxjitcompiler.so.*,
#      libnvidia-rtcore.so.*, nvoptix.bin → /mnt/c/Windows/System32/lxss/lib/
#      (требует Windows admin write access)
#   5. `wsl --shutdown` from Windows terminal
#
# After completion: DRJIT_LIBOPTIX_PATH=/usr/lib/wsl/lib/libnvoptix.so.1
# должен resolve правильные symbols.
set -e

WIN_DRIVER_VER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | tr -d ' ')"
# Windows driver version (e.g. 595.79) ≠ Linux driver version (e.g. 595.71.05).
# Mapping per NVIDIA release notes:
#   595.79 Win  ↔ 595.71.05 Linux (R595 production branch, Mar 2026)
#   596.36 Win  ↔ 595.71.05 Linux (Data Center R595)
# Default к latest stable Linux R595 если auto-detect не настроен.
DRIVER_VER="${BAS_NVIDIA_DRIVER_VER:-595.71.05}"
WORK_DIR="${BAS_OPTIX_WORK_DIR:-/tmp/optix_install_$$}"
LXSS_LIB="${BAS_LXSS_LIB:-/mnt/c/Windows/System32/lxss/lib}"

echo "==> OptiX WSL2 installer"
echo "    Windows driver: $WIN_DRIVER_VER (per nvidia-smi)"
echo "    Linux driver:   $DRIVER_VER (R595 production branch)"
echo "    work dir:       $WORK_DIR"
echo "    lxss lib:       $LXSS_LIB"

# Sanity.
if [ ! -d "$LXSS_LIB" ]; then
    echo "[!] $LXSS_LIB не существует — это не WSL2 host?"
    exit 1
fi

# Check if уже установлено.
need_install=0
for f in nvoptix.bin libnvidia-rtcore.so libnvidia-ptxjitcompiler.so; do
    if ls "$LXSS_LIB"/$f* >/dev/null 2>&1; then
        echo "    [+] $f already present"
    else
        echo "    [!] $f MISSING"
        need_install=1
    fi
done
if [ "$need_install" = "0" ]; then
    echo "==> Все OptiX libs уже на месте — попробовать запустить Sionna live"
    exit 0
fi

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# 2. Download.
RUN_FILE="NVIDIA-Linux-x86_64-${DRIVER_VER}.run"
URL="https://download.nvidia.com/XFree86/Linux-x86_64/${DRIVER_VER}/${RUN_FILE}"
if [ ! -f "$RUN_FILE" ]; then
    echo "==> [1/4] download $URL (~300 MB)..."
    if ! curl -L --fail -o "$RUN_FILE" "$URL"; then
        # Older driver might be in archive layout.
        echo "    [!] primary URL failed, попробуем archive..."
        URL2="https://us.download.nvidia.com/XFree86/Linux-x86_64/${DRIVER_VER}/${RUN_FILE}"
        curl -L --fail -o "$RUN_FILE" "$URL2" || {
            echo "    [!] both URLs failed. Скачайте вручную:"
            echo "        https://www.nvidia.com/Download/Find.aspx?lang=en-us"
            echo "        Linux 64-bit, version $DRIVER_VER, → $WORK_DIR/$RUN_FILE"
            exit 1
        }
    fi
else
    echo "==> [1/4] $RUN_FILE already downloaded ($(du -h "$RUN_FILE" | cut -f1))"
fi

# 3. Extract.
if [ ! -d driver ]; then
    echo "==> [2/4] extract driver content (no install)..."
    bash "$RUN_FILE" -x --target driver 2>&1 | tail -5
fi

# 4. Verify expected files присутствуют в extracted driver.
for need in nvoptix.bin libnvidia-rtcore.so libnvidia-ptxjitcompiler.so libnvoptix.so libnvidia-gpucomp.so; do
    if ! ls driver/${need}* >/dev/null 2>&1; then
        echo "    [!] missing $need in extracted driver — wrong version?"
        ls driver/ | grep -iE 'optix|rtcore|ptxjit|gpucomp' | head -10
        exit 1
    fi
done

# 5. Stage в driver-dist/.
mkdir -p driver-dist
cp -v driver/nvoptix.bin driver-dist/
cp -v driver/libnvoptix.so.* driver-dist/libnvoptix.so.1
cp -v driver/libnvidia-rtcore.so.* driver-dist/
cp -v driver/libnvidia-ptxjitcompiler.so.* driver-dist/libnvidia-ptxjitcompiler.so.1
cp -v driver/libnvidia-gpucomp.so.* driver-dist/libnvidia-gpucomp.so

# 6. Copy в lxss/lib (требует admin rights).
echo "==> [3/4] copy staged files в $LXSS_LIB (Windows admin write)..."
copy_failed=0
for src in driver-dist/*; do
    target="${LXSS_LIB}/$(basename "$src")"
    if cp "$src" "$target" 2>/dev/null; then
        echo "    [+] $(basename "$src") → ok"
    else
        echo "    [!] $(basename "$src") → permission denied"
        copy_failed=1
    fi
done

if [ "$copy_failed" = "1" ]; then
    echo
    echo "==> [!] Copy in lxss/lib не удалось без admin."
    echo "    Решение 1: запустите этот script с sudo (требует Windows admin token):"
    echo "        sudo bash $0"
    echo
    echo "    Решение 2: откройте Windows PowerShell как админ + запустите:"
    echo "        cd \\\\wsl.localhost\\Ubuntu-Restore\\$WORK_DIR\\driver-dist"
    echo "        Copy-Item * 'C:\\Windows\\System32\\lxss\\lib\\' -Force"
    echo
    echo "    После copy в C:\\Windows\\System32\\lxss\\lib\\:"
    echo "        wsl --shutdown   (из Windows)"
    echo "        DRJIT_LIBOPTIX_PATH=/usr/lib/wsl/lib/libnvoptix.so.1 \\"
    echo "        MITSUBA_VARIANT=cuda_ad_mono python -c \\"
    echo "            'import mitsuba as mi; mi.set_variant(\"cuda_ad_mono\"); print(mi.variant())'"
    exit 2
fi

echo
echo "==> [4/4] DONE. Дальше:"
echo "    1. Из Windows terminal:  wsl --shutdown"
echo "    2. Re-open WSL2 + verify:"
echo "       DRJIT_LIBOPTIX_PATH=/usr/lib/wsl/lib/libnvoptix.so.1 \\"
echo "       MITSUBA_VARIANT=cuda_ad_mono /home/afetz/bas-prototype/sionna_env/bin/python -c \\"
echo "         'import mitsuba; mitsuba.set_variant(\"cuda_ad_mono\"); print(mitsuba.variant())'"
echo "    3. Run Sionna live tile:"
echo "       MITSUBA_VARIANT=cuda_ad_mono \\"
echo "       /home/afetz/bas-prototype/sionna_env/bin/python scripts/sionna_real_tile.py \\"
echo "         --mode live --tile-i 0 --tile-j 0 --freq-mhz 2400"
