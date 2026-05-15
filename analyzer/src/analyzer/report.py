"""Сборка markdown-отчёта по прогону."""
from __future__ import annotations

from .metrics import RunReport


def to_markdown(report: RunReport) -> str:
    lines: list[str] = []
    lines.append(f"# Отчёт по прогону {report.run_id}")
    lines.append("")
    lines.append(f"- **Сценарий:** `{report.scenario_id}`")
    lines.append(f"- **config_hash:** `{report.config_hash}`")
    lines.append(f"- **seed:** `{report.seed}`")
    lines.append(f"- **Статус сценария:** `{report.scenario_status}`")
    lines.append("")
    lines.append(f"## Полётный контур")
    lines.append("")
    lines.append(f"- Sample-ов: {report.flight.samples}")
    lines.append(f"- Длительность миссии (sim): {report.flight.duration_s:.1f} с")
    lines.append(f"- Финальный режим: `{report.flight.final_mode}`")
    lines.append(f"- Посажен: `{report.flight.landed}`")
    if report.flight.waypoints_planned:
        lines.append(f"- Waypoint'ов: {report.flight.waypoints_reached}/{report.flight.waypoints_planned}")
    else:
        lines.append(f"- Waypoint'ов пройдено: {report.flight.waypoints_reached}")
    lines.append(f"- Дистанция пройдена: {report.flight.distance_flown_m:.1f} м")
    lines.append(f"- Максимальная высота: {report.flight.max_altitude_m:.1f} м")
    lines.append(f"- Максимальная скорость: {report.flight.max_speed_mps:.2f} м/с")
    pos = report.flight.final_position
    if report.flight.position_format == "geodetic" and pos:
        lines.append(
            f"- Финальная позиция: lat={pos.get('lat', 0):.7f} lon={pos.get('lon', 0):.7f} "
            f"alt_rel={pos.get('alt_rel_m', 0):.2f} м (AMSL={pos.get('alt_amsl_m', 0):.1f} м)"
        )
    elif pos:
        lines.append(
            f"- Финальная позиция: x={pos.get('x', 0):.2f} y={pos.get('y', 0):.2f} z={pos.get('z', 0):.2f}"
        )
    lines.append("")
    lines.append("## Сетевые потоки")
    lines.append("")
    lines.append("| flow_id | пакетов | PDR | потерь | в outage | средн. задержка, мс | p95 задержка, мс | jitter, мс | goodput, бит/с |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for flow_id, fm in sorted(report.flows.items()):
        lines.append(
            f"| `{flow_id}` | {fm.packets_total} | {fm.pdr:.3f} | {fm.packets_lost} | {fm.packets_in_outage} | "
            f"{fm.mean_delay_ms:.1f} | {fm.p95_delay_ms:.1f} | {fm.mean_jitter_ms:.1f} | {fm.goodput_bps:,.0f} |"
        )
    lines.append("")

    # Секция «Видеопоток» появляется только если найден хотя бы один
    # video_tx или video_rx event (этап 1.5.2.a-metrics).
    v = report.video
    if v.enabled:
        lines.append("## Видеопоток")
        lines.append("")
        lines.append(f"- TX уникальных RTP-пакетов (appsink): {v.tx_packets}")
        lines.append(f"- RX уникальных RTP-пакетов: {v.rx_packets}")
        lines.append(f"- Frame loss (gap-detection в RX, wraparound-aware): {100.0 * v.frame_loss_ratio:.2f}%")
        lines.append(
            f"- Самый длинный пропуск: {v.longest_gap_packets} пакетов / "
            f"{v.longest_gap_ms:.1f} мс"
        )
        lines.append(f"- Jitter RFC 3550 (rx-only): {v.jitter_ms_rfc3550:.2f} мс")
        lines.append(f"- FPS принято: {v.fps_received:.1f}")
        lines.append(f"- Длительность RX-потока: {v.duration_s:.1f} с")
        lines.append(f"- Bitrate RX (goodput): {v.bitrate_rx_goodput_bps:,.0f} бит/с")
        lines.append(
            f"- Bitrate TX (appsink, справочно): {v.bitrate_tx_appsink_bps:,.0f} бит/с"
        )
        lines.append("")
        lines.append(
            f"E2E latency (по {v.synchronous_pairs} из {v.matched_packets} matched-пар "
            f"с d ∈ [0, 1] s): "
            f"p50={v.e2e_latency_ms_p50:.1f} мс, "
            f"p95={v.e2e_latency_ms_p95:.1f} мс, "
            f"max={v.e2e_latency_ms_max:.1f} мс"
        )
        lines.append("")
        lines.append(
            "_Примечания:_"
        )
        lines.append(
            "- _TX-счёт из appsink-таппинга video/sender.py содержит больше "
            "RTP-пакетов чем реально уходит на сеть: queue leaky=downstream "
            "перед udpsink дропает buffers под CPU-лимитом. Авторитетный TX-счёт "
            "видеопотока — `payload` row в «Сетевых потоках»._"
        )
        lines.append(
            "- _E2E latency — приблизительная: tx-таппинг через `tee → appsink` "
            "асинхронен с pipeline-clock'ом, поэтому tx_wall может отставать "
            "от реального ухода в udpsink. Берётся подмножество «синхронных» "
            "пар (где tx_wall и rx_wall укладываются в 1 секунду друг от друга). "
            "Frame loss и jitter авторитетны — считаются только по rx-side._"
        )
        lines.append("")

    lines.append("## Синхронизация")
    lines.append("")
    lines.append(f"- Sync-ивентов: {report.sync.samples}")
    lines.append(f"- Среднее real_time_factor: {report.sync.mean_rtf:.3f}")
    lines.append(f"- Макс. рассогласование положения: {report.sync.max_position_desync_m:.2f} м")
    lines.append("")
    lines.append("## Версии компонентов")
    lines.append("")
    for k, v in sorted(report.versions.items()):
        lines.append(f"- {k}: `{v}`")
    return "\n".join(lines) + "\n"
