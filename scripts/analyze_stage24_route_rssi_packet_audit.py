#!/usr/bin/env python3
"""Analyze Stage 2.4 route-level live Sionna RT packet audit logs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib  # type: ignore

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # type: ignore
import numpy as np  # type: ignore

PAYLOAD_ROUTE_MIN_PACKETS = 1000


def as_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def finite(value: float) -> bool:
    return math.isfinite(value)


def repository_loss_reference(rssi_db: float | np.ndarray) -> float | np.ndarray:
    return 1.0 / (1.0 + np.exp(0.5 * (np.asarray(rssi_db) + 78.0)))


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (math.nan, math.nan)
    phat = k / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) / n) + (z * z / (4.0 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def git_value(args: list[str], default: str = "unknown") -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return default
    value = completed.stdout.strip()
    return value if value else default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def recorder_warnings(run_dir: Path) -> list[str]:
    path = run_dir / "auto_demo_recorder.log"
    if not path.exists():
        return []
    warnings: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if "WARN:" in line or "not reached" in line:
                warnings.append(line)
    return warnings


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def normalize_packets(raw_rows: list[dict[str, str]], source: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(raw_rows):
        decision = str(row.get("final_decision") or "")
        rssi_from = as_float(row.get("RSSI_from_Sionna"))
        rssi_used = as_float(row.get("RSSI_used_for_drop_decision"))
        channel_model = str(row.get("sionna_channel_model") or "")
        sample_valid = str(row.get("sionna_sample_valid") or "").lower() == "true"
        is_live_route = (
            sample_valid
            and channel_model == "rt_online"
            and finite(rssi_from)
            and finite(rssi_used)
        )
        out.append({
            "source_log": str(source),
            "row_index": i,
            "run_id": row.get("run_id", ""),
            "packet_uid": row.get("packet_uid", ""),
            "timestamp": as_float(row.get("timestamp")),
            "flow_id": row.get("flow_id", ""),
            "channel_type": row.get("channel_type", ""),
            "tx_node": row.get("tx_node", ""),
            "rx_node": row.get("rx_node", ""),
            "direction": row.get("direction", ""),
            "packet_size": as_int(row.get("packet_size")),
            "rtp_sequence_number": row.get("rtp_sequence_number", ""),
            "rtp_timestamp_90khz": row.get("rtp_timestamp_90khz", ""),
            "frame_id": row.get("frame_id", ""),
            "rtp_parse_offset": row.get("rtp_parse_offset", ""),
            "uav_x_north": as_float(row.get("uav_x_north")),
            "uav_y_east": as_float(row.get("uav_y_east")),
            "uav_z_m": as_float(row.get("uav_z_m")),
            "uav_lat": as_float(row.get("uav_lat")),
            "uav_lon": as_float(row.get("uav_lon")),
            "uav_alt_rel_m": as_float(row.get("uav_alt_rel_m")),
            "gcs_x_north": as_float(row.get("gcs_x_north")),
            "gcs_y_east": as_float(row.get("gcs_y_east")),
            "gcs_z_m": as_float(row.get("gcs_z_m")),
            "RSSI_from_Sionna": rssi_from,
            "RSSI_used_for_drop_decision": rssi_used,
            "expected_p_loss": as_float(row.get("expected_p_loss")),
            "error_rate_used_for_drop": as_float(row.get("error_rate_used_for_drop")),
            "random_draw": as_float(row.get("random_draw")),
            "final_decision": decision,
            "drop_reason": row.get("drop_reason", ""),
            "ns3_trace_source": row.get("ns3_trace_source", ""),
            "seed": as_int(row.get("seed")),
            "sionna_sample_valid": sample_valid,
            "sionna_sample_sequence": as_int(row.get("sionna_sample_sequence")),
            "sionna_channel_model": channel_model,
            "sionna_source_wall_time": as_float(row.get("sionna_source_wall_time")),
            "sionna_source_sim_time": as_float(row.get("sionna_source_sim_time")),
            "ns3_sionna_update_time": as_float(row.get("ns3_sionna_update_time")),
            "path_loss_db": as_float(row.get("path_loss_db")),
            "extra_delay_ms": as_float(row.get("extra_delay_ms")),
            "error_rate_reason": row.get("error_rate_reason", ""),
            "attempted": 1,
            "received": 1 if decision == "received" else 0,
            "dropped": 1 if decision == "dropped" else 0,
            "is_live_rt_route_packet": is_live_route,
            "rssi_delta_abs": (
                abs(rssi_from - rssi_used)
                if finite(rssi_from) and finite(rssi_used) else math.nan
            ),
        })
    return out


def live_packets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("is_live_rt_route_packet")]


def aggregate_bins(
    rows: list[dict[str, Any]],
    bin_width_db: float,
    min_packets: int,
    no_coverage_floor_db: float,
) -> list[dict[str, Any]]:
    valid = [
        row for row in rows
        if finite(as_float(row.get("RSSI_used_for_drop_decision")))
    ]
    if not valid:
        return []

    floor_rows = [r for r in valid if as_float(r["RSSI_used_for_drop_decision"]) <= no_coverage_floor_db]
    normal = [r for r in valid if as_float(r["RSSI_used_for_drop_decision"]) > no_coverage_floor_db]
    out: list[dict[str, Any]] = []

    def append_bucket(flow: str, lo: float, hi: float, bucket: list[dict[str, Any]], coverage_class: str) -> None:
        attempted = len(bucket)
        dropped = sum(as_int(row["dropped"]) for row in bucket)
        received = sum(as_int(row["received"]) for row in bucket)
        rssi_values = [as_float(row["RSSI_used_for_drop_decision"]) for row in bucket]
        expected_values = [as_float(row["expected_p_loss"]) for row in bucket]
        empirical = dropped / attempted if attempted else math.nan
        ci_low, ci_high = wilson_interval(dropped, attempted)
        expected_mean = sum(expected_values) / len(expected_values) if expected_values else math.nan
        out.append({
            "flow_id": flow,
            "channel_type": bucket[0].get("channel_type", flow),
            "coverage_class": coverage_class,
            "rssi_bin_low": lo,
            "rssi_bin_high": hi,
            "rssi_bin_center": (lo + hi) / 2.0 if hi > lo else lo,
            "median_rssi": float(np.median(rssi_values)),
            "attempted_packets": attempted,
            "received_packets": received,
            "dropped_packets": dropped,
            "empirical_loss_ratio": empirical,
            "expected_loss_ratio_mean": expected_mean,
            "ci95_low": ci_low,
            "ci95_high": ci_high,
            "absolute_error": abs(empirical - expected_mean) if finite(empirical) and finite(expected_mean) else math.nan,
            "low_confidence": attempted < min_packets,
        })

    floor_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in floor_rows:
        floor_grouped[str(row["flow_id"])].append(row)
    for flow, bucket in sorted(floor_grouped.items()):
        append_bucket(flow, no_coverage_floor_db, no_coverage_floor_db, bucket, "no_coverage_floor")

    if not normal:
        return out

    min_rssi = math.floor(min(as_float(r["RSSI_used_for_drop_decision"]) for r in normal) / bin_width_db) * bin_width_db
    max_rssi = math.ceil(max(as_float(r["RSSI_used_for_drop_decision"]) for r in normal) / bin_width_db) * bin_width_db
    if min_rssi == max_rssi:
        max_rssi += bin_width_db

    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in normal:
        flow = str(row["flow_id"])
        rssi = as_float(row["RSSI_used_for_drop_decision"])
        lo = math.floor((rssi - min_rssi) / bin_width_db) * bin_width_db + min_rssi
        grouped[(flow, lo)].append(row)

    for (flow, lo), bucket in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        append_bucket(flow, lo, lo + bin_width_db, bucket, "normal")
    return out


def downsample(rows: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return rows
    step = max(1, len(rows) // max_points)
    return rows[::step]


def plot_trace(rows: list[dict[str, Any]], out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(7.16, 4.1), sharex=True)
    fig.suptitle("Stage 2.4 route-level Sionna-to-ns-3 packet audit", fontsize=11)

    if not rows:
        for ax in axes:
            ax.text(0.5, 0.5, "No live RT packet audit rows", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
    else:
        samples_by_seq: dict[int, dict[str, Any]] = {}
        for row in rows:
            seq = as_int(row.get("sionna_sample_sequence"), -1)
            if seq < 0 or seq in samples_by_seq:
                continue
            samples_by_seq[seq] = row
        samples = sorted(samples_by_seq.values(), key=lambda row: as_float(row.get("timestamp")))
        colors = {"control": "#0B5FA5", "payload": "#B23A48"}
        if samples:
            samples = downsample(samples, 2500)
            axes[0].plot(
                [as_float(r["timestamp"]) for r in samples],
                [as_float(r["RSSI_used_for_drop_decision"]) for r in samples],
                color="#2A6F55",
                linewidth=1.1,
                label="live Sionna RSSI",
            )
        else:
            packet_samples = downsample(rows, 2500)
            axes[0].plot(
                [as_float(r["timestamp"]) for r in packet_samples],
                [as_float(r["RSSI_used_for_drop_decision"]) for r in packet_samples],
                color="#2A6F55",
                linewidth=1.1,
                label="packet RSSI",
            )
        axes[0].set_ylabel("RSSI, dBm")
        axes[0].grid(True, alpha=0.25)
        axes[0].legend(frameon=False, fontsize=8)

        dropped = [row for row in rows if row["final_decision"] == "dropped"]
        flows = sorted({str(row["flow_id"]) for row in rows})
        flow_y = {flow: idx for idx, flow in enumerate(flows)}
        for flow in flows:
            flow_drops = downsample([row for row in dropped if str(row["flow_id"]) == flow], 5000)
            if not flow_drops:
                continue
            axes[1].scatter(
                [as_float(r["timestamp"]) for r in flow_drops],
                [flow_y[flow] for _ in flow_drops],
                s=7,
                color=colors.get(flow, "#C23B22"),
                alpha=0.45,
                label=f"{flow} dropped",
            )
        if not dropped:
            axes[1].text(0.5, 0.5, "No packet drops", ha="center", va="center", transform=axes[1].transAxes)
        axes[1].set_yticks([flow_y[flow] for flow in flows], flows)
        axes[1].set_xlabel("ns-3 simulation time, s")
        axes[1].set_ylabel("Drop events")
        axes[1].grid(True, alpha=0.25)
        handles, labels = axes[1].get_legend_handles_labels()
        if handles:
            axes[1].legend(handles, labels, frameon=False, fontsize=8)
    fig.tight_layout()
    for suffix in (".png", ".pdf", ".svg"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(out_prefix.with_suffix(suffix), **kwargs)
    plt.close(fig)


def plot_map(rows: list[dict[str, Any]], out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(3.5, 3.2))
    ax.set_title("Stage 2.4 route RSSI map", fontsize=10)
    if not rows:
        ax.text(0.5, 0.5, "No live RT packet audit rows", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        points = []
        seen: set[tuple[int, float, float]] = set()
        for row in rows:
            seq = as_int(row["sionna_sample_sequence"])
            x = as_float(row["uav_x_north"])
            y = as_float(row["uav_y_east"])
            if not finite(x) or not finite(y):
                continue
            key = (seq, round(x, 2), round(y, 2))
            if key in seen:
                continue
            seen.add(key)
            points.append(row)
        points.sort(key=lambda r: as_float(r["timestamp"]))
        xs = [as_float(r["uav_x_north"]) for r in points]
        ys = [as_float(r["uav_y_east"]) for r in points]
        rssi = [as_float(r["RSSI_used_for_drop_decision"]) for r in points]
        ax.plot(ys, xs, color="#4A4A4A", linewidth=0.8, alpha=0.45)
        sc = ax.scatter(ys, xs, c=rssi, cmap="viridis", s=14, edgecolor="none")
        gcs_rows = [r for r in rows if finite(as_float(r["gcs_x_north"])) and finite(as_float(r["gcs_y_east"]))]
        if gcs_rows:
            ax.scatter(
                [as_float(gcs_rows[0]["gcs_y_east"])],
                [as_float(gcs_rows[0]["gcs_x_north"])],
                marker="^",
                s=55,
                color="#111111",
                label="GCS/tx",
            )
            ax.legend(frameon=False, fontsize=8)
        ax.set_xlabel("East, m")
        ax.set_ylabel("North, m")
        ax.grid(True, alpha=0.25)
        ax.set_aspect("equal", adjustable="datalim")
        fig.colorbar(sc, ax=ax, label="RSSI, dBm")
    fig.tight_layout()
    for suffix in (".png", ".pdf", ".svg"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(out_prefix.with_suffix(suffix), **kwargs)
    plt.close(fig)


def plot_bins(bins: list[dict[str, Any]], out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    flows = [flow for flow in ("control", "payload") if any(str(b["flow_id"]) == flow for b in bins)]
    if not flows:
        flows = sorted({str(b["flow_id"]) for b in bins})
    fig, axes = plt.subplots(1, max(1, len(flows)), figsize=(7.16, 2.65), sharey=True)
    if not isinstance(axes, np.ndarray):
        axes = np.array([axes])
    fig.suptitle("Stage 2.4 route-level simulated loss vs RSSI", fontsize=10)
    if not bins:
        axes[0].text(0.5, 0.5, "No live RT bins", ha="center", va="center", transform=axes[0].transAxes)
        axes[0].set_axis_off()
    else:
        normal_bins = [b for b in bins if b.get("coverage_class", "normal") == "normal"]
        plot_domain = normal_bins or bins
        x_min = min(as_float(b["rssi_bin_low"]) for b in plot_domain) - 2.0
        x_max = max(as_float(b["rssi_bin_high"]) for b in plot_domain) + 2.0
        x_ref = np.linspace(x_min, x_max, 300)
        colors = {"control": "#0B5FA5", "payload": "#B23A48"}
        for ax, flow in zip(axes, flows):
            ax.set_title(flow, fontsize=9)
            ax.plot(x_ref, repository_loss_reference(x_ref), "--", color="#4A4A4A", linewidth=1.0, label="Repository mapping")
            flow_bins = sorted(
                [b for b in bins if b["flow_id"] == flow and b.get("coverage_class", "normal") == "normal"],
                key=lambda b: as_float(b["rssi_bin_center"]),
            )
            floor_bins = [b for b in bins if b["flow_id"] == flow and b.get("coverage_class") == "no_coverage_floor"]
            if flow == "payload" and sum(as_int(b["attempted_packets"]) for b in flow_bins) < PAYLOAD_ROUTE_MIN_PACKETS:
                ax.text(0.5, 0.5, "payload n too low", ha="center", va="center", transform=ax.transAxes, fontsize=8)
            good = [b for b in flow_bins if not b["low_confidence"]]
            low = [b for b in flow_bins if b["low_confidence"]]
            for group, marker, label_suffix, alpha in ((good, "o", "", 0.95), (low, "x", " low n", 0.65)):
                if not group:
                    continue
                x = np.array([as_float(b["rssi_bin_center"]) for b in group])
                y = np.array([as_float(b["empirical_loss_ratio"]) for b in group])
                lo = np.array([as_float(b["ci95_low"]) for b in group])
                hi = np.array([as_float(b["ci95_high"]) for b in group])
                yerr = np.vstack([np.maximum(0.0, y - lo), np.maximum(0.0, hi - y)])
                sizes = np.array([max(24.0, min(140.0, math.sqrt(as_int(b["attempted_packets"])) * 5.0)) for b in group])
                ax.errorbar(x, y, yerr=yerr, fmt="none", ecolor=colors.get(flow, "#555555"), alpha=alpha, capsize=2)
                ax.scatter(x, y, s=sizes, marker=marker, color=colors.get(flow, "#555555"), alpha=alpha, label=f"{flow}{label_suffix}")
            for floor_bin in floor_bins:
                ax.scatter(
                    [x_min],
                    [as_float(floor_bin["empirical_loss_ratio"])],
                    marker="s",
                    s=50,
                    color="#111111",
                    alpha=0.8,
                    label=f"no-coverage floor, n={as_int(floor_bin['attempted_packets'])}",
                )
            ax.set_xlabel("RSSI, dBm")
            ax.set_ylim(-0.03, 1.03)
            ax.grid(True, alpha=0.25)
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                ax.legend(handles, labels, frameon=False, fontsize=6.5)
        axes[0].set_ylabel("Packet loss ratio")
        for ax in axes[len(flows):]:
            ax.set_axis_off()
    fig.tight_layout()
    for suffix in (".png", ".pdf", ".svg"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(out_prefix.with_suffix(suffix), **kwargs)
    plt.close(fig)


def write_reports(
    audit_path: Path,
    summary_path: Path,
    all_packets: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    bins: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    commit = git_value(["rev-parse", "HEAD"])
    dirty = git_value(["status", "--short"], default="")
    channel_counts = Counter(str(row.get("sionna_channel_model", "")) for row in all_packets)
    history_rows = read_jsonl(args.sionna_history)
    rt_variant_counts = Counter(str(row.get("rt_variant", "")) for row in history_rows if row.get("rt_variant"))
    run_warnings = recorder_warnings(args.run_dir)
    run_ids = sorted({str(row["run_id"]) for row in all_packets if row.get("run_id")})
    max_delta = max(
        [as_float(row["rssi_delta_abs"]) for row in packets if finite(as_float(row["rssi_delta_abs"]))],
        default=math.nan,
    )
    outside_ci = [
        b for b in bins
        if finite(as_float(b["expected_loss_ratio_mean"]))
        and not (as_float(b["ci95_low"]) <= as_float(b["expected_loss_ratio_mean"]) <= as_float(b["ci95_high"]))
    ]
    per_flow = defaultdict(lambda: {"attempted": 0, "received": 0, "dropped": 0})
    for row in packets:
        flow = str(row["flow_id"])
        per_flow[flow]["attempted"] += 1
        per_flow[flow]["received"] += as_int(row["received"])
        per_flow[flow]["dropped"] += as_int(row["dropped"])
    rssi_values = [
        as_float(row["RSSI_used_for_drop_decision"])
        for row in packets
        if finite(as_float(row["RSSI_used_for_drop_decision"]))
    ]
    normal_rssi_values = [value for value in rssi_values if value > args.no_coverage_floor_db]
    rssi_min = min(rssi_values) if rssi_values else math.nan
    rssi_max = max(rssi_values) if rssi_values else math.nan
    normal_rssi_min = min(normal_rssi_values) if normal_rssi_values else math.nan
    normal_rssi_max = max(normal_rssi_values) if normal_rssi_values else math.nan
    normal_bins = [b for b in bins if b.get("coverage_class", "normal") == "normal"]
    no_coverage_bins = [b for b in bins if b.get("coverage_class") == "no_coverage_floor"]
    run_command = args.command.strip() if args.command else (
        "sudo env BAS_STAGE24_PACKET_AUDIT=1 BAS_SIONNA_RT_ONLINE=1 "
        "BAS_SIONNA_REQUIRE_GPU=1 BAS_MITSUBA_VARIANT=cuda_ad_mono_polarized "
        "BAS_SIONNA_TARGET_FLOW=both bash scripts/run_stage24_route_rssi_packet_audit.sh"
    )
    analysis_command = (
        f".venv/bin/python scripts/analyze_stage24_route_rssi_packet_audit.py "
        f"--run-dir {args.run_dir}"
    )

    audit_lines = [
        "# Stage 2.4 Route RSSI Packet Audit",
        "",
        "This audit is for the route-level Stage 2.4 RT-online pipeline. It is separate from the controlled RSSI target consistency check.",
        "",
        "## Code Path",
        "",
        "1. `scripts/gcs_web_ui_server.py` writes `event_type=flight` rows from the live Stage 2.4 Web GCS telemetry.",
        "2. `scripts/sionna_channel_publisher.py --rt-online` tails those flight rows, runs live Sionna RT PathSolver, and writes current `rss_db`, position, GCS/tx position, and `loss_ratio` to the RT JSON file.",
        "3. `ns3/scenarios/two_channel.cc::sionna_poll_tick()` polls the RT JSON and updates the selected `AuditedRateErrorModel` instances for `control`, `payload`, or `both`.",
        "4. `AuditedRateErrorModel::DoCorrupt()` performs the packet-level Bernoulli decision and writes the packet audit CSV row.",
        "",
        "Because the CSV row is written inside `DoCorrupt()`, `RSSI_used_for_drop_decision` is the value stored in the ns-3 error model at the packet decision point.",
        "",
        "## Evidence",
        "",
        f"- Commit hash: `{commit}`",
        f"- Worktree state: `{'dirty' if dirty else 'clean'}`",
        f"- Run IDs: `{', '.join(run_ids) if run_ids else 'unknown'}`",
        f"- Raw packet CSV: `{args.packet_csv}`",
        f"- Sionna history: `{args.sionna_history}`",
        f"- Events JSONL: `{args.events}`",
        f"- ns-3 events JSONL: `{args.ns3_events}`",
        f"- Full route-run command: `{run_command}`",
        f"- Reanalysis command: `{analysis_command}`",
        f"- No-coverage floor threshold: `{args.no_coverage_floor_db}` dBm",
        f"- Packet rows total before live-RT filtering: `{len(all_packets)}`",
        f"- Live RT route packet rows used: `{len(packets)}`",
        f"- Sionna RT history rows: `{len(history_rows)}`",
        f"- Sionna RT variants: `{dict(rt_variant_counts)}`",
        f"- Sionna channel model counts: `{dict(channel_counts)}`",
        f"- Max absolute RSSI_from_Sionna vs RSSI_used delta: `{max_delta}`",
        f"- Recorder route warnings: `{len(run_warnings)}`",
        "",
    ]
    if not packets:
        audit_lines.extend([
            "## Blocking Finding",
            "",
            "No packet rows with `sionna_channel_model=rt_online` were available. Therefore the current Stage 2.4 run cannot be claimed as a route-level live Sionna-to-ns-3 packet audit.",
            "",
        ])
    elif finite(max_delta) and max_delta <= 1e-9:
        audit_lines.extend([
            "## RSSI Link Check",
            "",
            "Verified: every live RT packet row has `RSSI_from_Sionna == RSSI_used_for_drop_decision` within numeric precision.",
            "",
        ])
    else:
        audit_lines.extend([
            "## RSSI Link Check",
            "",
            "Mismatch: at least one live RT packet row has different `RSSI_from_Sionna` and `RSSI_used_for_drop_decision`.",
            "",
        ])
    audit_lines.extend([
        "## Route Execution Warnings",
        "",
    ])
    if run_warnings:
        for warning in run_warnings:
            audit_lines.append(f"- `{warning}`")
    else:
        audit_lines.append("- No recorder route warnings were found.")
    audit_lines.extend([
        "",
        "## Limitations",
        "",
        "- This is packet-level simulated loss over a Stage 2.4 route-run, not hardware-measured PER.",
        "- Binned loss-vs-RSSI is secondary and depends on the RSSI distribution actually visited by the route.",
        "- Payload is reported only if packets actually traverse the ns-3 payload channel during the run.",
        "",
    ])
    audit_path.write_text("\n".join(audit_lines), encoding="utf-8")

    total_attempted = len(packets)
    total_received = sum(as_int(row["received"]) for row in packets)
    total_dropped = sum(as_int(row["dropped"]) for row in packets)
    summary_lines = [
        "# Stage 2.4 Route RSSI Packet Summary",
        "",
        "This is a Stage 2.4 route-level Sionna-to-ns-3 packet audit using live RT route samples. It is simulated packet-level evidence, not hardware validation.",
        "",
        "## Counts",
        "",
        f"- Live RT route packets used: {total_attempted}",
        f"- Received: {total_received}",
        f"- Dropped: {total_dropped}",
        f"- RSSI range used for packet decisions: {rssi_min:.3f}..{rssi_max:.3f} dBm",
        f"- Normal RSSI range above no-coverage floor: {normal_rssi_min:.3f}..{normal_rssi_max:.3f} dBm",
        f"- RSSI bins: {len(bins)}",
        f"- Normal RSSI bins: {len(normal_bins)}",
        f"- No-coverage floor bins: {len(no_coverage_bins)}",
        f"- Recorder route warnings: {len(run_warnings)}",
    ]
    for flow in sorted(per_flow):
        item = per_flow[flow]
        summary_lines.append(
            f"- {flow}: attempted={item['attempted']} received={item['received']} dropped={item['dropped']}"
        )
    payload_attempted = per_flow.get("payload", {}).get("attempted", 0)
    if payload_attempted == 0:
        summary_lines.append("- Payload status: no Stage 2.4 payload packets traversed the ns-3 payload channel in this run; payload is not plotted as verified.")
    elif payload_attempted < PAYLOAD_ROUTE_MIN_PACKETS:
        summary_lines.append(f"- Payload status: only {payload_attempted} packets traversed the ns-3 payload channel; payload route audit not supported in current Stage 2.4 path.")
    else:
        summary_lines.append("- Payload status: payload packets traversed ns-3 and are included.")
    if run_warnings:
        summary_lines.extend([
            "",
            "## Route Execution Warnings",
            "",
        ])
        for warning in run_warnings:
            summary_lines.append(f"- `{warning}`")
    if rssi_values and rssi_min > -65.0:
        summary_lines.append("- Route RSSI limitation: this Stage 2.4 run stayed in a strong-signal region, so it does not test transition/weak RSSI behavior.")
    if rssi_values and (rssi_min > -90.0 or rssi_max < -65.0):
        summary_lines.append(
            "- Route RSSI coverage limitation: the final RF-stress corridor covers "
            f"{rssi_min:.3f}..{rssi_max:.3f} dBm; it avoids no-coverage blackout but does not span the full -90..-65 dBm envelope."
        )
    if outside_ci:
        summary_lines.extend([
            "",
            "## Mapping Mismatch Candidates",
            "",
        ])
        for row in outside_ci:
            summary_lines.append(
                f"- {row['flow_id']} RSSI bin {as_float(row['rssi_bin_low']):.1f}.."
                f"{as_float(row['rssi_bin_high']):.1f} dBm: empirical "
                f"{as_float(row['empirical_loss_ratio']):.6f}, expected "
                f"{as_float(row['expected_loss_ratio_mean']):.6f}, CI95 "
                f"[{as_float(row['ci95_low']):.6f}, {as_float(row['ci95_high']):.6f}], "
                f"n={as_int(row['attempted_packets'])}"
            )
    summary_lines.extend([
        "",
        "## Artifacts",
        "",
        "- `data/processed/stage24_route_rssi_packets.csv`",
        "- `data/processed/stage24_route_rssi_bins.csv`",
        "- `figures/stage24_route_rssi_packet_trace.png` / `.pdf` / `.svg`",
        "- `figures/stage24_route_rssi_map.png` / `.pdf` / `.svg`",
        "- `figures/stage24_route_rssi_loss_bins.png` / `.pdf` / `.svg`",
        "",
    ])
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--packet-csv", type=Path, default=None)
    parser.add_argument("--sionna-history", type=Path, default=None)
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--ns3-events", type=Path, default=None)
    parser.add_argument("--packets-out", type=Path, default=Path("data/processed/stage24_route_rssi_packets.csv"))
    parser.add_argument("--bins-out", type=Path, default=Path("data/processed/stage24_route_rssi_bins.csv"))
    parser.add_argument("--trace-prefix", type=Path, default=Path("figures/stage24_route_rssi_packet_trace"))
    parser.add_argument("--map-prefix", type=Path, default=Path("figures/stage24_route_rssi_map"))
    parser.add_argument("--bins-prefix", type=Path, default=Path("figures/stage24_route_rssi_loss_bins"))
    parser.add_argument("--audit-out", type=Path, default=Path("reports/stage24_route_rssi_packet_audit.md"))
    parser.add_argument("--summary-out", type=Path, default=Path("reports/stage24_route_rssi_packet_summary.md"))
    parser.add_argument("--bin-width-db", type=float, default=2.0)
    parser.add_argument("--min-packets-per-bin", type=int, default=100)
    parser.add_argument("--no-coverage-floor-db", type=float, default=-120.0)
    parser.add_argument("--command", default="")
    args = parser.parse_args()

    run_dir = args.run_dir
    args.packet_csv = args.packet_csv or (run_dir / "stage24_route_rssi_packets_raw.csv")
    args.sionna_history = args.sionna_history or (run_dir / "sionna_rt_history.jsonl")
    args.events = args.events or (run_dir / "events.jsonl")
    args.ns3_events = args.ns3_events or (run_dir / "ns3_events.jsonl")

    raw_rows = read_csv(args.packet_csv)
    all_packets = normalize_packets(raw_rows, args.packet_csv)
    packets = live_packets(all_packets)
    bins = aggregate_bins(packets, args.bin_width_db, args.min_packets_per_bin, args.no_coverage_floor_db)

    packet_fields = [
        "source_log", "row_index", "run_id", "packet_uid", "timestamp",
        "flow_id", "channel_type", "tx_node", "rx_node", "direction",
        "packet_size", "rtp_sequence_number", "rtp_timestamp_90khz",
        "frame_id", "rtp_parse_offset",
        "uav_x_north", "uav_y_east", "uav_z_m", "uav_lat", "uav_lon",
        "uav_alt_rel_m", "gcs_x_north", "gcs_y_east", "gcs_z_m",
        "RSSI_from_Sionna", "RSSI_used_for_drop_decision", "expected_p_loss",
        "error_rate_used_for_drop", "random_draw", "final_decision",
        "drop_reason", "ns3_trace_source", "seed", "sionna_sample_valid",
        "sionna_sample_sequence", "sionna_channel_model",
        "sionna_source_wall_time", "sionna_source_sim_time",
        "ns3_sionna_update_time", "path_loss_db", "extra_delay_ms",
        "error_rate_reason", "attempted", "received", "dropped",
        "is_live_rt_route_packet", "rssi_delta_abs",
    ]
    bin_fields = [
        "flow_id", "channel_type", "coverage_class", "rssi_bin_low", "rssi_bin_high",
        "rssi_bin_center", "median_rssi", "attempted_packets",
        "received_packets", "dropped_packets", "empirical_loss_ratio",
        "expected_loss_ratio_mean", "ci95_low", "ci95_high",
        "absolute_error", "low_confidence",
    ]
    write_csv(args.packets_out, packets, packet_fields)
    write_csv(args.bins_out, bins, bin_fields)
    plot_trace(packets, args.trace_prefix)
    plot_map(packets, args.map_prefix)
    plot_bins(bins, args.bins_prefix)
    write_reports(args.audit_out, args.summary_out, all_packets, packets, bins, args)

    print(f"raw packet rows: {len(raw_rows)}")
    print(f"live RT route packet rows: {len(packets)}")
    print(f"bins: {len(bins)}")
    print(f"wrote: {args.packets_out}")
    print(f"wrote: {args.bins_out}")
    print(f"wrote: {args.trace_prefix}.png/.pdf/.svg")
    print(f"wrote: {args.map_prefix}.png/.pdf/.svg")
    print(f"wrote: {args.bins_prefix}.png/.pdf/.svg")
    print(f"wrote: {args.audit_out}")
    print(f"wrote: {args.summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
