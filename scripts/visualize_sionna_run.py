#!/usr/bin/env python3
"""Визуализация Sionna end-to-end run: одна картинка где видны:

    1. Top-left:  RSS heatmap (radio map) + UAV trajectory overlay
    2. Top-right: loss_ratio(time) -- из ns3:sionna_poll events
    3. Bot-left:  UAV y(time) -- куда UAV отклоняется от LoS-оси
    4. Bot-right: channel_delay_ms(time) -- propagation delay изменяется
                  с positoin

Это defensible visual demo для гранта: показывает что в нашей реализации
ns-3 dynamically получает разные channel params по trajectory UAV через
iris_runway radio map.

Запуск:
  ./sionna_env/bin/python scripts/visualize_sionna_run.py \
      --run-dir logs/stage_2_1_synthetic_<id> \
      --radio-map radio_maps/iris_runway.npz \
      --out logs/stage_2_1_synthetic_<id>/sionna_overview.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np  # type: ignore
import matplotlib  # type: ignore
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sionna_channel_publisher import RadioMap, haversine_xy_m  # type: ignore


def load_flight_events(events_path: Path) -> list[dict]:
    out = []
    with events_path.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("event_type") == "flight" and "position" in ev:
                pos = ev["position"]
                if "lat" in pos and "lon" in pos:
                    out.append({
                        "sim_time": float(ev.get("sim_time", 0.0)),
                        "lat": float(pos["lat"]),
                        "lon": float(pos["lon"]),
                        "alt": float(pos.get("alt_rel_m", 0.0)),
                    })
    return out


def load_sionna_polls(ns3_path: Path) -> list[dict]:
    out = []
    with ns3_path.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("component") == "ns3:sionna_poll":
                out.append({
                    "sim_time": float(ev.get("sim_time", 0.0)),
                    "loss_ratio": float(ev.get("loss_ratio", 0.0)),
                    "extra_delay_ms": float(ev.get("extra_delay_ms", 0.0)),
                    "channel_delay_ms": float(ev.get("channel_delay_ms", 0.0)),
                })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--radio-map", default="radio_maps/iris_runway.npz")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    rm = RadioMap(Path(args.radio_map))
    flights = load_flight_events(run_dir / "events.jsonl")
    polls = load_sionna_polls(run_dir / "ns3_events.jsonl")
    print(f"flight events: {len(flights)}, sionna polls: {len(polls)}")

    # Convert lat/lon → x/y meters (как делает publisher).
    xs, ys, ts = [], [], []
    for ev in flights:
        x, y = haversine_xy_m(ev["lat"], ev["lon"])
        xs.append(x)
        ys.append(y)
        ts.append(ev["sim_time"])
    xs = np.array(xs)
    ys = np.array(ys)
    ts = np.array(ts)

    poll_t = np.array([p["sim_time"] for p in polls])
    poll_loss = np.array([p["loss_ratio"] for p in polls])
    poll_delay = np.array([p["channel_delay_ms"] for p in polls])

    fig, axs = plt.subplots(2, 2, figsize=(15, 9),
                            gridspec_kw={"hspace": 0.35, "wspace": 0.3})

    # ---- Top-left: radio map + trajectory ----
    ax = axs[0, 0]
    cx, cy, _ = rm.tx_pos
    sx = rm.x_max - rm.x_min
    sy = rm.y_max - rm.y_min
    extent = (rm.x_min, rm.x_max, rm.y_min, rm.y_max)
    im = ax.imshow(rm.rss_db, origin="lower", extent=extent,
                   cmap="viridis", vmin=-120, vmax=-30)
    ax.scatter([float(cx)], [float(cy)], marker="*", s=200, c="red",
               label="TX (GCS)", zorder=5)
    ax.plot(xs, ys, color="orange", lw=2, label="UAV trajectory", zorder=4)
    # точки старта/финиша
    if len(xs) > 0:
        ax.plot(xs[0], ys[0], "o", color="lime", markersize=10, zorder=6,
                label="start")
        ax.plot(xs[-1], ys[-1], "s", color="magenta", markersize=10, zorder=6,
                label="end")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title("Sionna RT radio map + UAV trajectory")
    ax.legend(loc="upper right")
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("RSS [dB]")

    # ---- Top-right: loss_ratio(time) ----
    ax = axs[0, 1]
    ax.plot(poll_t, poll_loss, color="crimson", lw=1.5, marker=".",
            markersize=3, label="loss_ratio (ns-3 RateErrorModel)")
    ax.set_xlabel("sim_time [s]")
    ax.set_ylabel("loss_ratio")
    ax.set_ylim(-0.02, max(0.2, float(poll_loss.max()) * 1.2) if len(poll_loss) else 1)
    ax.set_title(f"Dynamic loss_ratio (ns-3 channel_updated, n={len(polls)})")
    ax.grid(alpha=0.3)
    ax.legend()

    # ---- Bot-left: UAV y(time) ----
    ax = axs[1, 0]
    ax.plot(ts, ys, color="darkorange", lw=2, label="UAV y [m]")
    ax.set_xlabel("sim_time [s]")
    ax.set_ylabel("UAV y [m]")
    ax.axhline(0, color="gray", linestyle=":", lw=0.8)
    ax.set_title("UAV y(time): S-meander через iris_runway scene")
    ax.grid(alpha=0.3)
    ax.legend()

    # ---- Bot-right: channel_delay_ms(time) ----
    ax = axs[1, 1]
    ax.plot(poll_t, poll_delay, color="navy", lw=1.5, marker=".",
            markersize=3, label="channel_delay_ms (base + Sionna extra)")
    ax.set_xlabel("sim_time [s]")
    ax.set_ylabel("channel_delay_ms")
    ax.set_title(f"Dynamic channel delay (multi-path/scattering, n={len(polls)})")
    ax.grid(alpha=0.3)
    ax.legend()

    plt.suptitle(
        f"Sionna RT end-to-end pipeline: {run_dir.name}",
        fontsize=14, fontweight="bold",
    )

    out_path = Path(args.out) if args.out else (run_dir / "sionna_overview.png")
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"overview saved -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
