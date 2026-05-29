#!/usr/bin/env python3
"""Render a Sionna RT radio-map .npz as a coverage heatmap PNG."""
import glob
import sys
import numpy as np

src = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("radio_maps/*.npz"))[0]
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/sionna_heatmap.png"
d = np.load(src, allow_pickle=True)
print("file:", src)
for k in d.files:
    v = d[k]
    print("  key", k, getattr(v, "shape", None), getattr(v, "dtype", type(v)))

# Find the main 2D float grid (the RSS / path-gain map).
grid = None
gname = None
for k in d.files:
    v = d[k]
    if hasattr(v, "ndim") and v.ndim == 2 and v.size > 100:
        if grid is None or v.size > grid.size:
            grid = np.asarray(v, dtype=float)
            gname = k
if grid is None:
    print("no 2D grid found")
    sys.exit(1)
print("using grid:", gname, grid.shape)

# Replace non-finite with min for display.
finite = np.isfinite(grid)
if finite.any():
    lo = np.nanmin(grid[finite])
    grid = np.where(finite, grid, lo)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
im = ax.imshow(grid, origin="lower", cmap="turbo", aspect="auto")
cb = fig.colorbar(im, ax=ax, shrink=0.85)
cb.set_label("RSSI / path gain (dB)")
ax.set_title("Sionna RT — карта радиопокрытия (ray-traced)")
ax.set_xlabel("east cell")
ax.set_ylabel("north cell")
fig.tight_layout()
fig.savefig(out)
print("SAVED", out)
