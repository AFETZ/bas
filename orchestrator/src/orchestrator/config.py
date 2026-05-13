"""Загрузка YAML-конфигураций сценария, сети и миссии."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class NetworkProfile:
    profile_id: str
    transport: str
    link_type: str
    bandwidth_mbps: float
    delay_ms: float
    jitter_ms: float
    packet_loss_ratio: float
    outage_periods: list[list[float]]
    raw: dict[str, Any]


@dataclass(frozen=True)
class Mission:
    mission_id: str
    home: dict[str, float]
    waypoints: list[dict[str, Any]]
    success_criteria: list[Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    seed: int
    mission: Mission
    control_profile: NetworkProfile
    payload_profile: NetworkProfile
    components: dict[str, Any]
    max_duration_s: float
    raw: dict[str, Any]


@dataclass(frozen=True)
class Paths:
    root: Path
    configs: Path
    logs: Path
    scenarios: Path
    network_profiles: Path
    missions: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        root = root.resolve()
        return cls(
            root=root,
            configs=root / "configs",
            logs=root / "logs",
            scenarios=root / "configs" / "scenarios",
            network_profiles=root / "configs" / "network_profiles",
            missions=root / "configs" / "missions",
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_scenario(scenario_id: str, paths: Paths) -> Scenario:
    scenario_path = paths.scenarios / f"{scenario_id}.yaml"
    if not scenario_path.exists():
        raise FileNotFoundError(f"Сценарий не найден: {scenario_path}")
    raw = _load_yaml(scenario_path)

    mission = load_mission(raw["mission"]["ref"], paths)
    control_profile = load_network_profile(raw["network"]["control_channel"]["profile"], paths)
    payload_profile = load_network_profile(raw["network"]["payload_channel"]["profile"], paths)

    return Scenario(
        scenario_id=raw["scenario_id"],
        seed=int(raw.get("seed", 0)),
        mission=mission,
        control_profile=control_profile,
        payload_profile=payload_profile,
        components=raw.get("components", {}),
        max_duration_s=float(raw["mission"].get("max_duration_s", 600.0)),
        raw=raw,
    )


def load_mission(mission_ref: str, paths: Paths) -> Mission:
    path = paths.missions / f"{mission_ref}.yaml"
    raw = _load_yaml(path)
    return Mission(
        mission_id=raw["mission_id"],
        home=raw["home"],
        waypoints=raw["waypoints"],
        success_criteria=raw.get("success_criteria", []),
        raw=raw,
    )


def load_network_profile(profile_id: str, paths: Paths) -> NetworkProfile:
    path = paths.network_profiles / f"{profile_id}.yaml"
    raw = _load_yaml(path)
    p = raw["parameters"]
    return NetworkProfile(
        profile_id=raw["profile_id"],
        transport=raw["transport"],
        link_type=raw["link_type"],
        bandwidth_mbps=float(p["bandwidth_mbps"]),
        delay_ms=float(p["delay_ms"]),
        jitter_ms=float(p["jitter_ms"]),
        packet_loss_ratio=float(p["packet_loss_ratio"]),
        outage_periods=[list(map(float, w)) for w in p.get("outage_periods", [])],
        raw=raw,
    )


def config_hash(scenario: Scenario) -> str:
    """Стабильный хэш конфига для воспроизводимости (поле config_hash в журнале)."""
    payload = {
        "scenario": scenario.raw,
        "mission": scenario.mission.raw,
        "control_profile": scenario.control_profile.raw,
        "payload_profile": scenario.payload_profile.raw,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:16]
