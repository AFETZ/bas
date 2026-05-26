#!/usr/bin/env python3
"""Try cuda_ad_mono_polarized variant (correct per upstream).
Sionna's BSDF requires polarized spectrum (Jones matrix), not mono Color1f.
"""
import os
os.environ.pop("DRJIT_LIBOPTIX_PATH", None)   # use default /usr/lib path

import mitsuba as mi
mi.set_variant("cuda_ad_mono_polarized")
print(f"variant: {mi.variant()}  version: {mi.MI_VERSION}")

import sionna
from sionna import rt
print(f"sionna: {sionna.__version__}")

# Default Sionna does variant management automatically — let it init.
scene = rt.load_scene(rt.scene.floor_wall)
print(f"loaded floor_wall: {len(scene.objects)} objects")

scene.tx_array = rt.PlanarArray(num_rows=1, num_cols=1,
                                 vertical_spacing=0.5, horizontal_spacing=0.5,
                                 pattern="iso", polarization="V")
scene.rx_array = rt.PlanarArray(num_rows=1, num_cols=1,
                                 vertical_spacing=0.5, horizontal_spacing=0.5,
                                 pattern="iso", polarization="V")
scene.frequency = 2.4e9
scene.add(rt.Transmitter(name="tx", position=mi.Point3f(0, 0, 2), power_dbm=20))

solver = rt.RadioMapSolver()
import traceback
try:
    rm = solver(
        scene=scene,
        center=mi.Point3f(0, 0, 1.5),
        orientation=mi.Point3f(0, 0, 0),
        size=mi.Point2f(10, 10),
        cell_size=mi.Point2f(1, 1),
        samples_per_tx=1_000, max_depth=1,
    )
    import numpy as np
    pg = rm.path_gain.numpy() if hasattr(rm.path_gain, "numpy") else np.asarray(rm.path_gain)
    print(f"radio map shape: {pg.shape}")
    print(f"path_gain min/max: {pg.min():.3e} / {pg.max():.3e}")
    print("OK — Sionna RT live mode CUDA OptiX works!")
except Exception as e:
    print(f"[solve failed] {type(e).__name__}: {e}")
    traceback.print_exc()
    import sys
    sys.exit(1)
