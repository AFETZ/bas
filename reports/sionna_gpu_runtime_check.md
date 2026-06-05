# Sionna RT GPU Runtime Check

- **gpu_verified:** True
- method: `total_memory_delta_wsl2` (WSL2: per-process nvidia-smi GPU memory is unavailable)
- mitsuba_cuda_optix_backend_verified: True

## Environment
- nvidia-smi: `NVIDIA GeForce RTX 5070 Ti, 595.79, 2162 MiB, 16303 MiB`
- python: `/home/afetz/bas-prototype/sionna_env/bin/python`
- CUDA_VISIBLE_DEVICES: `None`
- LD_LIBRARY_PATH head: `/usr/lib/wsl/lib`
- LD_PRELOAD: `/usr/lib/x86_64-linux-gnu/libnvoptix.so.595.71.05`
- DRJIT_LIBOPTIX_PATH: `/usr/lib/x86_64-linux-gnu/libnvoptix.so.595.71.05`
- requested Mitsuba variant: `cuda_ad_mono_polarized`
- actual Mitsuba variant: `cuda_ad_mono_polarized`
- Dr.Jit version: `1.3.1`

## TensorFlow (diagnostic only — NOT used by Sionna RT ray tracing)
- TF version: `2.19.1`
- TF GPUs: `[]`
- tf_gpu_used: False (irrelevant to Mitsuba ray tracing)

## Active GPU evidence (total memory delta)
- GPU mem baseline: 2465 MiB
- Dr.Jit CUDA alloc delta: 0 MiB
- Mitsuba cuda render delta: 239 MiB (render ok: True)
- Sionna PathSolver ran: True (last rss=-54.04 dBm, 2.2 s)
- nvidia-smi after: `7 %, 2758 MiB`

## Interpretation
Sionna RT path solving uses the Mitsuba/Dr.Jit backend. A measurable increase
in **total** GPU memory during a CUDA render/allocation, together with the actual
`cuda_ad_mono_polarized` variant and a completed render/path solve, confirms the
OptiX/CUDA backend executes on the GPU. Per-process GPU memory is not exposed
under WSL2, and TensorFlow's CUDA runtime is not loaded; neither affects the
Mitsuba-based ray tracing path used by Sionna RT.

Output: `reports/sionna_gpu_runtime_check.json`.
