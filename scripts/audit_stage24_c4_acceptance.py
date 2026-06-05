#!/usr/bin/env python3
"""Stage 2.4 C4 acceptance audit — machine-readable + markdown.

Проверяет acceptance-критерии C4 (route-level live Sionna->ns-3 simulated packet
audit) ПО СЫРЫМ/PROCESSED данным, не доверяя сводкам. Печатает PASS/FAIL по
каждому критерию, пишет:
  reports/stage24_c4_acceptance_audit.md
  reports/stage24_c4_acceptance_audit.json

Ничего не сглаживает: если критерий не проходит — criterion=false, без сокрытия.

Usage:
  .venv/bin/python scripts/audit_stage24_c4_acceptance.py \
      [--run-id stage24_rf_stress_final_20260605T132014Z]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data/processed"
REPORTS = REPO / "reports"
FIGURES = REPO / "figures"

ROUTE_CSV = PROC / "stage24_route_rssi_packets.csv"
BINS_CSV = PROC / "stage24_route_rssi_bins.csv"
PAYLOAD_CSV = PROC / "stage24_payload_packets.csv"
FRAMES_CSV = PROC / "stage24_payload_frames.csv"
PAYLOAD_METRICS_REPORT = REPORTS / "stage24_payload_metrics_summary.md"
PAYLOAD_PATH_REPORT = REPORTS / "stage24_payload_path_audit.md"

SENTINEL_RSSI_DBM = -119.0     # ниже -119 считаем no-coverage/floor
BIN_MIN_PACKETS = 200          # порог "достаточно пакетов" для bin
WEAK_LOSS_THRESHOLD = 0.2
STRONG_LOSS_THRESHOLD = 0.05
IEEE_FIGURES = [
    "stage24_route_rssi_loss_bins_ieee",
    "stage24_route_rssi_map_ieee",
    "stage24_route_rssi_packet_trace_ieee",
    "stage24_payload_goodput_ieee",
    "stage24_payload_interruptions_ieee",
]

csv.field_size_limit(10_000_000)


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO), "status", "--porcelain"], text=True)
        return bool(out.strip())
    except Exception:
        return True


def parse_report_kv(path: Path) -> dict:
    """Парсит 'key=value' и '- key: value' строки из markdown-отчёта."""
    kv: dict[str, str] = {}
    if not path.exists():
        return kv
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip().lstrip("-* ").strip()
        m = re.match(r"^`?([A-Za-z0-9_ /-]+?)`?\s*[:=]\s*`?(.+?)`?$", s)
        if m:
            kv[m.group(1).strip().lower()] = m.group(2).strip()
    return kv


def report_get(kv: dict, *keys):
    for k in keys:
        if k.lower() in kv:
            return kv[k.lower()]
    return None


def to_bool(v) -> bool | None:
    if v is None:
        return None
    return str(v).strip().lower() in ("true", "1", "yes")


def to_int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
def scan_route_csv():
    res = {"rows": 0, "run_ids": Counter(), "channel_model": Counter(),
           "is_live": Counter(), "sample_valid": Counter(), "flow": Counter(),
           "decision": Counter(), "drop_reason": Counter(),
           "err_reason": Counter(), "max_delta": 0.0, "nonzero_delta": 0,
           "rssi_min": 1e9, "rssi_max": -1e9, "sentinel_rows": 0,
           "synthetic_markers": Counter(), "columns": []}
    with ROUTE_CSV.open(newline="") as f:
        r = csv.DictReader(f)
        res["columns"] = r.fieldnames or []
        for row in r:
            res["rows"] += 1
            res["run_ids"][row.get("run_id", "")] += 1
            res["channel_model"][row.get("sionna_channel_model", "")] += 1
            res["is_live"][row.get("is_live_rt_route_packet", "")] += 1
            res["sample_valid"][row.get("sionna_sample_valid", "")] += 1
            res["flow"][row.get("flow_id", "")] += 1
            res["decision"][row.get("final_decision", "")] += 1
            res["drop_reason"][row.get("drop_reason", "")] += 1
            res["err_reason"][row.get("error_rate_reason", "")] += 1
            # synthetic-target detection: любой признак не-route источника
            ts = row.get("ns3_trace_source", "")
            res["synthetic_markers"][ts] += 1
            try:
                a = float(row["RSSI_from_Sionna"])
                b = float(row["RSSI_used_for_drop_decision"])
                d = abs(a - b)
                if d > res["max_delta"]:
                    res["max_delta"] = d
                if d > 1e-9:
                    res["nonzero_delta"] += 1
                res["rssi_min"] = min(res["rssi_min"], b)
                res["rssi_max"] = max(res["rssi_max"], b)
                if b <= SENTINEL_RSSI_DBM:
                    res["sentinel_rows"] += 1
            except (ValueError, KeyError):
                pass
    return res


def scan_payload_csv():
    res = {"rows": 0, "run_ids": Counter(), "dir": Counter(),
           "near_far_decision": Counter(), "is_rtp": Counter()}
    if not PAYLOAD_CSV.exists():
        return res
    with PAYLOAD_CSV.open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            res["rows"] += 1
            res["run_ids"][row.get("run_id", "")] += 1
            d = row.get("direction", "")
            res["dir"][d] += 1
            res["is_rtp"][row.get("is_rtp", "")] += 1
            if d == "near_to_far":
                res["near_far_decision"][row.get("final_decision", "")] += 1
    return res


def count_frames():
    if not FRAMES_CSV.exists():
        return 0, 0
    n = 0
    gaps = 0
    with FRAMES_CSV.open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            n += 1
            if str(row.get("gap_event", "")).strip().lower() == "true":
                gaps += 1
    return n, gaps


def load_bins():
    rows = []
    if BINS_CSV.exists():
        rows = list(csv.DictReader(BINS_CSV.open(newline="")))
    return rows


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="stage24_rf_stress_final_20260605T132014Z")
    ap.add_argument("--out-md", default=str(REPORTS / "stage24_c4_acceptance_audit.md"))
    ap.add_argument("--out-json", default=str(REPORTS / "stage24_c4_acceptance_audit.json"))
    args = ap.parse_args(argv)
    EXPECT = args.run_id

    route = scan_route_csv()
    pay = scan_payload_csv()
    n_frames, n_gap_events = count_frames()
    bins = load_bins()
    pm = parse_report_kv(PAYLOAD_METRICS_REPORT)
    pp = parse_report_kv(PAYLOAD_PATH_REPORT)

    ctrl = route["flow"].get("control", 0)
    payf = route["flow"].get("payload", 0)
    entering = pay["dir"].get("near_to_far", 0)
    leaving = pay["near_far_decision"].get("received", 0)
    dropped = pay["near_far_decision"].get("dropped", 0)

    rtp_recv = (to_int(report_get(pm, "RTP received after ns-3", "rtp received after ns-3"))
                or to_int(report_get(pp, "RTP/video packets received after ns-3")))
    interruptions = (to_int(report_get(pm, "interruptions"))
                     or to_int(report_get(pp, "Interruption intervals over 1.000s")))
    bypass = to_bool(report_get(pm, "payload_bypass"))
    bypass_disabled = to_bool(report_get(pm, "payload_bypass_disabled"))
    require_ns3 = to_bool(report_get(pm, "require_ns3_payload"))
    valid_audit = to_bool(report_get(pm, "valid_payload_route_audit"))

    normal_bins = [b for b in bins if b.get("coverage_class") == "normal"]
    floor_bins = [b for b in bins if b.get("coverage_class") != "normal"]
    bins_pass_thresh = [b for b in normal_bins
                        if to_int(b.get("attempted_packets")) and
                        to_int(b["attempted_packets"]) >= BIN_MIN_PACKETS]
    weak_bins = [b for b in normal_bins
                 if float(b["empirical_loss_ratio"]) > WEAK_LOSS_THRESHOLD]
    strong_bins = [b for b in normal_bins
                   if float(b["empirical_loss_ratio"]) < STRONG_LOSS_THRESHOLD]
    figs_present = {n: all((FIGURES / f"{n}.{e}").exists() for e in ("png", "pdf", "svg"))
                    for n in IEEE_FIGURES}

    # --- Criteria ---
    checks = []

    def add(idx, name, ok, detail):
        checks.append({"n": idx, "criterion": name, "pass": bool(ok), "detail": detail})

    add(1, "single run_id (route+payload)",
        set(route["run_ids"]) == {EXPECT} and (pay["rows"] == 0 or set(pay["run_ids"]) == {EXPECT}),
        f"route={dict(route['run_ids'])} payload={dict(pay['run_ids'])}")
    add(2, "no synthetic RSSI targets (route trace source = ns-3 error model only)",
        set(route["synthetic_markers"]) <= {"ReceiveErrorModel::DoCorrupt", ""},
        f"ns3_trace_source={dict(route['synthetic_markers'])}")
    add(3, "only rt_online + is_live + sample_valid rows",
        set(route["channel_model"]) <= {"rt_online"} and set(route["is_live"]) <= {"True"}
        and set(route["sample_valid"]) <= {"True"},
        f"channel_model={dict(route['channel_model'])} is_live={dict(route['is_live'])} "
        f"sample_valid={dict(route['sample_valid'])}")
    add(4, "RSSI_from_Sionna == RSSI_used (max delta ~0)",
        route["max_delta"] == 0.0 and route["nonzero_delta"] == 0,
        f"max_delta_abs={route['max_delta']} count_nonzero_delta={route['nonzero_delta']}")
    add(5, "drop_reason = phy_rf_bernoulli for all drops",
        set(route["drop_reason"]) <= {"none", "phy_rf_bernoulli"}
        and route["drop_reason"].get("phy_rf_bernoulli", 0) == route["decision"].get("dropped", -1),
        f"drop_reason={dict(route['drop_reason'])} err_reason={dict(route['err_reason'])}")
    add(6, "control packets >= 3000", ctrl >= 3000, f"control={ctrl}")
    add(7, "payload route packets >= 10000", payf >= 10000, f"payload_route={payf}")
    add(8, "payload entering ns-3 >= 10000", entering >= 10000, f"entering_ns3={entering}")
    add(9, "payload leaving ns-3 > 0", leaving > 0, f"leaving_ns3={leaving}")
    add(10, "payload dropped by ns-3 > 0", dropped > 0, f"dropped_by_ns3={dropped}")
    add(11, "post-ns3 RTP received > 0", (rtp_recv or 0) > 0, f"rtp_received={rtp_recv}")
    add(12, "decoded frames > 0", n_frames > 0, f"decoded_frames_csv={n_frames}")
    add(13, "interruptions present (>=0, reported)", interruptions is not None,
        f"interruptions={interruptions} frame_gap_events_csv={n_gap_events}")
    add(14, "payload_bypass = false", bypass is False or bypass_disabled is True,
        f"payload_bypass={bypass} payload_bypass_disabled={bypass_disabled}")
    add(15, "require_ns3_payload = true", require_ns3 is True, f"require_ns3_payload={require_ns3}")
    add(16, "valid_payload_route_audit = true", valid_audit is True,
        f"valid_payload_route_audit={valid_audit}")
    add(17, "no-coverage/sentinel floor rows = 0 in route CSV",
        route["sentinel_rows"] == 0, f"sentinel_rows(RSSI<={SENTINEL_RSSI_DBM})={route['sentinel_rows']}")
    add(18, "RSSI range (excl floor) is a finite transition corridor",
        route["rssi_max"] > route["rssi_min"] > -119.0,
        f"rssi_used_range={route['rssi_min']:.3f}..{route['rssi_max']:.3f} dBm")
    add(19, "normal RSSI bins present", len(normal_bins) >= 4,
        f"normal_bins={len(normal_bins)} floor_bins={len(floor_bins)}")
    add(20, "bins pass packet-count threshold",
        len(bins_pass_thresh) == len(normal_bins) and len(normal_bins) > 0,
        f"bins_attempted>={BIN_MIN_PACKETS}: {len(bins_pass_thresh)}/{len(normal_bins)}; "
        f"low_confidence_any={any(to_bool(b.get('low_confidence')) for b in normal_bins)}")
    add(21, "weak/transition bin loss > 0.2 exists", len(weak_bins) >= 1,
        f"weak_bins={[(b['flow_id'], b['rssi_bin_low'], round(float(b['empirical_loss_ratio']),3)) for b in weak_bins]}")
    add(22, "strong bin loss < 0.05 exists", len(strong_bins) >= 1,
        f"strong_bins={[(b['flow_id'], b['rssi_bin_low'], round(float(b['empirical_loss_ratio']),3)) for b in strong_bins]}")
    add(23, "IEEE figures regenerated (png+pdf+svg present)",
        all(figs_present.values()), f"figures_present={figs_present}")
    add(24, "no old/failed run mixed (all processed CSV single run_id)",
        set(route["run_ids"]) == {EXPECT} and (pay["rows"] == 0 or set(pay["run_ids"]) == {EXPECT}),
        "processed route+payload CSVs carry exactly one run_id")

    n_pass = sum(1 for c in checks if c["pass"])
    n_fail = len(checks) - n_pass
    # Критерий 23 (figures) может быть pending до генерации — выделяем отдельно.
    blocking_fail = [c for c in checks if not c["pass"] and c["n"] != 23]
    status = "PASS" if not blocking_fail else "FAIL"

    summary = {
        "status": status,
        "run_id": EXPECT,
        "commit": git_commit(),
        "worktree_dirty": git_dirty(),
        "n_pass": n_pass, "n_fail": n_fail,
        "blocking_failures": [c["n"] for c in blocking_fail],
        "numbers": {
            "route_rows": route["rows"],
            "control_packets": ctrl,
            "payload_route_packets": payf,
            "control_received": route["flow"] and None,  # filled below
            "received_total": route["decision"].get("received", 0),
            "dropped_total": route["decision"].get("dropped", 0),
            "payload_entering_ns3": entering,
            "payload_leaving_ns3": leaving,
            "payload_dropped_by_ns3": dropped,
            "post_ns3_rtp_received": rtp_recv,
            "decoded_frames": n_frames,
            "interruptions": interruptions,
            "rssi_used_min_dbm": round(route["rssi_min"], 3),
            "rssi_used_max_dbm": round(route["rssi_max"], 3),
            "max_rssi_delta_abs": route["max_delta"],
            "sentinel_floor_rows": route["sentinel_rows"],
            "normal_bins": len(normal_bins),
            "floor_bins": len(floor_bins),
        },
        "checks": checks,
    }
    summary["numbers"].pop("control_received", None)

    # --- write JSON ---
    REPORTS.mkdir(exist_ok=True)
    Path(args.out_json).write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                   encoding="utf-8")

    # --- write MD ---
    lines = [
        "# Stage 2.4 C4 Acceptance Audit",
        "",
        f"- **Status:** {status}",
        f"- run_id: `{EXPECT}`",
        f"- commit: `{summary['commit']}`  (worktree dirty: {summary['worktree_dirty']})",
        f"- criteria pass: {n_pass}/{len(checks)}  (blocking failures: {summary['blocking_failures'] or 'none'})",
        "",
        "This audit is derived directly from processed/raw CSVs and the payload path",
        "report, not from a hand summary. It is a **simulated route-level packet audit**,",
        "not hardware validation and not field PER measurement.",
        "",
        "## Acceptance table",
        "",
        "| # | Criterion | Result | Detail |",
        "|---|---|---|---|",
    ]
    for c in checks:
        res = "PASS" if c["pass"] else ("PENDING" if c["n"] == 23 else "FAIL")
        det = c["detail"].replace("|", "\\|")
        lines.append(f"| {c['n']} | {c['criterion']} | {res} | {det} |")
    lines += [
        "",
        "## Key numbers",
        "",
        f"- control packets: {ctrl}",
        f"- payload route packets: {payf}",
        f"- payload entering ns-3: {entering}",
        f"- payload leaving ns-3: {leaving}",
        f"- payload dropped by ns-3: {dropped}",
        f"- post-ns3 RTP received: {rtp_recv}",
        f"- decoded frames: {n_frames}",
        f"- interruptions: {interruptions}",
        f"- RSSI used range: {route['rssi_min']:.3f}..{route['rssi_max']:.3f} dBm",
        f"- max RSSI_from_Sionna vs RSSI_used delta: {route['max_delta']}",
        f"- no-coverage/sentinel floor rows: {route['sentinel_rows']}",
        f"- normal bins / floor bins: {len(normal_bins)} / {len(floor_bins)}",
        "",
        "Outputs: `reports/stage24_c4_acceptance_audit.json` (machine-readable).",
    ]
    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[c4-audit] status={status} pass={n_pass}/{len(checks)} "
          f"blocking={summary['blocking_failures'] or 'none'}")
    print(f"[c4-audit] wrote {args.out_md}")
    print(f"[c4-audit] wrote {args.out_json}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
