#!/usr/bin/env python3
"""Analyze Stage 2.4 network-realistic payload path evidence."""

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


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def normalize_payload_packets(raw_rows: list[dict[str, str]], source: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(raw_rows):
        if row.get("flow_id") != "payload":
            continue
        rtp_seq = row.get("rtp_sequence_number", "")
        decision = row.get("final_decision", "")
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
            "rtp_sequence_number": as_int(rtp_seq, -1) if rtp_seq != "" else "",
            "rtp_timestamp_90khz": row.get("rtp_timestamp_90khz", ""),
            "frame_id": row.get("frame_id", ""),
            "rtp_parse_offset": row.get("rtp_parse_offset", ""),
            "RSSI_from_Sionna": as_float(row.get("RSSI_from_Sionna")),
            "RSSI_used_for_drop_decision": as_float(row.get("RSSI_used_for_drop_decision")),
            "expected_p_loss": as_float(row.get("expected_p_loss")),
            "random_draw": as_float(row.get("random_draw")),
            "final_decision": decision,
            "drop_reason": row.get("drop_reason", ""),
            "seed": row.get("seed", ""),
            "sionna_sample_valid": row.get("sionna_sample_valid", ""),
            "sionna_channel_model": row.get("sionna_channel_model", ""),
            "sionna_source_wall_time": as_float(row.get("sionna_source_wall_time")),
            "sionna_source_sim_time": as_float(row.get("sionna_source_sim_time")),
            "ns3_sionna_update_time": as_float(row.get("ns3_sionna_update_time")),
            "received": 1 if decision == "received" else 0,
            "dropped": 1 if decision == "dropped" else 0,
            "is_rtp": rtp_seq != "",
        })
    return out


def normalize_frames(video_rx_rows: list[dict[str, Any]], source: Path, frame_gap_s: float) -> list[dict[str, Any]]:
    frames = [row for row in video_rx_rows if row.get("event_type") == "video_frame"]
    out: list[dict[str, Any]] = []
    last_wall_time = math.nan
    for i, row in enumerate(frames):
        wall_dt = as_float(row.get("wall_dt"))
        wall_time = as_float(row.get("wall_time"))
        gap = wall_time - last_wall_time if finite(last_wall_time) and finite(wall_time) else math.nan
        out.append({
            "source_log": str(source),
            "frame_index": i + 1,
            "frame_id": row.get("frame_id", i + 1),
            "wall_time": wall_time,
            "wall_dt": wall_dt,
            "time_s": math.nan,
            "pts_ns": row.get("pts_ns", ""),
            "dts_ns": row.get("dts_ns", ""),
            "duration_ns": row.get("duration_ns", ""),
            "size_bytes": as_int(row.get("size_bytes")),
            "inter_frame_gap_s": gap,
            "gap_event": bool(finite(gap) and gap > frame_gap_s),
        })
        if finite(wall_time):
            last_wall_time = wall_time
    return out


def rtp_rows(rows: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("event_type") == event_type and r.get("rtp_seq") is not None]


def wall_times(rows: list[dict[str, Any]]) -> list[float]:
    return [as_float(r.get("wall_time")) for r in rows if finite(as_float(r.get("wall_time")))]


