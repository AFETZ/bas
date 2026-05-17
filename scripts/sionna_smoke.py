#!/usr/bin/env python3
"""Этап 2.1.a smoke: проверяем что Sionna RT работает на built-in сцене.

Загружает одну из reference сцен Sionna (`munich`, `etoile`), ставит TX и RX,
запускает ray-tracing с max_depth=2, печатает path_loss и delay.

Запуск:
  ./sionna_env/bin/python scripts/sionna_smoke.py
"""
from __future__ import annotations

import os
import sys

# Уменьшаем TF spam (warnings про CUDA, etc).
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np  # type: ignore

# Mitsuba variant ВАЖНО устанавливать ДО импорта sionna.rt.
# Sionna 1.x по умолчанию пробует `cuda_ad_mono_polarized` который требует
# OptiX SDK; в WSL без OptiX это фейлится на первом trace_paths. LLVM-variant
# работает на CPU без NVIDIA-зависимостей.
# См. docs/stage_2_1_sionna_plan.md (open question #1).
import mitsuba as mi  # type: ignore
if mi.variant() is None:
    mi.set_variant("llvm_ad_mono_polarized")

import sionna  # type: ignore
import sionna.rt as rt  # type: ignore


def main() -> int:
    print(f"sionna {sionna.__version__}")
    print(f"sionna.rt module: {rt}")

    # Список встроенных сцен.
    print(f"available scenes: {[s for s in dir(rt.scene) if not s.startswith('_')]}")

    # Sionna 1.x: load_scene принимает path к Mitsuba XML или одну из
    # встроенных сцен (`rt.scene.munich`, `rt.scene.etoile`).
    print("\n==> loading sionna.rt.scene.simple_street_canyon")
    try:
        scene = rt.load_scene(rt.scene.simple_street_canyon)
    except Exception as e:
        print(f"  simple_street_canyon failed: {e}")
        print("  trying box")
        scene = rt.load_scene(rt.scene.box)

    print(f"  scene loaded: {scene}")
    print(f"  scene objects: {len(scene.objects)}")

    # Помещаем TX в (0, 0, 10) и RX в (20, 0, 1.5).
    print("\n==> placing TX and RX")
    scene.tx_array = rt.PlanarArray(
        num_rows=1, num_cols=1,
        vertical_spacing=0.5, horizontal_spacing=0.5,
        pattern="iso", polarization="V",
    )
    scene.rx_array = rt.PlanarArray(
        num_rows=1, num_cols=1,
        vertical_spacing=0.5, horizontal_spacing=0.5,
        pattern="iso", polarization="V",
    )
    tx = rt.Transmitter(name="tx", position=[0.0, 0.0, 10.0])
    rx = rt.Receiver(name="rx", position=[20.0, 0.0, 1.5])
    scene.add(tx)
    scene.add(rx)
    print(f"  TX position: {tx.position}")
    print(f"  RX position: {rx.position}")

    # Запускаем ray-tracing. В Sionna 1.x используется PathSolver класс,
    # а не метод scene.trace_paths.
    print("\n==> tracing paths (max_depth=2)")
    solver = rt.PathSolver()
    paths = solver(scene=scene, max_depth=2)
    print(f"  paths: {paths}")
    a, tau = paths.cir(out_type="numpy")
    print(f"  CIR complex amplitudes shape: {a.shape}")
    print(f"  CIR delays shape: {tau.shape}")

    # CIR (channel impulse response) format в Sionna 1.x:
    # a: (num_rx, num_rx_ant, num_tx, num_tx_ant, num_paths, num_time_samples)
    # tau: (num_rx, num_rx_ant, num_tx, num_tx_ant, num_paths)
    # Просуммируем по путям -> total received power
    power_total = (np.abs(a) ** 2).sum()
    print(f"  total received power (rough): {power_total:.6e}")

    # Smoke radio map (1 TX, grid RX).
    print("\n==> computing radio map on small grid")
    rm_solver = rt.RadioMapSolver()
    rm = rm_solver(
        scene=scene,
        max_depth=2,
        samples_per_tx=10000,   # очень мало для smoke; в реальности 1e6+
        cell_size=(5.0, 5.0),   # 5x5 м per cell
        center=[10.0, 0.0, 1.5],
        orientation=[0.0, 0.0, 0.0],
        size=[40.0, 20.0],
    )
    print(f"  radio map: {rm}")
    print(f"  RSS shape: {rm.rss.shape}")
    rss_np = rm.rss.numpy() if hasattr(rm.rss, 'numpy') else np.asarray(rm.rss)
    print(f"  RSS dB range: {10*np.log10(rss_np[rss_np > 0].min() + 1e-30):.1f} .. "
          f"{10*np.log10(rss_np.max() + 1e-30):.1f}")

    print("\n==> smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
