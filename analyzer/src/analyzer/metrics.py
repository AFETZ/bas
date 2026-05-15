"""Вычисление метрик по контурам.

Метрики берутся из таблицы 1 и таблицы 5 архитектурного документа:
полётный контур, канал управления и телеметрии, видеоданные / полезная нагрузка,
сетевой контур, синхронизация.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlowMetrics:
    flow_id: str
    packets_total: int = 0
    packets_delivered: int = 0
    packets_lost: int = 0
    packets_in_outage: int = 0
    delays_s: list[float] = field(default_factory=list)
    jitters_s: list[float] = field(default_factory=list)
    bytes_sent: int = 0
    bytes_delivered: int = 0
    duration_s: float = 0.0
    configured_delay_s: float | None = None

    @property
    def pdr(self) -> float:
        if self.packets_total == 0:
            return 0.0
        return self.packets_delivered / self.packets_total

    @property
    def loss_ratio(self) -> float:
        if self.packets_total == 0:
            return 0.0
        return self.packets_lost / self.packets_total

    @property
    def mean_delay_ms(self) -> float:
        if not self.delays_s and self.configured_delay_s is not None:
            return 1000.0 * self.configured_delay_s
        return 1000.0 * statistics.mean(self.delays_s) if self.delays_s else 0.0

    @property
    def p95_delay_ms(self) -> float:
        if not self.delays_s:
            if self.configured_delay_s is not None:
                return 1000.0 * self.configured_delay_s
            return 0.0
        sorted_d = sorted(self.delays_s)
        k = max(0, int(0.95 * len(sorted_d)) - 1)
        return 1000.0 * sorted_d[k]

    @property
    def mean_jitter_ms(self) -> float:
        return 1000.0 * statistics.mean(j for j in self.jitters_s if j is not None) if self.jitters_s else 0.0

    @property
    def goodput_bps(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return 8.0 * self.bytes_delivered / self.duration_s


@dataclass
class FlightMetrics:
    samples: int = 0
    final_mode: str = ""
    landed: bool = False
    duration_s: float = 0.0
    waypoints_planned: int = 0
    waypoints_reached: int = 0
    final_position: dict[str, float] = field(default_factory=dict)
    position_format: str = ""   # "xyz" (stub) или "geodetic" (real)
    distance_flown_m: float = 0.0
    max_altitude_m: float = 0.0
    max_speed_mps: float = 0.0


@dataclass
class SyncMetrics:
    samples: int = 0
    mean_rtf: float = 0.0
    max_position_desync_m: float = 0.0


@dataclass
class RunReport:
    run_id: str
    scenario_id: str
    config_hash: str
    seed: int
    flows: dict[str, FlowMetrics]
    flight: FlightMetrics
    sync: SyncMetrics
    scenario_status: str
    versions: dict[str, str]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def compute(events: list[dict[str, Any]]) -> RunReport:
    flows: dict[str, FlowMetrics] = {}
    flight = FlightMetrics()
    sync = SyncMetrics()
    rtf_samples: list[float] = []
    desync_samples: list[float] = []

    run_id = ""
    scenario_id = ""
    config_hash = ""
    seed = 0
    versions: dict[str, str] = {}
    scenario_status = "unknown"

    first_pkt_time: dict[str, float] = {}
    last_pkt_time: dict[str, float] = {}
    prev_aggregate_tx: dict[str, int] = {}
    configured_delays_s: dict[str, float] = {}

    # для подсчёта траектории по двум форматам позиции
    prev_pos_xyz: tuple[float, float, float] | None = None
    prev_pos_geo: tuple[float, float, float] | None = None

    for ev in events:
        et = ev.get("event_type")

        if et == "run_start":
            run_id = ev.get("run_id", "")
            scenario_id = ev.get("scenario_id", "")
            config_hash = ev.get("config_hash", "")
            seed = ev.get("seed", 0)
            versions = ev.get("versions", {})

        elif et == "scenario":
            scenario_status = ev.get("status", scenario_status)
            if ev.get("status") == "success" and ev.get("reason") == "mission_landed":
                flight.landed = True

        elif et == "component":
            if ev.get("component") == "ns3" and ev.get("phase") == "start":
                ctrl = ev.get("ctrl", {})
                pload = ev.get("pload", {})
                if "delay_ms" in ctrl:
                    configured_delays_s["control"] = float(ctrl["delay_ms"]) / 1000.0
                if "delay_ms" in pload:
                    configured_delays_s["payload"] = float(pload["delay_ms"]) / 1000.0
            # Реальный режим: mission-runner emits waypoint_start/_done/mission_started с n_waypoints.
            if ev.get("component", "").startswith("mission-runner"):
                phase = ev.get("phase")
                if phase == "mission_started":
                    flight.waypoints_planned = ev.get("n_waypoints", flight.waypoints_planned)
                elif phase == "waypoint_done":
                    flight.waypoints_reached = max(flight.waypoints_reached, ev.get("idx", -1) + 1)
                elif phase == "mission_complete":
                    if ev.get("final_state") == "landed":
                        flight.landed = True
                    if "last_seq" in ev:
                        flight.waypoints_reached = max(flight.waypoints_reached, int(ev["last_seq"]) + 1)

        elif et == "flight":
            flight.samples += 1
            flight.final_mode = ev.get("flight_mode", flight.final_mode)
            if ev.get("mission_state") == "landed":
                flight.landed = True
            flight.duration_s = max(flight.duration_s, ev.get("sim_time", 0.0))

            pos = ev.get("position", {})
            # Stub-формат: {x, y, z}.
            if "x" in pos and "y" in pos and "z" in pos:
                flight.position_format = "xyz"
                flight.final_position = pos
                cur = (float(pos["x"]), float(pos["y"]), float(pos["z"]))
                if prev_pos_xyz is not None:
                    dx = cur[0] - prev_pos_xyz[0]
                    dy = cur[1] - prev_pos_xyz[1]
                    dz = cur[2] - prev_pos_xyz[2]
                    flight.distance_flown_m += math.sqrt(dx * dx + dy * dy + dz * dz)
                prev_pos_xyz = cur
                flight.max_altitude_m = max(flight.max_altitude_m, float(pos["z"]))
                # Stub: legacy поле waypoint_index в полётных событиях.
                flight.waypoints_reached = max(flight.waypoints_reached, ev.get("waypoint_index", 0))
            # Real-режим: {lat, lon, alt_amsl_m, alt_rel_m}.
            elif "lat" in pos and "lon" in pos:
                flight.position_format = "geodetic"
                flight.final_position = pos
                alt_rel = float(pos.get("alt_rel_m", 0.0))
                cur = (float(pos["lat"]), float(pos["lon"]), alt_rel)
                if prev_pos_geo is not None:
                    horiz = _haversine_m(prev_pos_geo[0], prev_pos_geo[1], cur[0], cur[1])
                    dz = cur[2] - prev_pos_geo[2]
                    flight.distance_flown_m += math.sqrt(horiz * horiz + dz * dz)
                prev_pos_geo = cur
                flight.max_altitude_m = max(flight.max_altitude_m, alt_rel)

            vel = ev.get("velocity_mps", {})
            # Real: {vx, vy, vz}; Stub: scalar в поле velocity_mps.
            if isinstance(vel, dict):
                vx = float(vel.get("vx", 0.0))
                vy = float(vel.get("vy", 0.0))
                vz = float(vel.get("vz", 0.0))
                flight.max_speed_mps = max(flight.max_speed_mps, math.sqrt(vx*vx + vy*vy + vz*vz))
            elif isinstance(vel, (int, float)):
                flight.max_speed_mps = max(flight.max_speed_mps, float(vel))

        elif et == "control_telemetry":
            if ev.get("message_type") == "MISSION_ITEM_REACHED":
                flight.waypoints_reached = max(flight.waypoints_reached, int(ev.get("seq", -1)) + 1)

        elif et in ("network", "payload"):
            flow_id = ev.get("flow_id", "<unknown>")
            fm = flows.setdefault(flow_id, FlowMetrics(flow_id=flow_id))
            if flow_id in configured_delays_s:
                fm.configured_delay_s = configured_delays_s[flow_id]

            # ns-3 currently emits one cumulative counter sample per second.
            if "packets_tx" in ev or "packets_rx" in ev:
                tx = int(ev.get("packets_tx", fm.packets_total) or 0)
                rx = int(ev.get("packets_rx", fm.packets_delivered) or 0)
                bytes_tx = int(ev.get("bytes_tx", fm.bytes_sent) or 0)
                bytes_rx = int(ev.get("bytes_rx", fm.bytes_delivered) or 0)

                prev_tx = prev_aggregate_tx.get(flow_id, tx)
                if ev.get("outage_state"):
                    fm.packets_in_outage += max(0, tx - prev_tx)
                prev_aggregate_tx[flow_id] = tx

                fm.packets_total = max(fm.packets_total, tx)
                fm.packets_delivered = max(fm.packets_delivered, rx)
                fm.packets_lost = max(0, fm.packets_total - fm.packets_delivered)
                fm.bytes_sent = max(fm.bytes_sent, bytes_tx)
                fm.bytes_delivered = max(fm.bytes_delivered, bytes_rx)

                t = ev.get("sim_time")
                if t is not None:
                    first_pkt_time.setdefault(flow_id, t)
                    last_pkt_time[flow_id] = t
                continue

            fm.packets_total += 1
            fm.bytes_sent += ev.get("size_bytes", 0)
            if ev.get("drop_reason") is None and ev.get("rx_time") is not None:
                fm.packets_delivered += 1
                fm.bytes_delivered += ev.get("size_bytes", 0)
                fm.delays_s.append(ev.get("delay", 0.0))
                jitter = ev.get("jitter")
                if jitter is not None:
                    fm.jitters_s.append(jitter)
            else:
                fm.packets_lost += 1
                if ev.get("drop_reason") == "outage":
                    fm.packets_in_outage += 1
            t = ev.get("tx_time", ev.get("sim_time"))
            if t is not None:
                first_pkt_time.setdefault(flow_id, t)
                last_pkt_time[flow_id] = t

        elif et == "sync":
            sync.samples += 1
            rtf = ev.get("real_time_factor")
            if rtf is not None:
                rtf_samples.append(rtf)
            ds = ev.get("position_desync_m")
            if ds is not None:
                desync_samples.append(ds)

    for flow_id, fm in flows.items():
        fm.duration_s = max(0.0, last_pkt_time.get(flow_id, 0.0) - first_pkt_time.get(flow_id, 0.0))

    if rtf_samples:
        sync.mean_rtf = statistics.mean(rtf_samples)
    if desync_samples:
        sync.max_position_desync_m = max(desync_samples)

    return RunReport(
        run_id=run_id,
        scenario_id=scenario_id,
        config_hash=config_hash,
        seed=seed,
        flows=flows,
        flight=flight,
        sync=sync,
        scenario_status=scenario_status,
        versions=versions,
    )