def filter_wall_interval(rows: list[dict[str, Any]], start_wall: float, end_wall: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        wall_time = as_float(row.get("wall_time"))
        if finite(wall_time) and start_wall <= wall_time <= end_wall:
            out.append(row)
    return out


def common_wall_interval(
    tx_rows: list[dict[str, Any]],
    rx_rows: list[dict[str, Any]],
    payload_packets: list[dict[str, Any]],
) -> tuple[float, float, float, str]:
    tx_times = wall_times(tx_rows)
    rx_times = wall_times(rx_rows)
    ns3_times = [
        as_float(row.get("sionna_source_wall_time"))
        for row in payload_packets
        if row.get("direction") == "near_to_far" and finite(as_float(row.get("sionna_source_wall_time")))
    ]
    intervals: list[tuple[str, float, float]] = []
    if tx_times:
        intervals.append(("video_tx", min(tx_times), max(tx_times)))
    if ns3_times:
        intervals.append(("ns3_payload", min(ns3_times), max(ns3_times)))
    if rx_times:
        intervals.append(("video_rx", min(rx_times), max(rx_times)))
    if not intervals:
        return (0.0, 0.0, 0.0, "no_wall_time")

    start_wall = max(item[1] for item in intervals)
    end_wall = min(item[2] for item in intervals)
    mode = "+".join(item[0] for item in intervals) + "_overlap"
    if end_wall <= start_wall:
        all_times = [time for _name, start, end in intervals for time in (start, end)]
        start_wall = min(all_times)
        end_wall = max(all_times)
        mode = "fallback_union_no_common_overlap"
    return (start_wall, end_wall, start_wall, mode)


def add_relative_time(rows: list[dict[str, Any]], origin_wall: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        wall_time = as_float(copied.get("wall_time"))
        copied["time_s"] = wall_time - origin_wall if finite(wall_time) else math.nan
        out.append(copied)
    return out


def frames_in_interval(
    frames: list[dict[str, Any]],
    start_wall: float,
    end_wall: float,
    origin_wall: float,
    frame_gap_s: float,
) -> list[dict[str, Any]]:
    filtered = sorted(
        [
            dict(frame)
            for frame in frames
            if finite(as_float(frame.get("wall_time"))) and start_wall <= as_float(frame.get("wall_time")) <= end_wall
        ],
        key=lambda frame: as_float(frame.get("wall_time")),
    )
    last_wall_time = math.nan
    for frame in filtered:
        wall_time = as_float(frame.get("wall_time"))
        gap = wall_time - last_wall_time if finite(last_wall_time) and finite(wall_time) else math.nan
        frame["time_s"] = wall_time - origin_wall if finite(wall_time) else math.nan
        frame["inter_frame_gap_s"] = gap
        frame["gap_event"] = bool(finite(gap) and gap > frame_gap_s)
        if finite(wall_time):
            last_wall_time = wall_time
    return filtered


def sequence_set(rows: list[dict[str, Any]]) -> set[int]:
    return {as_int(r.get("rtp_seq"), -1) for r in rows if as_int(r.get("rtp_seq"), -1) >= 0}


def time_series_goodput(
    rows: list[dict[str, Any]],
    start_wall: float,
    end_wall: float,
    origin_wall: float,
    bin_s: float = 1.0,
) -> list[dict[str, Any]]:
    buckets: dict[int, int] = defaultdict(int)
    for row in rows:
        wall_time = as_float(row.get("wall_time"))
        if not finite(wall_time) or wall_time < start_wall or wall_time > end_wall:
            continue
        t = wall_time - origin_wall
        buckets[int(math.floor(t / bin_s))] += as_int(row.get("size_bytes"))
    return [
        {
            "time_s": (idx + 0.5) * bin_s,
            "goodput_mbps": (bytes_count * 8.0) / (bin_s * 1_000_000.0),
        }
        for idx, bytes_count in sorted(buckets.items())
    ]


def interruption_intervals(rows: list[dict[str, Any]], gap_s: float) -> list[dict[str, Any]]:
    times = sorted(as_float(r.get("time_s")) for r in rows if finite(as_float(r.get("time_s"))))
    out: list[dict[str, Any]] = []
    for a, b in zip(times, times[1:]):
        gap = b - a
        if gap > gap_s:
            out.append({"start_s": a, "end_s": b, "gap_s": gap})
    return out


def summarize_bucket(
    bucket: list[dict[str, Any]],
    lo: float,
    hi: float,
    coverage_class: str,
    min_packets: int,
) -> dict[str, Any]:
    attempted = len(bucket)
    dropped = sum(as_int(r["dropped"]) for r in bucket)
    received = sum(as_int(r["received"]) for r in bucket)
    empirical = dropped / attempted if attempted else math.nan
    ci_low, ci_high = wilson_interval(dropped, attempted)
    expected = [as_float(r["expected_p_loss"]) for r in bucket if finite(as_float(r["expected_p_loss"]))]
    expected_mean = float(np.mean(expected)) if expected else math.nan
    rssi_values = [as_float(r["RSSI_used_for_drop_decision"]) for r in bucket]
    center = (lo + hi) / 2.0 if hi > lo else lo
    return {
        "flow_id": "payload",
        "coverage_class": coverage_class,
        "rssi_bin_low": lo,
        "rssi_bin_high": hi,
        "rssi_bin_center": center,
        "median_rssi": float(np.median(rssi_values)) if rssi_values else math.nan,
        "attempted_packets": attempted,
        "received_packets": received,
        "dropped_packets": dropped,
        "empirical_loss_ratio": empirical,
        "expected_loss_ratio_mean": expected_mean,
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "low_confidence": attempted < min_packets,
    }


def aggregate_rssi_bins(
    rows: list[dict[str, Any]],
    bin_width_db: float,
    min_packets: int,
    no_coverage_floor_db: float,
) -> list[dict[str, Any]]:
    valid = [r for r in rows if finite(as_float(r.get("RSSI_used_for_drop_decision")))]
    if not valid:
        return []
    floor_rows = [r for r in valid if as_float(r["RSSI_used_for_drop_decision"]) <= no_coverage_floor_db]
    normal = [r for r in valid if as_float(r["RSSI_used_for_drop_decision"]) > no_coverage_floor_db]
    out: list[dict[str, Any]] = []
    if floor_rows:
        out.append(summarize_bucket(floor_rows, no_coverage_floor_db, no_coverage_floor_db, "no_coverage_floor", min_packets))
    if not normal:
        return out

    min_rssi = math.floor(min(as_float(r["RSSI_used_for_drop_decision"]) for r in normal) / bin_width_db) * bin_width_db
    max_rssi = math.ceil(max(as_float(r["RSSI_used_for_drop_decision"]) for r in normal) / bin_width_db) * bin_width_db
    if min_rssi == max_rssi:
        max_rssi += bin_width_db
    grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for row in normal:
        rssi = as_float(row["RSSI_used_for_drop_decision"])
        lo = math.floor((rssi - min_rssi) / bin_width_db) * bin_width_db + min_rssi
        grouped[lo].append(row)
    for lo, bucket in sorted(grouped.items()):
        out.append(summarize_bucket(bucket, lo, lo + bin_width_db, "normal", min_packets))
    return out


def save_figure(fig: Any, prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf", ".svg"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(prefix.with_suffix(suffix), **kwargs)
    plt.close(fig)


def plot_goodput(
    tx_rows: list[dict[str, Any]],
    rx_rows: list[dict[str, Any]],
    prefix: Path,
    start_wall: float,
    end_wall: float,
    origin_wall: float,
) -> None:
    tx_series = time_series_goodput(tx_rows, start_wall, end_wall, origin_wall)
    rx_series = time_series_goodput(rx_rows, start_wall, end_wall, origin_wall)
    fig, ax = plt.subplots(figsize=(7.16, 2.8))
    ax.set_title("Stage 2.4 payload goodput after ns-3", fontsize=11)
    if tx_series:
        ax.plot([r["time_s"] for r in tx_series], [r["goodput_mbps"] for r in tx_series],
                color="#777777", linestyle="--", linewidth=1.0, label="video tx")
    if rx_series:
        ax.plot([r["time_s"] for r in rx_series], [r["goodput_mbps"] for r in rx_series],
                color="#0B5FA5", linewidth=1.3, label="post-ns3 video rx")
    if not tx_series and not rx_series:
        ax.text(0.5, 0.5, "No video RTP rows", ha="center", va="center", transform=ax.transAxes)
    ax.set_xlabel("Time, s")
    ax.set_ylabel("Goodput, Mbps")
    ax.grid(True, alpha=0.25)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, frameon=False, fontsize=8)
    fig.tight_layout()
    save_figure(fig, prefix)


def plot_interruptions(frames: list[dict[str, Any]], intervals: list[dict[str, Any]], prefix: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.16, 2.8))
    ax.set_title("Stage 2.4 payload video interruption trace", fontsize=11)
    if frames:
        ax.scatter(
            [as_float(f["time_s"]) for f in frames],
            [as_float(f["inter_frame_gap_s"], 0.0) for f in frames],
            s=5,
            alpha=0.45,
            color="#0B5FA5",
            label="decoded frame gap",
        )
    for interval in intervals:
        ax.axvspan(as_float(interval["start_s"]), as_float(interval["end_s"]), color="#B23A48", alpha=0.18)
    if not frames:
        ax.text(0.5, 0.5, "No decoded frame rows", ha="center", va="center", transform=ax.transAxes)
    ax.set_xlabel("Time, s")
    ax.set_ylabel("Frame gap, s")
    ax.grid(True, alpha=0.25)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, frameon=False, fontsize=8)
    fig.tight_layout()
    save_figure(fig, prefix)


