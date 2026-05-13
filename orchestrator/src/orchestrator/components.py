"""Жизненный цикл компонентов стенда.

Каждый компонент - адаптер над внешним процессом (Docker-контейнер или нативный
бинарь). Этап 1 реализован как stub-режим: компоненты эмулируют события и пишут в
общий журнал, что позволяет отладить оркестратор и анализатор до установки тяжёлых
зависимостей. Когда Docker и образы будут готовы, классы здесь заменят stub на
реальные docker-compose-вызовы.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Protocol

from .config import Mission, NetworkProfile, Scenario
from .logger import EventLogger


class Component(Protocol):
    name: str
    def start(self) -> None: ...
    def step(self, sim_time: float, wall_dt: float) -> None: ...
    def stop(self) -> None: ...


@dataclass
class StubGazeboArduPilot:
    """Заглушка связки Gazebo + ArduPilot SITL.

    Эмулирует движение по маршруту: на каждом шаге пересчитывает положение к
    следующей точке с заданной скоростью и пишет flight-события.
    """
    name: str
    mission: Mission
    logger: EventLogger
    speed_mps: float = 8.0
    update_hz: float = 10.0

    def __post_init__(self) -> None:
        self._wp_index = 0
        self._pos = (0.0, 0.0, 0.0)
        self._mode = "init"
        self._last_step = 0.0

    def start(self) -> None:
        self.logger.emit(
            "component",
            component=self.name,
            phase="start",
            mission_id=self.mission.mission_id,
        )
        self._mode = "takeoff"

    def step(self, sim_time: float, wall_dt: float) -> None:
        if sim_time - self._last_step < 1.0 / self.update_hz:
            return
        self._last_step = sim_time

        if self._wp_index >= len(self.mission.waypoints):
            return

        wp = self.mission.waypoints[self._wp_index]
        action = wp.get("action")
        if action == "takeoff":
            target = (0.0, 0.0, float(wp["alt_m"]))
        elif action == "waypoint":
            target = (float(wp["x_m"]), float(wp["y_m"]), float(wp["alt_m"]))
        elif action == "land":
            target = (self._pos[0], self._pos[1], 0.0)
        else:
            self._wp_index += 1
            return

        self._mode = {"takeoff": "guided", "waypoint": "auto", "land": "land"}[action]
        dx, dy, dz = (t - p for t, p in zip(target, self._pos))
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        step_dist = self.speed_mps / self.update_hz
        if dist <= step_dist or dist < 1e-6:
            self._pos = target
            self._wp_index += 1
            if self._wp_index >= len(self.mission.waypoints) and action == "land":
                self._mode = "landed"
        else:
            scale = step_dist / dist
            self._pos = (
                self._pos[0] + dx * scale,
                self._pos[1] + dy * scale,
                self._pos[2] + dz * scale,
            )

        self.logger.emit(
            "flight",
            sim_time=sim_time,
            vehicle_id="uav-1",
            position={"x": self._pos[0], "y": self._pos[1], "z": self._pos[2]},
            velocity_mps=self.speed_mps,
            flight_mode=self._mode,
            mission_state=("landed" if self._mode == "landed" else "in_progress"),
            waypoint_index=self._wp_index,
        )

    def stop(self) -> None:
        self.logger.emit("component", component=self.name, phase="stop", final_mode=self._mode)

    @property
    def is_complete(self) -> bool:
        return self._mode == "landed"


@dataclass
class StubNs3Channel:
    """Заглушка канала ns-3 с заданным профилем.

    Эмулирует независимые пакеты по двум каналам: control (телеметрия) и payload
    (видео). На каждом шаге генерирует пакеты с заданной частотой, применяет
    задержку/джиттер/потери/outage из профиля и пишет network-события.
    """
    name: str
    flow_id: str
    profile: NetworkProfile
    logger: EventLogger
    packet_rate_hz: float
    packet_size_bytes: int
    rng: random.Random
    is_payload: bool = False

    def __post_init__(self) -> None:
        self._next_tx_time = 0.0
        self._packet_id = 0

    def start(self) -> None:
        self.logger.emit(
            "component",
            component=self.name,
            phase="start",
            flow_id=self.flow_id,
            profile=self.profile.profile_id,
            link_type=self.profile.link_type,
        )

    def step(self, sim_time: float, wall_dt: float) -> None:
        while self._next_tx_time <= sim_time:
            self._emit_packet(self._next_tx_time)
            self._next_tx_time += 1.0 / self.packet_rate_hz

    def _emit_packet(self, tx_time: float) -> None:
        self._packet_id += 1
        in_outage = any(start <= tx_time <= end for start, end in self.profile.outage_periods)
        lost = in_outage or (self.rng.random() < self.profile.packet_loss_ratio)
        # delay = base + jitter (uniform around 0)
        jitter = (self.rng.random() * 2 - 1) * self.profile.jitter_ms / 1000.0
        delay = max(0.0, self.profile.delay_ms / 1000.0 + jitter)
        rx_time = None if lost else tx_time + delay
        drop_reason = "outage" if in_outage else ("loss" if lost else None)

        event_type = "payload" if self.is_payload else "network"
        record: dict = {
            "sim_time": tx_time,
            "flow_id": self.flow_id,
            "packet_id": self._packet_id,
            "tx_time": tx_time,
            "rx_time": rx_time,
            "drop_reason": drop_reason,
            "delay": delay,
            "jitter": jitter,
            "throughput_bps": self.profile.bandwidth_mbps * 1_000_000,
            "loss_ratio_target": self.profile.packet_loss_ratio,
            "outage_state": in_outage,
            "size_bytes": self.packet_size_bytes,
        }
        if self.is_payload:
            record["payload_id"] = self._packet_id
            record["capture_time"] = tx_time
            record["send_time"] = tx_time
            record["receive_time"] = rx_time
        self.logger.emit(event_type, **record)

    def stop(self) -> None:
        self.logger.emit("component", component=self.name, phase="stop", packets_sent=self._packet_id)
