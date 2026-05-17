#!/usr/bin/env python3
"""Этап 2.1.c: compute radio map для iris_runway scene через Sionna RT.

Загружает Mitsuba scene XML (сгенерированный export_scene_to_sionna.py),
помещает TX в позицию GCS (фиксированная) и считает radio map на сетке RX
точек над/у runway. Сохраняет таблицу path_loss(x,y,z) в .npz файл.

В рамках 2.1.c для smoke считаем только 2D heatmap на фиксированной высоте
(z=10м, типичная высота полёта iris). В 2.1.d/e расширим до 3D grid.

Запуск:
  ./sionna_env/bin/python scripts/compute_radio_map.py \
      --scene scene/iris_runway.xml \
      --out radio_maps/iris_runway.npz \
      --carrier-ghz 2.4 \
      --tx-pos 0 -30 1.5

Дальше:
  - 2.1.d: ns3/scenarios/sionna_error_model.cc загрузит .npz и применит
    path_loss к каждому пакету
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np  # type: ignore

import mitsuba as mi  # type: ignore
if mi.variant() is None:
    mi.set_variant("llvm_ad_mono_polarized")

import sionna  # type: ignore
import sionna.rt as rt  # type: ignore


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="scene/iris_runway.xml")
    ap.add_argument("--out", default="radio_maps/iris_runway.npz")
    ap.add_argument(
        "--carrier-ghz", type=float, default=2.4,
        help="WiFi 2.4 GHz / 5 GHz / LoRa 0.868 GHz",
    )
    ap.add_argument(
        "--tx-pos", nargs=3, type=float, default=[0.0, -30.0, 1.5],
        help="TX (GCS) позиция в метрах: x y z (origin = центр runway, на земле)",
    )
    ap.add_argument(
        "--map-center", nargs=3, type=float, default=[200.0, 0.0, 10.0],
        help="Центр radio map (метр)",
    )
    ap.add_argument(
        "--map-size", nargs=2, type=float, default=[800.0, 300.0],
        help="Размер radio map по X, Y (метр)",
    )
    ap.add_argument(
        "--cell-size", nargs=2, type=float, default=[10.0, 10.0],
        help="Размер ячейки по X, Y (метр)",
    )
    ap.add_argument(
        "--samples-per-tx", type=int, default=300_000,
        help="Число ray-trace sample на TX. Smoke=10000, production >=1e6",
    )
    ap.add_argument(
        "--max-depth", type=int, default=3,
        help="Глубина reflections (типично 2-4)",
    )
    ap.add_argument(
        "--save-png", action="store_true",
        help="Сохранить heatmap PNG рядом с .npz",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    print(f"sionna {sionna.__version__}, mitsuba variant={mi.variant()}")

    scene_path = Path(args.scene)
    if not scene_path.exists():
        raise SystemExit(f"scene не найден: {scene_path}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"==> loading scene {scene_path}")
    scene = rt.load_scene(str(scene_path))
    scene.frequency = args.carrier_ghz * 1e9
    print(f"  scene objects: {len(scene.objects)}")
    # scene.wavelength может быть drjit.Float, convert через np.
    try:
        wavelength_m = float(np.asarray(scene.wavelength).item())
    except Exception:
        wavelength_m = 3e8 / (args.carrier_ghz * 1e9)
    print(f"  carrier: {args.carrier_ghz} GHz (λ = {wavelength_m:.4f} м)")

    print(f"==> placing TX at {args.tx_pos}")
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
    tx = rt.Transmitter(name="gcs_tx", position=list(args.tx_pos))
    scene.add(tx)

    print(f"==> computing radio map")
    print(f"    center: {args.map_center}, size: {args.map_size}, "
          f"cell: {args.cell_size}, samples/tx: {args.samples_per_tx:,}, "
          f"max_depth: {args.max_depth}")
    solver = rt.RadioMapSolver()
    rm = solver(
        scene=scene,
        max_depth=args.max_depth,
        samples_per_tx=args.samples_per_tx,
        cell_size=tuple(args.cell_size),
        center=list(args.map_center),
        orientation=[0.0, 0.0, 0.0],
        size=list(args.map_size),
    )

    # RSS — это received signal strength (linear power). Преобразуем в dB.
    rss = np.asarray(rm.rss if not hasattr(rm.rss, 'numpy') else rm.rss.numpy())
    if rss.ndim == 3:
        rss = rss[0]  # (num_tx=1, n_y, n_x) -> (n_y, n_x)
    rss_lin = np.maximum(rss, 1e-30)
    rss_db = 10.0 * np.log10(rss_lin)

    n_cells = rss.size
    n_los = int((rss > 1e-12).sum())
    print(f"  RSS shape: {rss.shape}, total cells: {n_cells}, "
          f"with coverage: {n_los} ({100*n_los/n_cells:.1f}%)")
    print(f"  RSS dB range: {rss_db[rss > 1e-12].min():.1f} .. "
          f"{rss_db.max():.1f} dB")

    # Path loss: |TX_power - RSS|, при TX_power=1W=0dBW = 30 dBm.
    # Sionna RSS уже учитывает 1W передатчик через RadioMap normalization.
    path_loss_db = -rss_db   # RSS [dB] -> path_loss [dB] (минус значит loss)

    # Сохраняем .npz.
    np.savez(
        out_path,
        rss_db=rss_db.astype(np.float32),
        path_loss_db=path_loss_db.astype(np.float32),
        cell_size=np.array(args.cell_size),
        map_center=np.array(args.map_center),
        map_size=np.array(args.map_size),
        tx_position=np.array(args.tx_pos),
        carrier_hz=np.float64(args.carrier_ghz * 1e9),
        max_depth=np.int32(args.max_depth),
        samples_per_tx=np.int32(args.samples_per_tx),
    )
    print(f"  radio map saved -> {out_path}")

    # Опциональный PNG.
    if args.save_png:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        fig, ax = plt.subplots(figsize=(10, 5))
        cx, cy, cz = args.map_center
        sx, sy = args.map_size
        extent = (cx - sx/2, cx + sx/2, cy - sy/2, cy + sy/2)
        im = ax.imshow(
            rss_db, origin="lower", extent=extent,
            cmap="viridis", vmin=-120, vmax=-30,
        )
        ax.scatter([args.tx_pos[0]], [args.tx_pos[1]], marker="*",
                   s=200, c="red", label="TX (GCS)")
        ax.set_xlabel("x [m] (вдоль runway)")
        ax.set_ylabel("y [m]")
        ax.set_title(
            f"Sionna RT radio map: {args.carrier_ghz} GHz, "
            f"max_depth={args.max_depth}, samples={args.samples_per_tx:,}"
        )
        ax.legend()
        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label("RSS [dB]")
        png_path = out_path.with_suffix(".png")
        plt.tight_layout()
        plt.savefig(png_path, dpi=120)
        print(f"  heatmap saved -> {png_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