def plot_rssi_bins(bins: list[dict[str, Any]], prefix: Path) -> None:
    fig, ax = plt.subplots(figsize=(3.5, 2.65))
    ax.set_title("Payload loss after ns-3 vs RSSI", fontsize=10)
    if not bins:
        ax.text(0.5, 0.5, "No payload RSSI bins", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        normal_bins = [b for b in bins if b.get("coverage_class") == "normal"]
        floor_bins = [b for b in bins if b.get("coverage_class") == "no_coverage_floor"]
        plot_bins = normal_bins or bins
        x_min = min(as_float(b["rssi_bin_low"]) for b in plot_bins) - 2
        x_max = max(as_float(b["rssi_bin_high"]) for b in plot_bins) + 2
        x_ref = np.linspace(x_min, x_max, 300)
        ax.plot(x_ref, repository_loss_reference(x_ref), "--", color="#4A4A4A", linewidth=1.1, label="Repository mapping")
        for group, marker, label, alpha in (
            ([b for b in normal_bins if not b["low_confidence"]], "o", "payload", 0.95),
            ([b for b in normal_bins if b["low_confidence"]], "x", "payload low n", 0.65),
        ):
            if not group:
                continue
            x = np.array([as_float(b["rssi_bin_center"]) for b in group])
            y = np.array([as_float(b["empirical_loss_ratio"]) for b in group])
            lo = np.array([as_float(b["ci95_low"]) for b in group])
            hi = np.array([as_float(b["ci95_high"]) for b in group])
            yerr = np.vstack([np.maximum(0.0, y - lo), np.maximum(0.0, hi - y)])
            sizes = np.array([max(24.0, min(150.0, math.sqrt(as_int(b["attempted_packets"])) * 4.0)) for b in group])
            ax.errorbar(x, y, yerr=yerr, fmt="none", ecolor="#B23A48", alpha=alpha, capsize=2)
            ax.scatter(x, y, s=sizes, marker=marker, color="#B23A48", alpha=alpha, label=label)
        for floor_bin in floor_bins:
            ax.scatter(
                [x_min],
                [as_float(floor_bin["empirical_loss_ratio"])],
                marker="s",
                s=55,
                color="#111111",
                alpha=0.8,
                label=f"no-coverage floor, n={as_int(floor_bin['attempted_packets'])}",
            )
            ax.annotate("floor", (x_min, as_float(floor_bin["empirical_loss_ratio"])),
                        textcoords="offset points", xytext=(3, 6), fontsize=6)
        ax.set_xlabel("RSSI, dBm")
        ax.set_ylabel("Packet loss ratio")
        ax.set_ylim(-0.03, 1.03)
        ax.grid(True, alpha=0.25)
        ax.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    save_figure(fig, prefix)


def runtime_value(runtime_text: str, key: str) -> str:
    prefix = f"- {key}:"
    for line in runtime_text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return "unknown"


def infer_bypass_disabled(runtime_text: str, stack_text: str) -> tuple[bool, str]:
    runtime_value_text = runtime_value(runtime_text, "Bypass disabled").lower()
    if runtime_value_text == "true":
        return True, "payload_path_runtime.md"
    if runtime_value_text == "false":
        return False, "payload_path_runtime.md"

    stack_lower = stack_text.lower()
    if "payload bypass: disabled" in stack_lower:
        return True, "auto_demo_stack.log"
    if "payload bypass: enabled" in stack_lower:
        return False, "auto_demo_stack.log"
    return False, "missing_runtime_evidence"


def infer_require_ns3_payload(runtime_text: str, stack_text: str) -> tuple[bool, str]:
    runtime_value_text = runtime_value(runtime_text, "Require ns-3 payload").lower()
    if runtime_value_text in {"1", "true", "yes"}:
        return True, "payload_path_runtime.md"
    if runtime_value_text in {"0", "false", "no"}:
        return False, "payload_path_runtime.md"

    stack_lower = stack_text.lower()
    if "require ns-3 payload: 1" in stack_lower or "require ns-3 payload: true" in stack_lower:
        return True, "auto_demo_stack.log"
    if "require ns-3 payload: 0" in stack_lower or "require ns-3 payload: false" in stack_lower:
        return False, "auto_demo_stack.log"
    return False, "missing_runtime_evidence"


def write_reports(
    audit_path: Path,
    summary_path: Path,
    args: argparse.Namespace,
    payload_packets: list[dict[str, Any]],
    bins: list[dict[str, Any]],
    tx_rows: list[dict[str, Any]],
    rx_rows: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    interruptions: list[dict[str, Any]],
    time_sync: tuple[float, float, float, str],
) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_text = args.runtime_md.read_text(encoding="utf-8") if args.runtime_md.exists() else ""
    stack_text = args.stack_log.read_text(encoding="utf-8") if args.stack_log.exists() else ""
    bypass_disabled, bypass_evidence = infer_bypass_disabled(runtime_text, stack_text)
    require_ns3_payload, require_evidence = infer_require_ns3_payload(runtime_text, stack_text)
    start_wall, end_wall, origin_wall, time_sync_mode = time_sync
    direction_counts = Counter(str(r.get("direction", "")) for r in payload_packets)
    primary_packets = [r for r in payload_packets if r.get("direction") == "near_to_far"] or payload_packets
    entering = len(primary_packets)
    dropped = sum(as_int(r["dropped"]) for r in primary_packets)
    leaving = sum(as_int(r["received"]) for r in primary_packets)
    ns3_rtp = sum(1 for r in primary_packets if r.get("is_rtp"))
    tx_seq = sequence_set(tx_rows)
    rx_seq = sequence_set(rx_rows)
    tx_seq_not_observed = len(tx_seq - rx_seq) if tx_seq else 0
    rx_bytes = sum(as_int(r.get("size_bytes")) for r in rx_rows)
    duration = max(0.0, end_wall - start_wall)
    avg_goodput = (rx_bytes * 8.0 / max(duration, 1e-9)) / 1_000_000.0 if rx_bytes else 0.0
    enough_packets = entering >= args.min_payload_packets
    time_overlap_ok = not time_sync_mode.startswith("fallback") and duration > 0.0
    valid = (
        bypass_disabled
        and require_ns3_payload
        and enough_packets
        and len(rx_rows) > 0
        and len(frames) > 0
        and time_overlap_ok
    )

    audit_lines = [
        "# Stage 2.4 Payload Path Audit",
        "",
        "This report only treats payload metrics as valid when the video stream is received after the ns-3 payload channel.",
        "",
        "## Path",
        "",
        "Gazebo/video source -> UAV network namespace eth1 -> tap-pload-near -> ns-3 payload channel -> tap-pload-far -> GCS payload namespace -> RTP/H.264 receiver -> decoded frames/MJPEG Web GCS.",
        "",
        "## Evidence",
        "",
        f"- Commit hash: `{git_value(['rev-parse', 'HEAD'])}`",
        f"- Worktree state: `{'dirty' if git_value(['status', '--short'], default='') else 'clean'}`",
        f"- Run dir: `{args.run_dir}`",
        f"- Raw packet CSV: `{args.packet_csv}`",
        f"- video_tx JSONL: `{args.video_tx}`",
        f"- video_rx JSONL: `{args.video_rx}`",
        f"- Runtime check: `{args.runtime_md}`",
        f"- Stack log: `{args.stack_log}`",
        f"- Bypass disabled: `{str(bypass_disabled).lower()}`",
        f"- Bypass evidence source: `{bypass_evidence}`",
        f"- Require ns-3 payload: `{str(require_ns3_payload).lower()}`",
        f"- Require evidence source: `{require_evidence}`",
        f"- Common wall-time interval: `{start_wall:.6f}..{end_wall:.6f}`",
        f"- Common analysis duration: `{duration:.3f}` s",
        f"- Time-sync mode: `{time_sync_mode}`",
        f"- Time overlap valid: `{str(time_overlap_ok).lower()}`",
        f"- Payload packet directions: `{dict(direction_counts)}`",
        f"- Payload packets entering ns-3: `{entering}`",
        f"- Payload packets dropped by ns-3: `{dropped}`",
        f"- Payload packets leaving ns-3: `{leaving}`",
        f"- Payload ns-3 rows with explicit RTP sequence: `{ns3_rtp}`",
        f"- RTP/video packets transmitted by sender: `{len(tx_rows)}`",
        f"- RTP/video packets received after ns-3: `{len(rx_rows)}`",
        f"- RTP tx sequences not observed at post-ns3 receiver: `{tx_seq_not_observed}`",
        f"- Decoded frames: `{len(frames)}`",
        f"- Frame gaps over {args.frame_gap_s:.3f}s: `{sum(1 for f in frames if f.get('gap_event'))}`",
        f"- Interruption intervals over {args.interruption_gap_s:.3f}s: `{len(interruptions)}`",
        f"- Average post-ns3 goodput Mbps: `{avg_goodput:.6f}`",
        f"- Valid payload route audit: `{str(valid).lower()}`",
        "",
        "## Validity Rule",
        "",
        f"Payload route audit is valid only when bypass is disabled, BAS_REQUIRE_NS3_PAYLOAD=1, decoded post-ns3 video exists, and payload packets through ns-3 are >= {args.min_payload_packets}.",
        "",
    ]
    if not valid:
        audit_lines.extend([
            "## Blocking / Limiting Finding",
            "",
            "Payload metrics must not be used as validated Stage 2.4 payload evidence for this run.",
            "",
        ])
    audit_lines.extend([
        "## Artifacts",
        "",
        "- `data/processed/stage24_payload_packets.csv`",
        "- `data/processed/stage24_payload_frames.csv`",
        "- `figures/stage24_payload_goodput_trace.png` / `.pdf` / `.svg`",
        "- `figures/stage24_payload_interruption_trace.png` / `.pdf` / `.svg`",
        "- `figures/stage24_payload_rssi_loss_bins.png` / `.pdf` / `.svg`",
        "",
    ])
    audit_path.write_text("\n".join(audit_lines), encoding="utf-8")

    bins_good = sum(1 for b in bins if not b["low_confidence"])
    summary_lines = [
        "# Stage 2.4 Payload Metrics Summary",
        "",
        "Payload metrics are computed only from post-ns3 receiver logs and ns-3 payload packet audit rows.",
        "",
        f"- valid_payload_route_audit={str(valid).lower()}",
        f"- payload_bypass={str(not bypass_disabled).lower()}",
        f"- payload_bypass_disabled={str(bypass_disabled).lower()}",
        f"- Bypass evidence source: {bypass_evidence}",
        f"- require_ns3_payload={str(require_ns3_payload).lower()}",
        f"- Require evidence source: {require_evidence}",
        f"- payload_packets_entering_ns3={entering}",
        f"- payload_packets_dropped_by_ns3={dropped}",
        f"- payload_packets_leaving_ns3={leaving}",
        f"- RTP received after ns-3: {len(rx_rows)}",
        f"- decoded_frames={len(frames)}",
        f"- interruptions={len(interruptions)}",
        f"- Average goodput Mbps over common interval: {avg_goodput:.6f}",
        f"- Common time interval s: 0.000..{duration:.3f} ({time_sync_mode})",
        f"- time_overlap_valid={str(time_overlap_ok).lower()}",
        f"- RSSI bins: {len(bins)} total, {bins_good} with enough packets",
    ]
    if entering < args.min_payload_packets:
        summary_lines.append(f"- Payload route audit not supported for article claims: ns-3 payload packets {entering} < {args.min_payload_packets}.")
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--packet-csv", type=Path, default=None)
    parser.add_argument("--video-tx", type=Path, default=None)
    parser.add_argument("--video-rx", type=Path, default=None)
    parser.add_argument("--runtime-md", type=Path, default=None)
    parser.add_argument("--stack-log", type=Path, default=None)
    parser.add_argument("--packets-out", type=Path, default=Path("data/processed/stage24_payload_packets.csv"))
    parser.add_argument("--frames-out", type=Path, default=Path("data/processed/stage24_payload_frames.csv"))
    parser.add_argument("--goodput-prefix", type=Path, default=Path("figures/stage24_payload_goodput_trace"))
    parser.add_argument("--interruption-prefix", type=Path, default=Path("figures/stage24_payload_interruption_trace"))
    parser.add_argument("--bins-prefix", type=Path, default=Path("figures/stage24_payload_rssi_loss_bins"))
    parser.add_argument("--audit-out", type=Path, default=Path("reports/stage24_payload_path_audit.md"))
    parser.add_argument("--summary-out", type=Path, default=Path("reports/stage24_payload_metrics_summary.md"))
    parser.add_argument("--min-payload-packets", type=int, default=10000)
    parser.add_argument("--bin-width-db", type=float, default=2.0)
    parser.add_argument("--min-bin-packets", type=int, default=300)
    parser.add_argument("--no-coverage-floor-db", type=float, default=-120.0)
    parser.add_argument("--interruption-gap-s", type=float, default=1.0)
    parser.add_argument("--frame-gap-s", type=float, default=0.5)
    args = parser.parse_args()

    args.run_dir = args.run_dir.resolve()
    args.packet_csv = args.packet_csv or (args.run_dir / "stage24_route_rssi_packets_raw.csv")
    args.video_tx = args.video_tx or (args.run_dir / "video_tx.jsonl")
    args.video_rx = args.video_rx or (args.run_dir / "video_rx.jsonl")
    args.runtime_md = args.runtime_md or (args.run_dir / "payload_path_runtime.md")
    args.stack_log = args.stack_log or (args.run_dir / "auto_demo_stack.log")

    raw_packets = read_csv(args.packet_csv)
    payload_packets = normalize_payload_packets(raw_packets, args.packet_csv)
    video_tx_rows = read_jsonl(args.video_tx)
    video_rx_rows = read_jsonl(args.video_rx)
    tx_rows_all = rtp_rows(video_tx_rows, "video_tx")
    rx_rows_all = rtp_rows(video_rx_rows, "video_rx")
    frames_all = normalize_frames(video_rx_rows, args.video_rx, args.frame_gap_s)
    time_sync = common_wall_interval(tx_rows_all, rx_rows_all, payload_packets)
    start_wall, end_wall, origin_wall, _time_sync_mode = time_sync
    tx_rows = filter_wall_interval(tx_rows_all, start_wall, end_wall)
    rx_rows = filter_wall_interval(rx_rows_all, start_wall, end_wall)
    frames = frames_in_interval(frames_all, start_wall, end_wall, origin_wall, args.frame_gap_s)
    interruptions = interruption_intervals(frames, args.interruption_gap_s)
    bins = aggregate_rssi_bins(payload_packets, args.bin_width_db, args.min_bin_packets, args.no_coverage_floor_db)

    write_csv(args.packets_out, payload_packets, [
        "source_log", "row_index", "run_id", "packet_uid", "timestamp",
        "flow_id", "channel_type", "tx_node", "rx_node", "direction",
        "packet_size", "rtp_sequence_number", "rtp_timestamp_90khz",
        "frame_id", "rtp_parse_offset", "RSSI_from_Sionna",
        "RSSI_used_for_drop_decision", "expected_p_loss", "random_draw",
        "final_decision", "drop_reason", "seed", "sionna_sample_valid",
        "sionna_channel_model", "sionna_source_wall_time",
        "sionna_source_sim_time", "ns3_sionna_update_time",
        "received", "dropped", "is_rtp",
    ])
    write_csv(args.frames_out, frames, [
        "source_log", "frame_index", "frame_id", "wall_time", "wall_dt", "time_s",
        "pts_ns", "dts_ns", "duration_ns", "size_bytes",
        "inter_frame_gap_s", "gap_event",
    ])

    plot_goodput(tx_rows, rx_rows, args.goodput_prefix, start_wall, end_wall, origin_wall)
    plot_interruptions(frames, interruptions, args.interruption_prefix)
    plot_rssi_bins(bins, args.bins_prefix)
    write_reports(
        args.audit_out,
        args.summary_out,
        args,
        payload_packets,
        bins,
        tx_rows,
        rx_rows,
        frames,
        interruptions,
        time_sync,
    )

    print(f"payload ns-3 rows: {len(payload_packets)}")
    print(f"video_tx RTP rows in common interval: {len(tx_rows)}")
    print(f"video_rx RTP rows in common interval: {len(rx_rows)}")
    print(f"decoded frames: {len(frames)}")
    print(f"interruptions: {len(interruptions)}")
    print(f"wrote: {args.packets_out}")
    print(f"wrote: {args.frames_out}")
    print(f"wrote: {args.audit_out}")
    print(f"wrote: {args.summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
