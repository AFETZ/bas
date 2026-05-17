#!/usr/bin/env python3
"""Этап 2.1 — synthetic demo всего Sionna RT pipeline без mission.

Цель: показать что pipeline `Sionna RT radio map -> lookup -> ns-3 dynamic
loss` физически работает и даёт **зависимость loss_ratio от позиции UAV**.

Что делает скрипт:
  1. Принимает заранее посчитанную radio_maps/iris_runway.npz (этап 2.1.c)
  2. Генерирует synthetic траекторию UAV: пролёт от GCS вдоль runway
     с пересечением радио-теней препятствий (towers, building)
  3. Для каждой waypoint позиции вызывает RadioMap.lookup() и
     RSS→loss_ratio логику из sionna_channel_publisher.py
  4. Печатает таблицу (x, y, RSS_dB, loss_ratio)
  5. Сохраняет CSV `logs/sionna_demo/trajectory.csv` для analyzer
  6. (Опционально) рисует график loss_ratio(time) рядом с
     UAV-trajectory в radio map → `logs/sionna_demo/trajectory_loss.png`

Это даёт defensible demo для гранта/диплома: показывает что
**в нашей реализации loss_ratio в ns-3 действительно следует
ray-traced радиокарте Sionna**, а не подключен случайно.

Запуск:
  ./sionna_env/bin/python scripts/demo_sionna_pipeline.py
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np  # type: ignore

# Используем те же функции lookup'а что и live publisher --
# гарантирует consistent поведение между demo и реальным mission run.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sionna_channel_publisher import RadioMap, rss_to_loss_ratio  # type: ignore


def synthetic_trajectory(n: int = 120) -> list[tuple[float, float, float, float]]:
    """Synthetic UAV-trajectory: пролёт от GCS (0, -30) на восток.

    UAV сначала летит вдоль runway (видит LoS к GCS), затем пересекает
    зону за tower_a (x=100, y=70) → tower_b (x=300, y=-70) → building
    (x=500, y=60). Если bypass'ить препятствия по y=0 (по середине runway),
    то значительной тени не будет; если идти на off-runway позиции, тень
    видна резко.

    Делаем S-образную траекторию между препятствиями: y meandering ±60м
    чтобы попадать в тени.

    Возвращает: список (t_s, x, y, z).
    """
    points = []
    for i in range(n):
        t = i * 2.0   # каждые 2 секунды
        x = -50.0 + (700.0 / (n - 1)) * i   # x: -50 .. 650 м
        # S-meander: ±60 м по y с периодом 200м (длина волны)
        y = 60.0 * np.sin(2 * np.pi * x / 250.0)
        z = 30.0   # фиксированная высота полёта
        points.append((t, float(x), float(y), z))
    return points


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio-map", default="radio_maps/iris_runway.npz")
    ap.add_argument("--out-dir", default="logs/sionna_demo")
    ap.add_argument("--save-plot", action="store_true")
    ap.add_argument("--points", type=int, default=120)
    args = ap.parse_args()

    rm = RadioMap(Path(args.radio_map))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    trajectory = synthetic_trajectory(args.points)

    csv_path = out_dir / "trajectory.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "x_m", "y_m", "z_m", "rss_db", "path_loss_db", "loss_ratio"])
        loss_series: list[float] = []
        rss_series: list[float] = []
        for t, x, y, z in trajectory:
            rss_db, pl_db = rm.lookup(x, y)
            loss = rss_to_loss_ratio(rss_db)
            w.writerow([f"{t:.1f}", f"{x:.1f}", f"{y:.1f}", f"{z:.1f}",
                        f"{rss_db:.2f}", f"{pl_db:.2f}", f"{loss:.4f}"])
            loss_series.append(loss)
            rss_series.append(rss_db)

    print(f"trajectory CSV: {csv_path} ({len(trajectory)} points)")
    print(f"RSS range: {min(rss_series):.1f} .. {max(rss_series):.1f} dB")
    print(f"loss_ratio range: {min(loss_series):.4f} .. {max(loss_series):.4f}")
    print(f"loss_ratio mean: {np.mean(loss_series):.4f}")
    n_high = sum(1 for r in loss_series if r > 0.5)
    print(f"high-loss points (loss>0.5): {n_high} / {len(loss_series)}")

    if args.save_plot:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        fig, axs = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        times = [pt[0] for pt in trajectory]
        ys = [pt[2] for pt in trajectory]

        ax0 = axs[0]
        ax0.plot(times, rss_series, color="navy", lw=2, label="RSS [dB]")
        ax0.set_ylabel("RSS [dB]")
        ax0.legend(loc="lower left")
        ax0.grid(alpha=0.3)
        ax0r = ax0.twinx()
        ax0r.plot(times, ys, color="darkorange", lw=1, linestyle="--", label="UAV y [m]")
        ax0r.set_ylabel("UAV y [m]")
        ax0r.legend(loc="upper right")
        ax0.set_title(
            "Sionna RT radio map: synthetic UAV trajectory through iris_runway scene"
        )

        ax1 = axs[1]
        ax1.plot(times, loss_series, color="crimson", lw=2, label="loss_ratio")
        ax1.set_ylabel("loss_ratio")
        ax1.set_xlabel("time [s]")
        ax1.set_ylim(0, 1)
        ax1.grid(alpha=0.3)
        ax1.legend(loc="upper right")
        ax1.axhline(0.5, color="gray", linestyle=":", lw=0.8)

        plt.tight_layout()
        plot_path = out_dir / "trajectory_loss.png"
        plt.savefig(plot_path, dpi=120)
        print(f"plot: {plot_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
