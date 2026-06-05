#!/usr/bin/env python3
"""Build a Stage 2.4 RF-stress waypoint route from Sionna RSSI pre-scan data.

The selected RSSI targets are used only to choose route coordinates. The route
run itself must still use live Sionna RT RSSI in ns-3 packet decisions.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib  # type: ignore

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # type: ignore
import numpy as np  # type: ignore


DEFAULT_TARGETS = [-95, -90, -85, -82, -80, -78, -76, -74, -72, -70, -68, -65, -60]


@dataclass(frozen=True)
class ScanPoint:
    index: int
    north_m: float
    east_m: float
    altitude_m: float
    rssi_db: float
    path_loss_db: float
    source: str


@dataclass(frozen=True)
class SelectedPoint:
    target_rssi_db: float
    point: ScanPoint
    error_db: float
    status: str
    visit_order: int = -1


def parse_targets(value: str) -> list[float]:
    if not value.strip():
        return DEFAULT_TARGETS
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def parse_xyz(value: str) -> tuple[float, float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected x,y,z")
    return (parts[0], parts[1], parts[2])


def finite(value: float) -> bool:
    return math.isfinite(value)


def distance(a: ScanPoint, b: ScanPoint) -> float:
    return math.hypot(a.north_m - b.north_m, a.east_m - b.east_m)


def safe_selection_points(points: list[ScanPoint], args: argparse.Namespace) -> list[ScanPoint]:
    no_coverage = [p for p in points if p.rssi_db <= args.no_coverage_floor_db]
    safe: list[ScanPoint] = []
    for point in points:
        if point.rssi_db <= args.no_coverage_floor_db:
            continue
        if no_coverage:
            nearest_floor = min(distance(point, floor) for floor in no_coverage)
            if nearest_floor < args.floor_avoid_radius_m:
                continue
        safe.append(point)
    return safe


def load_cached_points(args: argparse.Namespace) -> list[ScanPoint]:
    data = np.load(args.radio_map)
    rss = np.asarray(data["rss_db"], dtype=float)
    path_loss = np.asarray(data.get("path_loss_db", -rss), dtype=float)
    cx, cy, _cz = np.asarray(data["map_center"], dtype=float)
    sx, sy = np.asarray(data["map_size"], dtype=float)
    cell_x, cell_y = np.asarray(data["cell_size"], dtype=float)

    xs = np.linspace(cx - sx / 2.0 + cell_x / 2.0, cx + sx / 2.0 - cell_x / 2.0, rss.shape[1])
    ys = np.linspace(cy - sy / 2.0 + cell_y / 2.0, cy + sy / 2.0 - cell_y / 2.0, rss.shape[0])

    points: list[ScanPoint] = []
    index = 0
    for iy, east_m in enumerate(ys):
        for ix, north_m in enumerate(xs):
            if not (args.north_min <= north_m <= args.north_max):
                continue
            if not (args.east_min <= east_m <= args.east_max):
                continue
            rssi_db = float(rss[iy, ix])
            if not finite(rssi_db) or rssi_db < args.min_valid_rssi_db or rssi_db > args.max_valid_rssi_db:
                continue
            points.append(
                ScanPoint(
                    index=index,
                    north_m=float(north_m),
                    east_m=float(east_m),
                    altitude_m=float(args.altitude_m),
                    rssi_db=rssi_db,
                    path_loss_db=float(path_loss[iy, ix]),
                    source="cached_radio_map",
                )
            )
            index += 1
    return points


def grid_points(args: argparse.Namespace) -> list[tuple[float, float]]:
    def axis_values(min_value: float, max_value: float, step: float) -> list[float]:
        values: list[float] = []
        current = min_value
        while current <= max_value + 1e-9:
            values.append(float(current))
            current += step
        return values

    north_values = axis_values(args.north_min, args.north_max, args.grid_step_m)
    east_values = axis_values(args.east_min, args.east_max, args.grid_step_m)
    return [(float(n), float(e)) for n in north_values for e in east_values]


def load_live_rt_points(args: argparse.Namespace) -> list[ScanPoint]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sionna_channel_publisher import LiveRTChannel  # pylint: disable=import-error

    channel = LiveRTChannel(
        scene_path=Path(args.rt_scene),
        tx_position=parse_xyz(args.rt_tx),
        max_depth=args.rt_max_depth,
        mitsuba_variant=args.mitsuba_variant or None,
        require_gpu=args.require_gpu,
    )

    points: list[ScanPoint] = []
    for index, (north_m, east_m) in enumerate(grid_points(args)):
        rssi_db, path_loss_db = channel.lookup(north_m, east_m, args.altitude_m)
        if finite(rssi_db) and args.min_valid_rssi_db <= rssi_db <= args.max_valid_rssi_db:
            points.append(
                ScanPoint(
                    index=index,
                    north_m=north_m,
                    east_m=east_m,
                    altitude_m=float(args.altitude_m),
                    rssi_db=float(rssi_db),
                    path_loss_db=float(path_loss_db),
                    source="live_sionna_rt_prescan",
                )
            )
        print(
            f"[prescan] {index + 1:04d}: north={north_m:.1f} east={east_m:.1f} "
            f"rss={rssi_db:.3f} dBm",
            flush=True,
        )
    return points


def choose_targets(
    points: list[ScanPoint],
    targets: list[float],
    max_target_error_db: float,
    min_separation_m: float,
    args: argparse.Namespace,
) -> list[SelectedPoint]:
    selected: list[SelectedPoint] = []
    candidate_points = safe_selection_points(points, args)
    if not candidate_points:
        candidate_points = [p for p in points if p.rssi_db > args.no_coverage_floor_db]
    if not candidate_points:
        return selected

    for target in targets:
        ranked = sorted(candidate_points, key=lambda p: (abs(p.rssi_db - target), p.index))
        separated = [
            p for p in ranked
            if all(distance(p, prev.point) >= min_separation_m for prev in selected)
        ]
        point = separated[0] if separated else ranked[0]
        error_db = abs(point.rssi_db - target)
        status = "ok" if error_db <= max_target_error_db else "nearest_only"
        selected.append(SelectedPoint(target, point, error_db, status))
    return selected


def order_nearest_neighbor(selected: list[SelectedPoint]) -> list[SelectedPoint]:
    remaining = list(selected)
    ordered: list[SelectedPoint] = []
    current_north = 0.0
    current_east = 0.0
    while remaining:
        idx, _item = min(
            enumerate(remaining),
            key=lambda pair: math.hypot(pair[1].point.north_m - current_north, pair[1].point.east_m - current_east),
        )
        item = remaining.pop(idx)
        ordered.append(item)
        current_north = item.point.north_m
        current_east = item.point.east_m
    return [
        SelectedPoint(item.target_rssi_db, item.point, item.error_db, item.status, i)
        for i, item in enumerate(ordered)
    ]


def order_route(selected: list[SelectedPoint], mode: str) -> list[SelectedPoint]:
    if mode == "target_desc":
        ordered = sorted(selected, key=lambda item: item.target_rssi_db, reverse=True)
    elif mode == "target_asc":
        ordered = sorted(selected, key=lambda item: item.target_rssi_db)
    elif mode == "east":
        ordered = sorted(selected, key=lambda item: item.point.east_m)
    else:
        return order_nearest_neighbor(selected)
    return [
        SelectedPoint(item.target_rssi_db, item.point, item.error_db, item.status, i)
        for i, item in enumerate(ordered)
    ]


def trajectory_steps(selected: list[SelectedPoint], args: argparse.Namespace) -> list[dict[str, Any]]:
    route = order_route(selected, args.route_order)
    if args.there_and_back and len(route) > 1:
        route = route + [
            SelectedPoint(item.target_rssi_db, item.point, item.error_db, item.status, len(route) + i)
            for i, item in enumerate(reversed(route[:-1]))
        ]

    steps: list[dict[str, Any]] = [
        {"label": "guided", "api": "command", "payload": {"action": "guided"}, "wait_s": 2.0},
        {
            "label": "arm",
            "api": "command",
            "payload": {"action": "arm", "altitude": args.altitude_m},
            "wait_s": 3.0,
            "screenshot": "01_armed",
        },
        {
            "label": f"takeoff_{args.altitude_m:.0f}m",
            "api": "command",
            "payload": {"action": "takeoff", "altitude": args.altitude_m},
            "wait_s": max(8.0, args.altitude_m),
            "screenshot": "02_takeoff",
        },
    ]
    for idx, item in enumerate(route):
        point = item.point
        direction = "out" if idx < len(selected) else "back"
        label = f"rf_{direction}_{idx + 1:02d}_target_{item.target_rssi_db:.0f}dbm"
        steps.append(
            {
                "label": label,
                "api": "goto",
                "payload": {
                    "north": round(point.north_m, 1),
                    "east": round(point.east_m, 1),
                    "altitude": round(point.altitude_m, 1),
                },
                "wait_s": args.hold_s,
                "reach_check": True,
                "reach_north": round(point.north_m, 1),
                "reach_east": round(point.east_m, 1),
                "reach_alt": round(point.altitude_m, 1),
                "reach_tol_m": args.reach_tol_m,
                "reach_timeout_s": args.reach_timeout_s,
                "screenshot": f"{idx + 3:02d}_{label}" if idx in (0, len(route) // 2, len(route) - 1) else None,
                "prescan_target_rssi_db": item.target_rssi_db,
                "prescan_rssi_db": round(point.rssi_db, 3),
                "prescan_selection_status": item.status,
            }
        )
    steps.extend(
        [
            {
                "label": "return_home",
                "api": "goto",
                "payload": {"north": 0.0, "east": 0.0, "altitude": round(args.altitude_m, 1)},
                "wait_s": 3.0,
                "reach_check": True,
                "reach_north": 0.0,
                "reach_east": 0.0,
                "reach_tol_m": args.reach_tol_m,
                "reach_timeout_s": args.reach_timeout_s,
                "screenshot": "route_home",
            },
            {"label": "land", "api": "command", "payload": {"action": "land"}, "wait_s": 10.0},
            {"label": "disarm_safety", "api": "command", "payload": {"action": "disarm"}, "wait_s": 2.0},
        ]
    )
    return steps


def write_points_csv(path: Path, points: list[ScanPoint], selected: list[SelectedPoint], route_order: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    selected_by_index = {item.point.index: item for item in order_route(selected, route_order)}
    fields = [
        "scan_index", "north_m", "east_m", "altitude_m", "rssi_db", "path_loss_db",
        "source", "selected", "target_rssi_db", "selection_error_db",
        "selection_status", "visit_order",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for point in points:
            item = selected_by_index.get(point.index)
            writer.writerow(
                {
                    "scan_index": point.index,
                    "north_m": f"{point.north_m:.3f}",
                    "east_m": f"{point.east_m:.3f}",
                    "altitude_m": f"{point.altitude_m:.3f}",
                    "rssi_db": f"{point.rssi_db:.6f}",
                    "path_loss_db": f"{point.path_loss_db:.6f}",
                    "source": point.source,
                    "selected": "true" if item else "false",
                    "target_rssi_db": "" if item is None else f"{item.target_rssi_db:.3f}",
                    "selection_error_db": "" if item is None else f"{item.error_db:.6f}",
                    "selection_status": "" if item is None else item.status,
                    "visit_order": "" if item is None else item.visit_order,
                }
            )


def plot_prescan(points: list[ScanPoint], selected: list[SelectedPoint], out_prefix: Path, route_order: str) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    ax.set_title("Stage 2.4 RF-stress Sionna pre-scan", fontsize=10)
    if not points:
        ax.text(0.5, 0.5, "No valid Sionna RSSI pre-scan points", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        sc = ax.scatter(
            [p.east_m for p in points],
            [p.north_m for p in points],
            c=[p.rssi_db for p in points],
            s=18,
            cmap="viridis",
            vmin=-100,
            vmax=-55,
            alpha=0.85,
            edgecolors="none",
        )
        ordered = order_route(selected, route_order)
        if ordered:
            xs = [item.point.east_m for item in ordered]
            ys = [item.point.north_m for item in ordered]
            ax.plot(xs, ys, color="#111111", linewidth=1.0, alpha=0.75)
            ax.scatter(xs, ys, marker="x", s=42, color="#C23B22", label="selected waypoints")
            for item in ordered:
                ax.annotate(
                    f"{item.target_rssi_db:.0f}->{item.point.rssi_db:.1f}",
                    (item.point.east_m, item.point.north_m),
                    textcoords="offset points",
                    xytext=(4, 4),
                    fontsize=6.2,
                    color="#111111",
                )
        ax.scatter([0.0], [0.0], marker="o", s=36, color="#111111", label="home")
        ax.set_xlabel("East, m")
        ax.set_ylabel("North, m")
        ax.grid(True, alpha=0.25)
        ax.set_aspect("equal", adjustable="box")
        ax.legend(frameon=False, fontsize=7)
        fig.colorbar(sc, ax=ax, label="RSSI, dBm")
    fig.tight_layout()
    for suffix in (".png", ".pdf", ".svg"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(out_prefix.with_suffix(suffix), **kwargs)
    plt.close(fig)


def write_report(path: Path, points: list[ScanPoint], selected: list[SelectedPoint], args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rssi_values = [p.rssi_db for p in points if finite(p.rssi_db)]
    missed = [item for item in selected if item.status != "ok"]
    cached_tx = ""
    if not args.live_rt and args.radio_map.exists():
        try:
            cached_data = np.load(args.radio_map)
            cached_tx_values = np.asarray(cached_data.get("tx_position", []), dtype=float)
            if cached_tx_values.size >= 3:
                cached_tx = ",".join(f"{value:g}" for value in cached_tx_values[:3])
        except Exception:
            cached_tx = ""
    lines = [
        "# Stage 2.4 RF-Stress Route Pre-Scan",
        "",
        "This pre-scan selects route coordinates only. It does not create synthetic RSSI targets for the route-run packet CSV.",
        "",
        "## Inputs",
        "",
        f"- Mode: `{('live_sionna_rt' if args.live_rt else 'cached_radio_map')}`",
        f"- Radio map: `{args.radio_map}`",
        f"- RT scene: `{args.rt_scene}`",
        f"- Route/live RT TX/GCS position: `{args.rt_tx}`",
        f"- Cached map TX position: `{cached_tx or 'not used'}`",
        f"- Altitude: `{args.altitude_m}` m",
        f"- Bounds north/east: `{args.north_min}..{args.north_max}` / `{args.east_min}..{args.east_max}` m",
        f"- Grid step: `{args.grid_step_m}` m",
        f"- No-coverage floor excluded from waypoint selection: `{args.no_coverage_floor_db}` dBm",
        f"- No-coverage avoid radius: `{args.floor_avoid_radius_m}` m",
        f"- Route order: `{args.route_order}`",
        f"- Targets for waypoint selection: `{', '.join(f'{t:.0f}' for t in parse_targets(args.targets))}` dBm",
        "",
        "## Pre-Scan Coverage",
        "",
        f"- Valid scan points: `{len(points)}`",
        f"- RSSI range in pre-scan: `{min(rssi_values):.3f}..{max(rssi_values):.3f}` dBm" if rssi_values else "- RSSI range in pre-scan: `none`",
        "",
        "## Selected Waypoints",
        "",
    ]
    if not selected:
        lines.append("- No waypoints were selected.")
    for item in order_route(selected, args.route_order):
        point = item.point
        lines.append(
            f"- target {item.target_rssi_db:.0f} dBm -> north={point.north_m:.1f} m, "
            f"east={point.east_m:.1f} m, pre-scan RSSI={point.rssi_db:.3f} dBm, "
            f"error={item.error_db:.3f} dB, status={item.status}"
        )
    if missed:
        lines.extend(
            [
                "",
                "## Coverage Gap",
                "",
                "At least one target could not be matched within the configured error threshold. This is a pre-run warning, not a route-run result.",
            ]
        )
        for item in missed:
            lines.append(
                f"- target {item.target_rssi_db:.0f} dBm nearest pre-scan RSSI "
                f"{item.point.rssi_db:.3f} dBm (error {item.error_db:.3f} dB)"
            )
    lines.extend(
        [
            "",
            "## Route-Run Rule",
            "",
            "The generated trajectory JSON may include `prescan_target_rssi_db` metadata for traceability, but the Stage 2.4 route-run must use live Sionna RT RSSI from the current UAV/GCS coordinates. Acceptance must be decided from `RSSI_used_for_drop_decision` in the ns-3 packet audit CSV.",
            "",
            "## Artifacts",
            "",
            f"- Pre-scan points CSV: `{args.points_out}`",
            f"- Trajectory JSON: `{args.trajectory_out}`",
            f"- Pre-scan map: `{args.map_prefix}.png/.pdf/.svg`",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--radio-map", type=Path, default=Path("radio_maps/iris_runway.npz"))
    parser.add_argument("--live-rt", action="store_true")
    parser.add_argument("--rt-scene", default="scene/iris_runway.xml")
    parser.add_argument("--rt-tx", default="0,-400,1.5")
    parser.add_argument("--rt-max-depth", type=int, default=2)
    parser.add_argument("--mitsuba-variant", default="")
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument("--targets", default=",".join(str(v) for v in DEFAULT_TARGETS))
    parser.add_argument("--altitude-m", type=float, default=10.0)
    parser.add_argument("--north-min", type=float, default=-220.0)
    parser.add_argument("--north-max", type=float, default=230.0)
    parser.add_argument("--east-min", type=float, default=-220.0)
    parser.add_argument("--east-max", type=float, default=230.0)
    parser.add_argument("--grid-step-m", type=float, default=30.0)
    parser.add_argument("--min-valid-rssi-db", type=float, default=-150.0)
    parser.add_argument("--max-valid-rssi-db", type=float, default=0.0)
    parser.add_argument("--no-coverage-floor-db", type=float, default=-120.0)
    parser.add_argument("--floor-avoid-radius-m", type=float, default=45.0)
    parser.add_argument("--max-target-error-db", type=float, default=3.0)
    parser.add_argument("--min-separation-m", type=float, default=20.0)
    parser.add_argument("--route-order", choices=("nearest", "target_desc", "target_asc", "east"), default="nearest")
    parser.add_argument("--hold-s", type=float, default=12.0)
    parser.add_argument("--reach-tol-m", type=float, default=7.0)
    parser.add_argument("--reach-timeout-s", type=float, default=70.0)
    parser.add_argument("--there-and-back", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--points-out", type=Path, default=Path("data/processed/stage24_rf_stress_prescan_points.csv"))
    parser.add_argument("--trajectory-out", type=Path, default=Path("data/processed/stage24_rf_stress_waypoints.json"))
    parser.add_argument("--map-prefix", type=Path, default=Path("figures/stage24_rf_stress_prescan_map"))
    parser.add_argument("--report-out", type=Path, default=Path("reports/stage24_rf_stress_prescan.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = parse_targets(args.targets)
    if args.live_rt:
        points = load_live_rt_points(args)
    else:
        points = load_cached_points(args)
    selected = choose_targets(points, targets, args.max_target_error_db, args.min_separation_m, args)
    trajectory = trajectory_steps(selected, args)

    write_points_csv(args.points_out, points, selected, args.route_order)
    args.trajectory_out.parent.mkdir(parents=True, exist_ok=True)
    args.trajectory_out.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    plot_prescan(points, selected, args.map_prefix, args.route_order)
    write_report(args.report_out, points, selected, args)

    print(f"pre-scan points: {len(points)}")
    if points:
        rssi_values = [p.rssi_db for p in points]
        print(f"pre-scan RSSI range: {min(rssi_values):.3f}..{max(rssi_values):.3f} dBm")
    print(f"selected waypoints: {len(selected)}")
    print(f"wrote: {args.points_out}")
    print(f"wrote: {args.trajectory_out}")
    print(f"wrote: {args.map_prefix}.png/.pdf/.svg")
    print(f"wrote: {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
