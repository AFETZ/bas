#!/usr/bin/env python3
"""Sionna RT GPU runtime evidence (WSL2-correct) — пишет md + json отчёты.

Доказывает РЕАЛЬНЫЙ GPU backend Sionna RT (Mitsuba/Dr.Jit CUDA/OptiX), а не
флаги. Метод корректен для WSL2:
  * TensorFlow GPU — только диагностика (Sionna RT path solving идёт через
    Mitsuba/Dr.Jit, не через TF); TF на CPU не делает вывод о Sionna RT;
  * per-process nvidia-smi на WSL2 НЕ отдаёт GPU-память процесса — НЕ используем;
  * основной метод: дельта ОБЩЕЙ GPU-памяти (`nvidia-smi memory.used`) вокруг
    реальной CUDA-аллокации / Mitsuba cuda render / Sionna PathSolve.

gpu_verified = (actual variant содержит 'cuda') И (cuda-render выполнен) И
               (total GPU memory delta >= порога на cuda render ИЛИ drjit alloc).

Запускать ПОД OptiX-окружением (см. scripts/check_sionna_gpu_runtime.sh):
  LD_LIBRARY_PATH=/usr/lib/wsl/lib  LD_PRELOAD=<libnvoptix>  MITSUBA_VARIANT=cuda_*

Выход:
  reports/sionna_gpu_runtime_check.md
  reports/sionna_gpu_runtime_check.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORTS = REPO / "reports"
SCENE = os.environ.get("BAS_RT_SCENE", "scene/iris_runway.xml")
GPU_MEM_THRESHOLD_MIB = 50


def smi(query: str) -> str:
    try:
        return subprocess.check_output(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader"],
            text=True, timeout=8).strip()
    except Exception as e:
        return f"<nvidia-smi error: {e}>"


def gpu_used_mib() -> int:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=8)
        return int(out.strip().splitlines()[0])
    except Exception:
        return -1


def main() -> int:
    ev: dict = {"method": "total_memory_delta_wsl2",
                "gpu_mem_threshold_mib": GPU_MEM_THRESHOLD_MIB}
    ev["nvidia_smi_name"] = smi("name,driver_version,memory.used,memory.total")
    ev["python_executable"] = sys.executable
    ev["cuda_visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES")
    ev["ld_library_path_head"] = (os.environ.get("LD_LIBRARY_PATH", "").split(":") or [""])[0]
    ev["ld_preload"] = os.environ.get("LD_PRELOAD")
    ev["drjit_liboptix_path"] = os.environ.get("DRJIT_LIBOPTIX_PATH")
    ev["requested_mitsuba_variant"] = os.environ.get("MITSUBA_VARIANT", "")

    # TensorFlow (diagnostic only)
    try:
        import tensorflow as tf  # type: ignore
        ev["tensorflow_version"] = tf.__version__
        ev["tensorflow_gpus"] = [d.name for d in tf.config.list_physical_devices("GPU")]
    except Exception as e:
        ev["tensorflow_version"] = None
        ev["tensorflow_gpus"] = []
        ev["tensorflow_error"] = repr(e)
    ev["tf_gpu_used"] = bool(ev.get("tensorflow_gpus"))

    # Mitsuba / Dr.Jit
    import mitsuba as mi
    if mi.variant() is None:
        mi.set_variant(ev["requested_mitsuba_variant"] or "cuda_ad_mono_polarized")
    ev["actual_mitsuba_variant"] = str(mi.variant())
    cuda_variant = "cuda" in ev["actual_mitsuba_variant"]
    try:
        import drjit as dr  # type: ignore
        ev["drjit_version"] = getattr(dr, "__version__", "n/a")
    except Exception as e:
        ev["drjit_version"] = None
        ev["drjit_error"] = repr(e)

    # (1) Dr.Jit CUDA allocation — total memory delta
    base = gpu_used_mib()
    ev["gpu_mem_baseline_mib"] = base
    drjit_delta = 0
    try:
        import drjit as dr  # type: ignore
        from drjit.cuda import Float as CFloat  # форсит CUDA backend
        arr = dr.zeros(CFloat, 200_000_000)
        arr += 1.0
        dr.eval(arr); dr.sync_thread()
        drjit_delta = gpu_used_mib() - base
        del arr; dr.flush_malloc_cache()
    except Exception as e:
        ev["drjit_cuda_error"] = repr(e)
    ev["drjit_cuda_mem_delta_mib"] = int(drjit_delta)

    # (2) Mitsuba cuda render — total memory delta + completion
    b2 = gpu_used_mib()
    mi_delta = 0
    mi_render_ok = False
    try:
        import drjit as dr  # type: ignore
        sc = mi.load_dict({"type": "scene", "integrator": {"type": "path"},
            "sensor": {"type": "perspective",
                       "film": {"type": "hdrfilm", "width": 512, "height": 512}},
            "emitter": {"type": "constant"}, "shape": {"type": "sphere"}})
        _ = mi.render(sc, spp=256)
        dr.sync_thread()
        mi_delta = gpu_used_mib() - b2
        mi_render_ok = True
    except Exception as e:
        ev["mitsuba_render_error"] = repr(e)
    ev["mitsuba_render_mem_delta_mib"] = int(mi_delta)
    ev["mitsuba_cuda_render_ok"] = mi_render_ok

    # (3) Real Sionna RT PathSolver (same LiveRTChannel as publisher)
    rt_ok = False
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        from sionna_channel_publisher import LiveRTChannel
        t0 = time.time()
        ch = LiveRTChannel(scene_path=Path(SCENE),
                           mitsuba_variant=ev["actual_mitsuba_variant"],
                           require_gpu=False)
        for i in range(20):
            rss, _pl = ch.lookup(float(-50 + i * 3), float(-30 + i), 10.0)
        ev["pathsolver_last_rss_dbm"] = round(float(rss), 2)
        ev["pathsolver_seconds"] = round(time.time() - t0, 1)
        ev["pathsolver_gpu_verified_self"] = getattr(ch, "gpu_verified", None)
        rt_ok = True
    except Exception as e:
        ev["sionna_pathsolver_error"] = repr(e)
    ev["sionna_pathsolver_ran"] = rt_ok

    ev["nvidia_smi_after"] = smi("utilization.gpu,memory.used")
    ev["wsl2_per_process_gpu_accounting"] = "unavailable"

    gpu_mem_evidence = (mi_delta >= GPU_MEM_THRESHOLD_MIB) or (drjit_delta >= GPU_MEM_THRESHOLD_MIB)
    gpu_verified = bool(cuda_variant and mi_render_ok and rt_ok and gpu_mem_evidence)
    ev["mitsuba_cuda_optix_backend_verified"] = gpu_verified
    ev["gpu_verified"] = gpu_verified

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "sionna_gpu_runtime_check.json").write_text(
        json.dumps(ev, indent=2, ensure_ascii=False), encoding="utf-8")

    md = [
        "# Sionna RT GPU Runtime Check",
        "",
        f"- **gpu_verified:** {gpu_verified}",
        f"- method: `{ev['method']}` (WSL2: per-process nvidia-smi GPU memory is unavailable)",
        f"- mitsuba_cuda_optix_backend_verified: {gpu_verified}",
        "",
        "## Environment",
        f"- nvidia-smi: `{ev['nvidia_smi_name']}`",
        f"- python: `{ev['python_executable']}`",
        f"- CUDA_VISIBLE_DEVICES: `{ev['cuda_visible_devices']}`",
        f"- LD_LIBRARY_PATH head: `{ev['ld_library_path_head']}`",
        f"- LD_PRELOAD: `{ev['ld_preload']}`",
        f"- DRJIT_LIBOPTIX_PATH: `{ev['drjit_liboptix_path']}`",
        f"- requested Mitsuba variant: `{ev['requested_mitsuba_variant']}`",
        f"- actual Mitsuba variant: `{ev['actual_mitsuba_variant']}`",
        f"- Dr.Jit version: `{ev['drjit_version']}`",
        "",
        "## TensorFlow (diagnostic only — NOT used by Sionna RT ray tracing)",
        f"- TF version: `{ev.get('tensorflow_version')}`",
        f"- TF GPUs: `{ev.get('tensorflow_gpus')}`",
        f"- tf_gpu_used: {ev['tf_gpu_used']} (irrelevant to Mitsuba ray tracing)",
        "",
        "## Active GPU evidence (total memory delta)",
        f"- GPU mem baseline: {ev['gpu_mem_baseline_mib']} MiB",
        f"- Dr.Jit CUDA alloc delta: {ev['drjit_cuda_mem_delta_mib']} MiB",
        f"- Mitsuba cuda render delta: {ev['mitsuba_render_mem_delta_mib']} MiB "
        f"(render ok: {ev['mitsuba_cuda_render_ok']})",
        f"- Sionna PathSolver ran: {ev['sionna_pathsolver_ran']} "
        f"(last rss={ev.get('pathsolver_last_rss_dbm')} dBm, "
        f"{ev.get('pathsolver_seconds')} s)",
        f"- nvidia-smi after: `{ev['nvidia_smi_after']}`",
        "",
        "## Interpretation",
        "Sionna RT path solving uses the Mitsuba/Dr.Jit backend. A measurable increase",
        "in **total** GPU memory during a CUDA render/allocation, together with the actual",
        "`cuda_ad_mono_polarized` variant and a completed render/path solve, confirms the",
        "OptiX/CUDA backend executes on the GPU. Per-process GPU memory is not exposed",
        "under WSL2, and TensorFlow's CUDA runtime is not loaded; neither affects the",
        "Mitsuba-based ray tracing path used by Sionna RT.",
        "",
        "Output: `reports/sionna_gpu_runtime_check.json`.",
    ]
    (REPORTS / "sionna_gpu_runtime_check.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"[gpu-check] gpu_verified={gpu_verified} "
          f"mitsuba_render_delta={mi_delta} MiB drjit_delta={drjit_delta} MiB")
    print(f"[gpu-check] wrote {REPORTS / 'sionna_gpu_runtime_check.md'}")
    print(f"[gpu-check] wrote {REPORTS / 'sionna_gpu_runtime_check.json'}")
    return 0 if gpu_verified else 3


if __name__ == "__main__":
    sys.exit(main())
