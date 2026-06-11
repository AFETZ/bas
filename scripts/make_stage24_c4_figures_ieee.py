#!/usr/bin/env python3
"""Publication (IEEE) figures for Stage 2.4 C4 route-level Sionna->ns-3 audit.

Строит 5 основных фигур ИЗ PROCESSED single-run CSV (provenance: один run_id),
без смешивания старых прогонов. Сохраняет png/pdf/svg.

  A stage24_route_rssi_loss_bins_ieee   — loss vs RSSI, 2 панели (control|payload),
                                          точки route-level loss + CI, пунктир =
                                          configured RSSI-to-loss mapping.
  B stage24_route_rssi_map_ieee         — RSSI карта маршрута, crop по траектории.
  C stage24_route_rssi_packet_trace_ieee— RSSI(t) + windowed loss rate (не точки).
  D stage24_payload_goodput_ieee        — payload goodput offered-to-ns3 vs
                                          delivered-after-ns3 (windowed).
  E stage24_payload_interruptions_ieee  — frame inter-arrival gap + interruptions.

Все подписи: "route-level simulated loss", НЕ "measured PER". no-coverage floor
не смешивается с normal bins (в финальном run floor rows = 0).

Usage: .venv/bin/python scripts/make_stage24_c4_figures_ieee.py
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np  # type: ignore
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data/processed"
FIG = REPO / "figures"
FIG.mkdir(exist_ok=True)
csv.field_size_limit(10_000_000)

plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.titlesize": 10,
    "axes.labelsize": 9, "legend.fontsize": 8, "xtick.labelsize": 8,
    "ytick.labelsize": 8, "axes.grid": True, "grid.alpha": 0.3,
    "figure.dpi": 120, "savefig.bbox": "tight",
})
C_CTRL, C_PAY = "#1f4e79", "#b5651d"   # control=blue, payload=orange-brown


def save(fig, name):
    for ext in ("png", "pdf", "svg"):
        fig.savefig(FIG / f"{name}.{ext}", dpi=300)
    plt.close(fig)
    print(f"[fig] wrote figures/{name}.png/.pdf/.svg")


# ---------------------------------------------------------------------------
def load_bins():
    rows = list(csv.DictReader((PROC / "stage24_route_rssi_bins.csv").open()))
    out = defaultdict(list)
    for r in rows:
        out[r["flow_id"]].append(r)
    for fl in out:
        out[fl].sort(key=lambda r: float(r["rssi_bin_center"]))
    return out


def fig_a_loss_bins():
    bins = load_bins()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0), sharey=True)
    for ax, flow, color in ((axes[0], "control", C_CTRL), (axes[1], "payload", C_PAY)):
        rows = bins.get(flow, [])
        if not rows:
            continue
        x = np.array([float(r["rssi_bin_center"]) for r in rows])
        emp = np.array([float(r["empirical_loss_ratio"]) for r in rows])
        lo = np.array([float(r["ci95_low"]) for r in rows])
        hi = np.array([float(r["ci95_high"]) for r in rows])
        exp = np.array([float(r["expected_loss_ratio_mean"]) for r in rows])
        yerr = np.vstack([np.clip(emp - lo, 0, None), np.clip(hi - emp, 0, None)])
        # configured mapping (dashed) — отсортировано по RSSI
        order = np.argsort(x)
        ax.plot(x[order], exp[order], "--", color="0.35", lw=1.3,
                label="configured RSSI-to-loss mapping", zorder=2)
        ax.errorbar(x, emp, yerr=yerr, fmt="o", ms=4.5, color=color,
                    ecolor=color, elinewidth=1.1, capsize=2.5,
                    label="route-level simulated loss (95% CI)", zorder=3)
        ax.set_title(f"{flow} channel")
        ax.set_xlabel("RSSI used for drop decision (dBm)")
        ax.set_ylim(-0.02, 1.0)
        # естественный порядок dBm: слабый сигнал слева (высокий loss), сильный справа
    axes[0].set_ylabel("packet loss ratio")
    axes[0].legend(loc="upper right", framealpha=0.9)
    fig.suptitle("Stage 2.4 route-level simulated loss vs RSSI", y=1.02)
    save(fig, "stage24_route_rssi_loss_bins_ieee")


def stream_route():
    """Yield (sim_time, flow, rssi_used, decision, x_north, y_east)."""
    with (PROC / "stage24_route_rssi_packets.csv").open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                yield (float(row["timestamp"]), row["flow_id"],
                       float(row["RSSI_used_for_drop_decision"]),
                       row["final_decision"],
                       float(row["uav_x_north"]), float(row["uav_y_east"]))
            except (ValueError, KeyError):
                continue


def fig_b_map():
    xs, ys, rssi = [], [], []
    for i, (t, fl, rs, dec, xn, ye) in enumerate(stream_route()):
        if fl != "payload":          # одна геометрия достаточно (RSSI общий)
            continue
        if i % 3:                    # downsample
            continue
        xs.append(ye); ys.append(xn); rssi.append(rs)
    xs, ys, rssi = np.array(xs), np.array(ys), np.array(rssi)
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    sc = ax.scatter(xs, ys, c=rssi, cmap="viridis", s=11, alpha=0.85,
                    edgecolors="none")
    cb = fig.colorbar(sc, ax=ax, pad=0.02)
    cb.set_label("live Sionna RT RSSI (dBm)")
    # crop по траектории; aspect=auto чтобы маршрут заполнял полотно
    # (узкий коридор: восток ~±240 м, север ~±35 м — equal aspect дал бы пустоту)
    mx, Mx = xs.min(), xs.max()
    my, My = ys.min(), ys.max()
    px = 0.04 * (Mx - mx); py = max(4.0, 0.18 * (My - my))
    ax.set_xlim(mx - px, Mx + px); ax.set_ylim(my - py, My + py)
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    ax.set_title("Stage 2.4 route RSSI map (cropped to trajectory; axes not to scale)")
    ax.text(0.015, 0.93, "← toward GCS/Tx (west, y_east = -600 m)",
            transform=ax.transAxes, fontsize=7, color="0.2", va="top")
    save(fig, "stage24_route_rssi_map_ieee")


def fig_c_trace():
    t_rssi, rssi = [], []
    # windowed loss
    win = 5.0
    min_packets_per_window = 20
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # flow -> wbin -> [att, drop]
    tmin, tmax = 1e18, -1e18
    for t, fl, rs, dec, xn, ye in stream_route():
        tmin = min(tmin, t); tmax = max(tmax, t)
        if fl == "payload":
            t_rssi.append(t); rssi.append(rs)
        wb = int(t // win)
        a = agg[fl][wb]
        a[0] += 1
        if dec == "dropped":
            a[1] += 1
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.0, 4.2), sharex=True)
    # RSSI over time (downsample)
    t_rssi = np.array(t_rssi); rssi = np.array(rssi)
    step = max(1, len(t_rssi) // 4000)
    ax1.plot(t_rssi[::step], rssi[::step], color="#2c7fb8", lw=0.8)
    ax1.set_ylabel("RSSI (dBm)")
    ax1.set_title("C4 online Sionna RT RSSI and windowed packet loss")
    # windowed loss per flow
    for fl, color in (("control", C_CTRL), ("payload", C_PAY)):
        # Exclude partial edge windows, whose one-packet ratios otherwise appear
        # as artificial 0 or 1 spikes at stream startup/shutdown.
        wbs = sorted(
            wb for wb, (attempts, _) in agg[fl].items()
            if attempts >= min_packets_per_window
        )
        tx = [wb * win + win / 2 for wb in wbs]
        ly = [agg[fl][wb][1] / agg[fl][wb][0] if agg[fl][wb][0] else np.nan for wb in wbs]
        ax2.plot(tx, ly, "-", color=color, lw=1.4, label=f"{fl} loss ({int(win)} s window)")
    ax2.set_ylabel("windowed loss ratio"); ax2.set_xlabel("ns-3 simulation time (s)")
    ax2.set_ylim(-0.02, 1.0); ax2.legend(loc="upper right", framealpha=0.9)
    save(fig, "stage24_route_rssi_packet_trace_ieee")


def fig_d_goodput():
    win = 5.0
    offered = defaultdict(float)   # wbin -> bytes entering ns-3 (near_to_far)
    delivered = defaultdict(float)  # wbin -> bytes received after ns-3
    with (PROC / "stage24_payload_packets.csv").open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("direction") != "near_to_far":
                continue
            try:
                t = float(row["timestamp"]); sz = float(row["packet_size"])
            except (ValueError, KeyError):
                continue
            wb = int(t // win)
            offered[wb] += sz
            if row.get("final_decision") == "received":
                delivered[wb] += sz
    wbs = sorted(set(offered) | set(delivered))
    tx = np.array([wb * win + win / 2 for wb in wbs])
    off_mbps = np.array([offered[wb] * 8 / win / 1e6 for wb in wbs])
    del_mbps = np.array([delivered[wb] * 8 / win / 1e6 for wb in wbs])
    fig, ax = plt.subplots(figsize=(7.0, 3.0))
    ax.plot(tx, off_mbps, "-", color="0.5", lw=1.2, label="offered to ns-3 payload channel")
    ax.plot(tx, del_mbps, "-", color=C_PAY, lw=1.6, label="delivered after ns-3")
    ax.fill_between(tx, del_mbps, off_mbps, color=C_PAY, alpha=0.12)
    ax.set_xlabel("ns-3 simulation time (s)"); ax.set_ylabel("payload goodput (Mbit/s)")
    ax.set_title("Stage 2.4 ns-3-audited payload goodput (5 s windows)")
    ax.legend(loc="upper right", framealpha=0.9)
    save(fig, "stage24_payload_goodput_ieee")


def fig_e_interruptions():
    ts, gaps, ev = [], [], []
    with (PROC / "stage24_payload_frames.csv").open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                ts.append(float(row["time_s"]))
                g = row.get("inter_frame_gap_s", "")
                gaps.append(float(g) if g not in ("", "nan") else np.nan)
                ev.append(str(row.get("gap_event", "")).strip().lower() == "true")
            except (ValueError, KeyError):
                continue
    ts = np.array(ts); gaps = np.array(gaps); ev = np.array(ev)
    order = np.argsort(ts); ts, gaps, ev = ts[order], gaps[order], ev[order]
    g = np.clip(gaps, 1e-2, None)   # для log-оси
    fig, ax = plt.subplots(figsize=(7.0, 3.0))
    ax.plot(ts, g, "-", color="#999", lw=0.5, alpha=0.7, label="inter-frame gap")
    ax.scatter(ts[ev], g[ev], s=12, color="#c0392b", alpha=0.55,
               label="frame gap > 0.5 s (interruption)", zorder=3)
    ax.axhline(1.0, color="0.5", ls="--", lw=0.9)
    ax.text(ts.min(), 1.15, "1 s interruption threshold", fontsize=6.5, color="0.4")
    ax.set_yscale("log")
    ax.set_xlabel("receiver time (s)")
    ax.set_ylabel("inter-frame gap (s, log scale)")
    ax.set_title("Stage 2.4 post-ns-3 decoded-frame interruptions")
    ax.legend(loc="upper left", framealpha=0.9)
    save(fig, "stage24_payload_interruptions_ieee")


def main() -> int:
    fig_a_loss_bins()
    fig_b_map()
    fig_c_trace()
    fig_d_goodput()
    fig_e_interruptions()
    print("[fig] all C4 IEEE figures generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
