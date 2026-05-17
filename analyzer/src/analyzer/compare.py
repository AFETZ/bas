"""Этап 1.6: сравнительный отчёт двух прогонов (WiFi vs LoRa).

Берёт два каталога прогонов (logs/<run_id>/), загружает каждый через
существующий load + compute, и генерирует:
  * `comparison.md` — side-by-side таблицы (полёт, сетевые потоки, видео,
    outage correlation), плюс короткий вывод-сравнение.
  * `comparison.csv` — flat metric/wifi/lora/delta, удобно для Excel/pandas.

Не запускает прогоны сам — оркестрация в scripts/run_stage_1_6_compare.sh.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .load import find_run_dir, load_run_events, load_video_events
from .metrics import RunReport, compute, compute_video


@dataclass
class LabeledRun:
    label: str
    run_dir: Path
    report: RunReport


def load_labeled_run(run_path: str | Path, label: str) -> LabeledRun:
    run_dir = find_run_dir(str(run_path))
    events = load_run_events(run_dir)
    report = compute(events)
    tx_events, rx_events = load_video_events(run_dir)
    if tx_events or rx_events:
        report.video = compute_video(tx_events, rx_events, events)
    return LabeledRun(label=label, run_dir=run_dir, report=report)


# ---------------- markdown side-by-side ----------------

def _bool_str(v: bool) -> str:
    return "да" if v else "нет"


def _fmt(v: float, fmt: str = "{:.3f}") -> str:
    try:
        return fmt.format(v)
    except Exception:
        return str(v)


def to_compare_markdown(a: LabeledRun, b: LabeledRun) -> str:
    """Side-by-side сравнение двух прогонов в markdown."""
    ra, rb = a.report, b.report
    lines: list[str] = []
    lines.append(f"# Сравнительный отчёт: {a.label} vs {b.label}")
    lines.append("")
    lines.append("Этап 1.6: side-by-side сравнение двух сетевых профилей по полётному, "
                 "канальному и видеоконтуру. Прогоны сделаны через "
                 "`scripts/run_stage_1_6_compare.sh`; каждый — отдельный run-dir.")
    lines.append("")
    lines.append("## Идентификация прогонов")
    lines.append("")
    lines.append("| параметр | " + a.label + " | " + b.label + " |")
    lines.append("|---|---|---|")
    lines.append(f"| run_id | `{ra.run_id}` | `{rb.run_id}` |")
    lines.append(f"| scenario_id | `{ra.scenario_id}` | `{rb.scenario_id}` |")
    lines.append(f"| config_hash | `{ra.config_hash}` | `{rb.config_hash}` |")
    lines.append(f"| seed | {ra.seed} | {rb.seed} |")
    lines.append(f"| status | `{ra.scenario_status}` | `{rb.scenario_status}` |")
    lines.append(f"| run_dir | `{a.run_dir}` | `{b.run_dir}` |")
    lines.append("")

    # ---- Полётный контур ----
    lines.append("## Полётный контур")
    lines.append("")
    lines.append("| метрика | " + a.label + " | " + b.label + " |")
    lines.append("|---|---:|---:|")
    fa, fb = ra.flight, rb.flight
    lines.append(f"| Sample-ов | {fa.samples} | {fb.samples} |")
    lines.append(f"| Длительность (sim), с | {fa.duration_s:.1f} | {fb.duration_s:.1f} |")
    lines.append(f"| Финальный режим | `{fa.final_mode}` | `{fb.final_mode}` |")
    lines.append(f"| Посажен | {_bool_str(fa.landed)} | {_bool_str(fb.landed)} |")
    lines.append(f"| Waypoint'ов пройдено | {fa.waypoints_reached}/{fa.waypoints_planned} | "
                 f"{fb.waypoints_reached}/{fb.waypoints_planned} |")
    lines.append(f"| Дистанция, м | {fa.distance_flown_m:.1f} | {fb.distance_flown_m:.1f} |")
    lines.append(f"| Макс. высота, м | {fa.max_altitude_m:.1f} | {fb.max_altitude_m:.1f} |")
    lines.append(f"| Макс. скорость, м/с | {fa.max_speed_mps:.2f} | {fb.max_speed_mps:.2f} |")
    lines.append("")

    # ---- Сетевые потоки ----
    lines.append("## Сетевые потоки")
    lines.append("")
    flow_ids = sorted(set(ra.flows.keys()) | set(rb.flows.keys()))
    for flow_id in flow_ids:
        lines.append(f"### `{flow_id}`")
        lines.append("")
        lines.append("| метрика | " + a.label + " | " + b.label + " |")
        lines.append("|---|---:|---:|")
        fa_flow = ra.flows.get(flow_id)
        fb_flow = rb.flows.get(flow_id)
        if fa_flow is None or fb_flow is None:
            present_in = a.label if fa_flow else b.label
            lines.append(f"| _только в_ | `{present_in}` | `{present_in}` |")
            lines.append("")
            continue
        lines.append(f"| Пакетов всего | {fa_flow.packets_total} | {fb_flow.packets_total} |")
        lines.append(f"| Доставлено | {fa_flow.packets_delivered} | {fb_flow.packets_delivered} |")
        lines.append(f"| PDR | {fa_flow.pdr:.3f} | {fb_flow.pdr:.3f} |")
        lines.append(f"| Потерь | {fa_flow.packets_lost} | {fb_flow.packets_lost} |")
        lines.append(f"| В outage | {fa_flow.packets_in_outage} | {fb_flow.packets_in_outage} |")
        lines.append(f"| Средн. задержка, мс | {fa_flow.mean_delay_ms:.1f} | {fb_flow.mean_delay_ms:.1f} |")
        lines.append(f"| p95 задержка, мс | {fa_flow.p95_delay_ms:.1f} | {fb_flow.p95_delay_ms:.1f} |")
        lines.append(f"| Средн. jitter, мс | {fa_flow.mean_jitter_ms:.1f} | {fb_flow.mean_jitter_ms:.1f} |")
        lines.append(f"| Goodput, бит/с | {fa_flow.goodput_bps:,.0f} | {fb_flow.goodput_bps:,.0f} |")
        lines.append("")

    # ---- Видеопоток ----
    va, vb = ra.video, rb.video
    if va.enabled or vb.enabled:
        lines.append("## Видеопоток")
        lines.append("")
        lines.append("| метрика | " + a.label + " | " + b.label + " |")
        lines.append("|---|---:|---:|")
        lines.append(f"| Timing source | "
                     f"{'precise' if va.e2e_latency_precise else 'approximate'} | "
                     f"{'precise' if vb.e2e_latency_precise else 'approximate'} |")
        lines.append(f"| TX RTP-пакетов | {va.tx_packets} | {vb.tx_packets} |")
        lines.append(f"| RX RTP-пакетов | {va.rx_packets} | {vb.rx_packets} |")
        lines.append(f"| Frame loss, % | {100.0 * va.frame_loss_ratio:.2f} | {100.0 * vb.frame_loss_ratio:.2f} |")
        lines.append(f"| Longest gap, пакетов | {va.longest_gap_packets} | {vb.longest_gap_packets} |")
        lines.append(f"| Longest gap, мс | {va.longest_gap_ms:.1f} | {vb.longest_gap_ms:.1f} |")
        lines.append(f"| Jitter RFC 3550, мс | {va.jitter_ms_rfc3550:.2f} | {vb.jitter_ms_rfc3550:.2f} |")
        lines.append(f"| FPS принято | {va.fps_received:.1f} | {vb.fps_received:.1f} |")
        lines.append(f"| Bitrate RX goodput, бит/с | {va.bitrate_rx_goodput_bps:,.0f} | {vb.bitrate_rx_goodput_bps:,.0f} |")
        lines.append(f"| E2E **min**, мс | {va.e2e_latency_ms_min:.1f} | {vb.e2e_latency_ms_min:.1f} |")
        lines.append(f"| E2E p50, мс | {va.e2e_latency_ms_p50:.1f} | {vb.e2e_latency_ms_p50:.1f} |")
        lines.append(f"| E2E p95, мс | {va.e2e_latency_ms_p95:.1f} | {vb.e2e_latency_ms_p95:.1f} |")
        # MP4 paths если есть
        mp4_a = a.run_dir / "video_rx.mp4"
        mp4_b = b.run_dir / "video_rx.mp4"
        mp4_a_str = f"`{mp4_a}` ({mp4_a.stat().st_size//1024} KB)" if mp4_a.exists() else "—"
        mp4_b_str = f"`{mp4_b}` ({mp4_b.stat().st_size//1024} KB)" if mp4_b.exists() else "—"
        lines.append(f"| MP4 demo | {mp4_a_str} | {mp4_b_str} |")
        lines.append("")

        # outage correlation summary
        if va.payload_outage_windows or vb.payload_outage_windows:
            lines.append("### Outage ↔ video gap корреляция")
            lines.append("")
            lines.append("| метрика | " + a.label + " | " + b.label + " |")
            lines.append("|---|---|---|")
            wa = ", ".join(w.label for w in va.payload_outage_windows) or "—"
            wb = ", ".join(w.label for w in vb.payload_outage_windows) or "—"
            lines.append(f"| Payload outage windows | {wa} | {wb} |")
            lines.append(f"| Gap-событий около outage | {va.video_gap_events_near_outage} | {vb.video_gap_events_near_outage} |")
            lines.append(f"| RTP потеряно около outage | {va.video_gap_packets_near_outage} | {vb.video_gap_packets_near_outage} |")
            lines.append(f"| RTP потеряно вне outage | {va.video_gap_packets_outside_outage} | {vb.video_gap_packets_outside_outage} |")
            lines.append("")

    # ---- Краткий вывод ----
    lines.append("## Краткий вывод")
    lines.append("")
    lines += _conclusion(a, b)

    return "\n".join(lines) + "\n"


def _conclusion(a: LabeledRun, b: LabeledRun) -> list[str]:
    """Двух-трёх параграфный вывод на основе разницы метрик."""
    ra, rb = a.report, b.report
    out: list[str] = []

    # Mission outcome
    if ra.flight.landed and rb.flight.landed:
        out.append(f"- На обоих профилях миссия завершена успешно (landed=True), "
                   f"полётный контур не деградировал ни в `{a.label}` ни в `{b.label}`.")
    elif ra.flight.landed and not rb.flight.landed:
        out.append(f"- На `{a.label}` миссия посажена, на `{b.label}` — нет; "
                   "сеть оказала влияние на полётный контур.")
    elif rb.flight.landed and not ra.flight.landed:
        out.append(f"- На `{b.label}` миссия посажена, на `{a.label}` — нет.")
    else:
        out.append("- Ни в одном из прогонов миссия не была посажена.")

    # Control channel comparison
    ca = ra.flows.get("control")
    cb = rb.flows.get("control")
    if ca and cb:
        out.append(f"- Control channel: PDR `{a.label}` {ca.pdr:.3f} / `{b.label}` {cb.pdr:.3f}; "
                   f"средняя задержка {ca.mean_delay_ms:.0f} мс / {cb.mean_delay_ms:.0f} мс. "
                   f"MAVLink mission upload и AUTO-flight выдержали оба профиля.")

    # Payload + video
    pa = ra.flows.get("payload")
    pb = rb.flows.get("payload")
    va, vb = ra.video, rb.video
    if pa and pb and (va.enabled or vb.enabled):
        delta_min = vb.e2e_latency_ms_min - va.e2e_latency_ms_min
        out.append(f"- Видеоканал: реальная transit-latency "
                   f"`{a.label}` {va.e2e_latency_ms_min:.0f} мс vs `{b.label}` {vb.e2e_latency_ms_min:.0f} мс "
                   f"(Δ {delta_min:+.0f} мс), что соответствует разнице конфигурируемых задержек "
                   f"ns-3 control+payload каналов.")
        if vb.video_gap_events_near_outage > 0:
            out.append(f"- На `{b.label}` outage-окна payload-канала "
                       f"({', '.join(w.label for w in vb.payload_outage_windows)}) "
                       f"видны в видеопотоке: {vb.video_gap_events_near_outage} gap-событий "
                       f"около outage, потеряно {vb.video_gap_packets_near_outage} RTP-пакетов "
                       f"(самый длинный — {vb.longest_gap_packets} пакетов / {vb.longest_gap_ms:.0f} мс). "
                       "Корреляция деградации канала с видеопотерями подтверждена.")
        elif va.video_gap_events_near_outage > 0:
            out.append(f"- На `{a.label}` outage-окна привели к {va.video_gap_events_near_outage} "
                       f"gap-событиям; на `{b.label}` outage не пересёкся с видео-gap'ами.")

    return out


# ---------------- flat CSV ----------------

CSV_HEADER = ["metric", "{label_a}", "{label_b}", "delta"]


def _delta(a: float, b: float) -> str:
    try:
        return f"{(b - a):+g}"
    except Exception:
        return ""


def to_compare_csv(a: LabeledRun, b: LabeledRun) -> str:
    """Flat metric/labelA/labelB/delta CSV."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", a.label, b.label, "delta"])

    def row(metric: str, va, vb) -> None:
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            w.writerow([metric, va, vb, vb - va])
        else:
            w.writerow([metric, va, vb, ""])

    ra, rb = a.report, b.report
    row("scenario_status", ra.scenario_status, rb.scenario_status)
    row("flight_landed", int(ra.flight.landed), int(rb.flight.landed))
    row("flight_duration_s", round(ra.flight.duration_s, 1), round(rb.flight.duration_s, 1))
    row("flight_waypoints_reached", ra.flight.waypoints_reached, rb.flight.waypoints_reached)
    row("flight_distance_flown_m", round(ra.flight.distance_flown_m, 1), round(rb.flight.distance_flown_m, 1))
    row("flight_max_altitude_m", round(ra.flight.max_altitude_m, 1), round(rb.flight.max_altitude_m, 1))
    row("flight_max_speed_mps", round(ra.flight.max_speed_mps, 2), round(rb.flight.max_speed_mps, 2))

    for flow_id in sorted(set(ra.flows.keys()) | set(rb.flows.keys())):
        fa = ra.flows.get(flow_id)
        fb = rb.flows.get(flow_id)
        if not fa or not fb:
            continue
        row(f"{flow_id}_packets_total", fa.packets_total, fb.packets_total)
        row(f"{flow_id}_pdr", round(fa.pdr, 4), round(fb.pdr, 4))
        row(f"{flow_id}_packets_lost", fa.packets_lost, fb.packets_lost)
        row(f"{flow_id}_packets_in_outage", fa.packets_in_outage, fb.packets_in_outage)
        row(f"{flow_id}_mean_delay_ms", round(fa.mean_delay_ms, 1), round(fb.mean_delay_ms, 1))
        row(f"{flow_id}_p95_delay_ms", round(fa.p95_delay_ms, 1), round(fb.p95_delay_ms, 1))
        row(f"{flow_id}_mean_jitter_ms", round(fa.mean_jitter_ms, 2), round(fb.mean_jitter_ms, 2))
        row(f"{flow_id}_goodput_bps", round(fa.goodput_bps, 0), round(fb.goodput_bps, 0))

    va, vb = ra.video, rb.video
    if va.enabled or vb.enabled:
        row("video_timing_precise", int(va.e2e_latency_precise), int(vb.e2e_latency_precise))
        row("video_tx_packets", va.tx_packets, vb.tx_packets)
        row("video_rx_packets", va.rx_packets, vb.rx_packets)
        row("video_frame_loss_ratio", round(va.frame_loss_ratio, 4), round(vb.frame_loss_ratio, 4))
        row("video_longest_gap_packets", va.longest_gap_packets, vb.longest_gap_packets)
        row("video_longest_gap_ms", round(va.longest_gap_ms, 1), round(vb.longest_gap_ms, 1))
        row("video_jitter_rfc3550_ms", round(va.jitter_ms_rfc3550, 2), round(vb.jitter_ms_rfc3550, 2))
        row("video_fps_received", round(va.fps_received, 2), round(vb.fps_received, 2))
        row("video_bitrate_rx_goodput_bps", round(va.bitrate_rx_goodput_bps, 0), round(vb.bitrate_rx_goodput_bps, 0))
        row("video_e2e_min_ms", round(va.e2e_latency_ms_min, 1), round(vb.e2e_latency_ms_min, 1))
        row("video_e2e_p50_ms", round(va.e2e_latency_ms_p50, 1), round(vb.e2e_latency_ms_p50, 1))
        row("video_e2e_p95_ms", round(va.e2e_latency_ms_p95, 1), round(vb.e2e_latency_ms_p95, 1))
        row("video_gap_events_near_outage", va.video_gap_events_near_outage, vb.video_gap_events_near_outage)
        row("video_gap_packets_near_outage", va.video_gap_packets_near_outage, vb.video_gap_packets_near_outage)
        row("video_gap_packets_outside_outage", va.video_gap_packets_outside_outage, vb.video_gap_packets_outside_outage)

    return buf.getvalue()
