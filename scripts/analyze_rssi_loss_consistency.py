#!/usr/bin/env python3
"""Analyze a controlled packet-level ns-3 RSSI/loss consistency run."""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib  # type: ignore

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # type: ignore
import numpy as np  # type: ignore


DEFAULT_TARGETS = "-95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60"
DEFAULT_FLOWS = "control,payload"


def repository_loss_reference(rssi_db: float | np.ndarray) -> float | np.ndarray:
    return 1.0 / (1.0 + np.exp(0.5 * (np.asarray(rssi_db) + 78.0)))


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


def parse_targets(value: str) -> list[float]:
    return [float(item) for item in value.split(",") if item.strip()]


def parse_flows(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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


def read_packets(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def normalize_packets(
    rows: list[dict[str, str]],
    source_log: Path,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        decision = row.get("final_decision", "")
        dropped = 1 if decision == "dropped" else 0
        received = 1 if decision == "received" else 0
        out.append({
            "source_log": str(source_log),
            "row_index": idx,
            "run_id": row.get("run_id", ""),
            "packet_uid": row.get("packet_uid", ""),
            "timestamp": as_float(row.get("timestamp")),
            "flow_id": row.get("flow_id", ""),
            "channel_type": row.get("channel_type", ""),
            "tx_node": row.get("tx_node", ""),
            "rx_node": row.get("rx_node", ""),
            "rssi_db_used": as_float(row.get("rssi_db_used")),
            "expected_p_loss": as_float(row.get("expected_p_loss")),
            "random_draw": as_float(row.get("random_draw")),
            "final_decision": decision,
            "drop_reason": row.get("drop_reason", ""),
            "seed": as_int(row.get("seed")),
            "rssi_target": as_float(row.get("rssi_target")),
            "attempted": 1,
            "received": received,
            "dropped": dropped,
        })
    return out


def aggregate_bins(
    packets: list[dict[str, Any]],
    targets: list[float],
    flows: list[str],
    min_packets: int,
    min_seeds: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in packets:
        flow = str(row["flow_id"])
        target = as_float(row["rssi_target"])
        if not math.isfinite(target):
            continue
        grouped[(flow, target)].append(row)

    bins: list[dict[str, Any]] = []
    for flow in flows:
        for target in targets:
            rows = grouped.get((flow, target), [])
            attempted = len(rows)
            dropped = sum(as_int(r.get("dropped")) for r in rows)
            received = sum(as_int(r.get("received")) for r in rows)
            seed_counts: dict[int, int] = defaultdict(int)
            for row in rows:
                seed_counts[as_int(row.get("seed"))] += 1
            seed_count = len(seed_counts)
            expected_values = [
                as_float(row.get("expected_p_loss"))
                for row in rows
                if math.isfinite(as_float(row.get("expected_p_loss")))
            ]
            expected_mean = (
                sum(expected_values) / len(expected_values)
                if expected_values else math.nan
            )
            empirical = dropped / attempted if attempted else math.nan
            ci_low, ci_high = wilson_interval(dropped, attempted)
            abs_error = abs(empirical - expected_mean) if math.isfinite(empirical) else math.nan
            bins.append({
                "flow_id": flow,
                "channel_type": rows[0]["channel_type"] if rows else flow,
                "rssi_target": target,
                "rssi_db_used_mean": (
                    sum(as_float(r["rssi_db_used"]) for r in rows) / attempted
                    if attempted else math.nan
                ),
                "attempted_packets": attempted,
                "received_packets": received,
                "dropped_packets": dropped,
                "empirical_loss_ratio": empirical,
                "expected_loss_ratio_mean": expected_mean,
                "ci95_low": ci_low,
                "ci95_high": ci_high,
                "absolute_error": abs_error,
                "seed_count": seed_count,
                "min_attempted_per_seed": min(seed_counts.values()) if seed_counts else 0,
                "max_attempted_per_seed": max(seed_counts.values()) if seed_counts else 0,
                "packet_threshold_pass": attempted >= min_packets,
                "seed_threshold_pass": seed_count >= min_seeds,
            })
    return bins


def plot_ieee(bins: list[dict[str, Any]], output_prefix: Path) -> None:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    flows = [flow for flow in ("control", "payload") if any(b["flow_id"] == flow for b in bins)]
    if not flows:
        flows = sorted({str(b["flow_id"]) for b in bins})

    fig, axes = plt.subplots(
        1,
        len(flows),
        figsize=(7.16, 2.75 if len(flows) > 1 else 3.0),
        sharey=True,
    )
    if len(flows) == 1:
        axes = [axes]  # type: ignore[assignment]

    x_ref = np.linspace(-97.0, -58.0, 400)
    y_ref = repository_loss_reference(x_ref)
    colors = {
        "control": "#0B5FA5",
        "payload": "#B23A48",
    }

    for ax, flow in zip(axes, flows):
        flow_bins = sorted(
            [b for b in bins if b["flow_id"] == flow],
            key=lambda b: as_float(b["rssi_target"]),
        )
        x = np.array([as_float(b["rssi_target"]) for b in flow_bins])
        y = np.array([as_float(b["empirical_loss_ratio"]) for b in flow_bins])
        low = np.array([as_float(b["ci95_low"]) for b in flow_bins])
        high = np.array([as_float(b["ci95_high"]) for b in flow_bins])
        yerr = np.vstack([np.maximum(0.0, y - low), np.maximum(0.0, high - y)])

        ax.plot(
            x_ref,
            y_ref,
            "--",
            color="#4A4A4A",
            linewidth=1.3,
            label="Repository mapping",
        )
        ax.errorbar(
            x,
            y,
            yerr=yerr,
            fmt="o",
            color=colors.get(flow, "#0B5FA5"),
            ecolor=colors.get(flow, "#0B5FA5"),
            elinewidth=1.1,
            capsize=2.5,
            markersize=4.6,
            label="Empirical ns-3 loss",
        )
        ax.set_title(flow.capitalize(), fontsize=9)
        ax.set_xlabel("RSSI, dBm")
        ax.set_xlim(-97, -58)
        ax.set_ylim(-0.03, 1.03)
        ax.grid(True, color="#A0A0A0", alpha=0.28, linewidth=0.6)
        ax.tick_params(labelsize=8)

    axes[0].set_ylabel("Packet loss ratio")
    fig.suptitle("Packet-level ns-3 loss consistency check", fontsize=11)
    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False, fontsize=8)
    fig.subplots_adjust(bottom=0.25, top=0.82, wspace=0.12)

    for suffix in (".png", ".pdf", ".svg"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(output_prefix.with_suffix(suffix), **kwargs)
    plt.close(fig)


def write_summary(
    path: Path,
    packets: list[dict[str, Any]],
    bins: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    commit = git_value(["rev-parse", "HEAD"])
    dirty = git_value(["status", "--short"], default="")
    run_ids = sorted({str(row["run_id"]) for row in packets if row.get("run_id")})
    total_attempted = len(packets)
    total_dropped = sum(as_int(row["dropped"]) for row in packets)
    total_received = sum(as_int(row["received"]) for row in packets)
    good_bins = [
        row for row in bins
        if row["packet_threshold_pass"] and row["seed_threshold_pass"]
    ]
    mae_values = [
        as_float(row["absolute_error"])
        for row in good_bins
        if math.isfinite(as_float(row["absolute_error"]))
    ]
    overall_mae = sum(mae_values) / len(mae_values) if mae_values else math.nan
    by_flow: dict[str, list[float]] = defaultdict(list)
    for row in good_bins:
        value = as_float(row["absolute_error"])
        if math.isfinite(value):
            by_flow[str(row["flow_id"])].append(value)

    max_abs = max(mae_values) if mae_values else math.nan
    outside_ci = [
        row for row in good_bins
        if not (
            as_float(row["ci95_low"]) <= as_float(row["expected_loss_ratio_mean"]) <= as_float(row["ci95_high"])
        )
    ]
    status = (
        "No implementation mismatch was detected at 95% binomial CI."
        if not outside_ci else
        f"{len(outside_ci)} bins have the configured mapping outside the 95% CI; "
        "record this as an implementation/statistical mismatch candidate."
    )

    lines = [
        "# RSSI Loss Consistency Summary",
        "",
        "This is a simulated packet-level ns-3 consistency check. It is not a "
        "hardware-measured PER result and it is not a field calibration of the "
        "radio channel.",
        "",
        "## Result",
        "",
        f"- Status: {status}",
        f"- Total attempted packets: {total_attempted}",
        f"- Received packets: {total_received}",
        f"- Dropped packets: {total_dropped}",
        f"- RSSI/flow bins passing thresholds: {len(good_bins)} / {len(bins)}",
        f"- Minimum packet threshold per RSSI/flow bin: {args.min_packets_per_target}",
        f"- Minimum seed threshold: {args.min_seeds}",
        f"- Overall MAE over passing bins: {overall_mae:.6f}",
        f"- Maximum absolute error over passing bins: {max_abs:.6f}",
    ]
    for flow, values in sorted(by_flow.items()):
        flow_mae = sum(values) / len(values) if values else math.nan
        lines.append(f"- {flow} MAE: {flow_mae:.6f}")

    if outside_ci:
        lines.extend([
            "",
            "## Mismatch Candidates",
            "",
            "The configured mapping lies outside the 95% binomial confidence interval "
            "for the following bins. This may be a statistical tail under multiple "
            "comparisons, but it is reported explicitly rather than hidden.",
            "",
        ])
        for row in outside_ci:
            lines.append(
                f"- `{row['flow_id']}` at `{as_float(row['rssi_target']):.1f} dBm`: "
                f"empirical `{as_float(row['empirical_loss_ratio']):.6f}`, "
                f"expected `{as_float(row['expected_loss_ratio_mean']):.6f}`, "
                f"CI95 `[{as_float(row['ci95_low']):.6f}, "
                f"{as_float(row['ci95_high']):.6f}]`, "
                f"abs error `{as_float(row['absolute_error']):.6f}`"
            )

    lines.extend([
        "",
        "## Reproducibility",
        "",
        f"- Commit hash: `{commit}`",
        f"- Worktree state: `{'dirty' if dirty else 'clean'}`",
        f"- Run IDs: `{', '.join(run_ids) if run_ids else 'unknown'}`",
        f"- RSSI targets: `{args.targets}`",
        f"- Flows: `{args.flows}`",
        "- Wrapper env vars: `RUN_ID`, `TARGETS`, `FLOWS`, `SEEDS`, "
        "`PACKETS_PER_POINT`, `PACKET_BYTES`, `INTERVAL_MS`, `BASE_SEED`, "
        "`NS3_IMAGE`, `CONTAINER_NAME`",
        f"- Raw per-packet log: `{args.input}`",
        f"- Processed per-packet CSV: `{args.per_packet_out}`",
        f"- Processed bins CSV: `{args.bins_out}`",
        f"- Figure prefix: `{args.figure_prefix}`",
        f"- Command: `{args.command}`",
        "",
        "## Interpretation",
        "",
        "The RSSI value in the per-packet CSV is `rssi_db_used`, written inside "
        "`RssiLossDecisionErrorModel::DoCorrupt()`. Therefore it is the RSSI "
        "used for the drop decision in this controlled scenario, not merely a "
        "publisher-side Sionna RT sample.",
        "",
        "Rows with `final_decision=dropped` use `drop_reason=phy_rf_bernoulli`; "
        "the scenario does not model application timeouts, queues, MAVLink "
        "bridge failures, or orchestration-layer loss.",
        "",
        "Payload is checked as a controlled ns-3 payload flow in the calibration "
        "scenario. This does not by itself prove that every full Stage 2.4 "
        "high-rate video path is traversing ns-3 in a live mission.",
        "",
        "## Artifacts",
        "",
        "- `data/processed/rssi_loss_consistency_per_packet.csv`",
        "- `data/processed/rssi_loss_consistency_bins.csv`",
        "- `figures/rssi_loss_consistency_ieee.png`",
        "- `figures/rssi_loss_consistency_ieee.pdf`",
        "- `figures/rssi_loss_consistency_ieee.svg`",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument(
        "--per-packet-out",
        type=Path,
        default=Path("data/processed/rssi_loss_consistency_per_packet.csv"),
    )
    parser.add_argument(
        "--bins-out",
        type=Path,
        default=Path("data/processed/rssi_loss_consistency_bins.csv"),
    )
    parser.add_argument(
        "--figure-prefix",
        type=Path,
        default=Path("figures/rssi_loss_consistency_ieee"),
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("reports/rssi_loss_consistency_summary.md"),
    )
    parser.add_argument("--targets", default=DEFAULT_TARGETS)
    parser.add_argument("--flows", default=DEFAULT_FLOWS)
    parser.add_argument("--min-packets-per-target", type=int, default=1000)
    parser.add_argument("--min-seeds", type=int, default=5)
    parser.add_argument("--command", default="")
    args = parser.parse_args()

    raw = read_packets(args.input)
    packets = normalize_packets(raw, args.input)
    targets = parse_targets(args.targets)
    flows = parse_flows(args.flows)
    bins = aggregate_bins(
        packets,
        targets=targets,
        flows=flows,
        min_packets=args.min_packets_per_target,
        min_seeds=args.min_seeds,
    )

    per_packet_fields = [
        "source_log",
        "row_index",
        "run_id",
        "packet_uid",
        "timestamp",
        "flow_id",
        "channel_type",
        "tx_node",
        "rx_node",
        "rssi_db_used",
        "expected_p_loss",
        "random_draw",
        "final_decision",
        "drop_reason",
        "seed",
        "rssi_target",
        "attempted",
        "received",
        "dropped",
    ]
    bin_fields = [
        "flow_id",
        "channel_type",
        "rssi_target",
        "rssi_db_used_mean",
        "attempted_packets",
        "received_packets",
        "dropped_packets",
        "empirical_loss_ratio",
        "expected_loss_ratio_mean",
        "ci95_low",
        "ci95_high",
        "absolute_error",
        "seed_count",
        "min_attempted_per_seed",
        "max_attempted_per_seed",
        "packet_threshold_pass",
        "seed_threshold_pass",
    ]
    write_csv(args.per_packet_out, packets, per_packet_fields)
    write_csv(args.bins_out, bins, bin_fields)
    plot_ieee(bins, args.figure_prefix)
    write_summary(args.summary_out, packets, bins, args)

    passing = [
        row for row in bins
        if row["packet_threshold_pass"] and row["seed_threshold_pass"]
    ]
    print(f"raw packets: {len(raw)}")
    print(f"processed packets: {len(packets)}")
    print(f"bins passing thresholds: {len(passing)} / {len(bins)}")
    print(f"wrote: {args.per_packet_out}")
    print(f"wrote: {args.bins_out}")
    print(f"wrote: {args.figure_prefix}.png/.pdf/.svg")
    print(f"wrote: {args.summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
