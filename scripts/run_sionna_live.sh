#!/usr/bin/env bash
# Wrapper для Sionna RT live mode на WSL2.
#
# Solves 3 issues:
#   1. WSL libcuda.so.1 priority через LD_LIBRARY_PATH=/usr/lib/wsl/lib first
#   2. Full libnvoptix.so.595.71.05 (not 10KB stub) через LD_PRELOAD
#   3. DRJIT_LIBOPTIX_PATH explicit pointer
#
# Pre-requisites:
#   * apt install libnvidia-gl-595 (matches Linux driver R595)
#   * /usr/share/nvidia/nvoptix.bin (auto-installed by libnvidia-gl-595)
#   * /usr/lib/x86_64-linux-gnu/libnvoptix.so.595.71.05 (49MB)
#   * /usr/lib/x86_64-linux-gnu/libnvidia-rtcore.so.595.71.05 (42MB)
#
# Usage:
#   bash scripts/run_sionna_live.sh                       # minimal test
#   bash scripts/run_sionna_live.sh real_tile             # iris_runway tile
#   bash scripts/run_sionna_live.sh -- python custom.py   # custom command
set -e

OPTIX_LIB=/usr/lib/x86_64-linux-gnu/libnvoptix.so.595.71.05

if [ ! -f "$OPTIX_LIB" ]; then
    echo "[!] libnvoptix.so.595.71.05 not installed. Run:"
    echo "    sudo apt install libnvidia-gl-595"
    echo "  Or:  bash scripts/install_mitsuba_optix_wsl.sh"
    exit 1
fi

export LD_LIBRARY_PATH=/usr/lib/wsl/lib
export LD_PRELOAD="$OPTIX_LIB"
export DRJIT_LIBOPTIX_PATH="$OPTIX_LIB"
export MITSUBA_VARIANT=cuda_ad_mono_polarized

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

case "${1:-minimal}" in
    minimal)
        echo "==> Sionna live mode — minimal floor_wall test"
        sionna_env/bin/python scripts/_sionna_polarized_test.py
        ;;
    real_tile)
        shift || true
        echo "==> Sionna live mode — iris_runway tile compute"
        sionna_env/bin/python scripts/sionna_real_tile.py --mode live "$@"
        ;;
    --)
        shift
        exec "$@"
        ;;
    *)
        echo "Unknown mode: $1"
        echo "Usage: $0 [minimal | real_tile [--tile-i N --tile-j N ...] | -- <cmd>]"
        exit 2
        ;;
esac
