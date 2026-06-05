#!/usr/bin/env bash
# Sionna RT GPU runtime evidence — доказывает РЕАЛЬНЫЙ GPU backend, не флаги.
# Тонкая обёртка: ставит OptiX-окружение (как run_sionna_live.sh) и зовёт
# scripts/check_sionna_gpu_runtime.py, который делает замеры и пишет отчёты:
#   reports/sionna_gpu_runtime_check.md / .json
#
# ВАЖНО: Sionna RT path solving идёт через Mitsuba/Dr.Jit (CUDA/OptiX), НЕ через
# TensorFlow. TF GPU здесь только диагностика. На WSL2 per-process GPU-память не
# видна — используем дельту ОБЩЕЙ GPU-памяти на реальном CUDA-render/path-solve.
#
# Usage: bash scripts/check_sionna_gpu_runtime.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO="$(pwd)"
PYBIN="${BAS_SIONNA_PYTHON:-$REPO/sionna_env/bin/python}"

OPTIX_LIB="${BAS_OPTIX_LIB:-/usr/lib/x86_64-linux-gnu/libnvoptix.so.595.71.05}"
export PATH="/usr/lib/wsl/lib:$PATH"
export LD_LIBRARY_PATH="/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
if [ -f "$OPTIX_LIB" ]; then
    export LD_PRELOAD="$OPTIX_LIB"
    export DRJIT_LIBOPTIX_PATH="$OPTIX_LIB"
fi
export MITSUBA_VARIANT="${MITSUBA_VARIANT:-cuda_ad_mono_polarized}"

echo "=========================================================================="
echo "  SIONNA RT GPU RUNTIME CHECK  (env -> check_sionna_gpu_runtime.py)"
echo "=========================================================================="
echo "  python:        $PYBIN"
echo "  MITSUBA_VARIANT(req): $MITSUBA_VARIANT"
echo "  LD_PRELOAD:    ${LD_PRELOAD:-<unset>}"
echo "=== nvidia-smi BEFORE ==="
nvidia-smi --query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total \
    --format=csv,noheader 2>&1 | head -2
echo

# TF-шум фильтруем, важные строки от check_sionna_gpu_runtime.py остаются.
"$PYBIN" "$REPO/scripts/check_sionna_gpu_runtime.py" 2>&1 \
  | grep -vE 'oneDNN|InitializeLog|cuFFT|cuDNN|cuBLAS|computation_placer|cpu_feature_guard|To enable|rebuild TensorFlow|absl::|Skipping registering|Cannot dlopen|dlopen some|Could not find cuda|cuda_executor|gpu_device|HDRFilm'
RC=${PIPESTATUS[0]}
echo
echo "check_sionna_gpu_runtime rc=$RC (0 = gpu_verified=true)"
exit "$RC"
