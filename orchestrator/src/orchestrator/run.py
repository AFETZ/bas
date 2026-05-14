"""Запуск сценария: создание run_id, подъём компонентов, главный цикл, итог."""
from __future__ import annotations

import platform
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .components import StubGazeboArduPilot, StubNs3Channel
from .config import Paths, Scenario, config_hash, load_scenario
from .logger import EventLogger


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def run_scenario(
    scenario_id: str,
    project_root: Path,
    *,
    stub: bool = True,
    mavlink_endpoint: str = "tcp:127.0.0.1:5760",
    external_compose: bool = False,
    run_dir_override: Path | None = None,
) -> Path:
    """Прогнать сценарий, вернуть путь к каталогу с логами прогона.

    Параметры:
        mavlink_endpoint - адрес MAVLink (по умолчанию loopback; для радио-петли
            передаётся IP внутри netns, например tcp:10.10.0.2:5760).
        external_compose - True если контейнеры подняты внешним скриптом
            (этап 1.5.1+); оркестратор не делает docker compose up/down.
        run_dir_override - использовать заданный run_dir вместо генерации.
            Нужно когда host-скрипт уже создал каталог.
    """
    paths = Paths.from_root(project_root)
    scenario = load_scenario(scenario_id, paths)

    if run_dir_override is not None:
        run_dir = run_dir_override
        run_id = run_dir.name
    else:
        run_id = _new_run_id()
        run_dir = paths.logs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "events.jsonl"

    with EventLogger(log_path, run_id=run_id, scenario_id=scenario.scenario_id) as logger:
        _emit_run_start(logger, scenario, stub=stub)
        if stub:
            _run_stub(scenario, logger)
        else:
            _run_real(scenario, logger, project_root=paths.root,
                      mavlink_endpoint=mavlink_endpoint,
                      external_compose=external_compose)
        _emit_run_end(logger, scenario)

    return run_dir


def _emit_run_start(logger: EventLogger, scenario: Scenario, *, stub: bool) -> None:
    logger.emit(
        "run_start",
        config_hash=config_hash(scenario),
        seed=scenario.seed,
        scenario_raw=scenario.raw,
        control_profile=scenario.control_profile.profile_id,
        payload_profile=scenario.payload_profile.profile_id,
        mission=scenario.mission.mission_id,
        max_duration_s=scenario.max_duration_s,
        stub=stub,
        versions={
            "orchestrator": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
    )


def _emit_run_end(logger: EventLogger, scenario: Scenario) -> None:
    logger.emit("run_end", scenario_id=scenario.scenario_id)


def _run_real(
    scenario: Scenario,
    logger: EventLogger,
    project_root: Path,
    *,
    mavlink_endpoint: str = "tcp:127.0.0.1:5760",
    external_compose: bool = False,
) -> None:
    """Запуск через Docker compose (gazebo + sitl, реальная физика и MAVLink)."""
    from .real_components import DockerComposeFlightStack, MissionRunner

    compose_file = project_root / "docker-compose.yml"
    if not compose_file.exists() and not external_compose:
        raise FileNotFoundError(f"docker-compose.yml не найден: {compose_file}")

    stack = DockerComposeFlightStack(
        compose_file=compose_file,
        logger=logger,
        mavlink_endpoint=mavlink_endpoint,
        external_compose=external_compose,
    )
    stack.start()

    try:
        runner = MissionRunner(stack=stack, mission=scenario.mission, logger=logger)
        runner.upload_and_start()

        wall_start = time.time()
        deadline = wall_start + scenario.max_duration_s
        last_status_emit = 0.0

        while True:
            stack.step(time.time() - wall_start, wall_dt=0.5)

            now = time.time()
            if now - last_status_emit >= 5.0:
                # Можно подмиксовать sync-событие тут, пока ns-3 не подключён.
                logger.emit(
                    "sync",
                    sim_time=now - wall_start,
                    wall_time=now,
                    real_time_factor=1.0,
                    position_desync_m=0.0,
                )
                last_status_emit = now

            if stack.is_complete:
                # Статус сценария отражает реальный mission_state, а не всегда success.
                state = stack.mission_state
                if state == "landed":
                    status, reason = "success", "mission_landed"
                elif state == "complete":
                    status, reason = "success", "mission_complete"
                else:  # "failed"
                    status, reason = "failed", f"mission_state={state}"
                logger.emit("scenario", sim_time=now - wall_start,
                            status=status, reason=reason)
                break
            if now > deadline:
                logger.emit("scenario", sim_time=now - wall_start, status="timeout",
                            reason="max_duration_s")
                break
            time.sleep(0.5)
    finally:
        stack.stop()


def _run_stub(scenario: Scenario, logger: EventLogger) -> None:
    rng = random.Random(scenario.seed)

    flight = StubGazeboArduPilot(
        name="gazebo+ardupilot(stub)",
        mission=scenario.mission,
        logger=logger,
    )
    control = StubNs3Channel(
        name="ns3(control,stub)",
        flow_id="control",
        profile=scenario.control_profile,
        logger=logger,
        packet_rate_hz=20.0,         # типичная частота телеметрии MAVLink
        packet_size_bytes=64,
        rng=random.Random(scenario.seed + 1),
    )
    payload = StubNs3Channel(
        name="ns3(payload,stub)",
        flow_id="payload_video",
        profile=scenario.payload_profile,
        logger=logger,
        packet_rate_hz=30.0,         # ~30 кадров/с
        packet_size_bytes=1200,
        rng=random.Random(scenario.seed + 2),
        is_payload=True,
    )

    components = [flight, control, payload]
    for c in components:
        c.start()

    sim_dt = 0.05                    # 20 Hz scheduler
    sim_time = 0.0
    last_sync_emit = 0.0
    wall_start = time.time()
    deadline = wall_start + scenario.max_duration_s

    try:
        while True:
            for c in components:
                c.step(sim_time, wall_dt=sim_dt)

            # Sync-событие раз в секунду
            if sim_time - last_sync_emit >= 1.0:
                logger.emit(
                    "sync",
                    sim_time=sim_time,
                    gazebo_time=sim_time,
                    ns3_time=sim_time,
                    real_time_factor=1.0,
                    position_desync_m=0.0,
                )
                last_sync_emit = sim_time

            if flight.is_complete:
                logger.emit("scenario", sim_time=sim_time, status="success", reason="mission_landed")
                break

            if time.time() > deadline:
                logger.emit("scenario", sim_time=sim_time, status="timeout", reason="max_duration_s")
                break

            sim_time += sim_dt
    finally:
        for c in components:
            c.stop()
