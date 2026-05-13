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
