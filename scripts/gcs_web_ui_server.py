#!/usr/bin/env python3
"""Live web GCS for Stage 2.4.

The UI is an operator surface around MAVProxy. Commands are still typed into
MAVProxy stdin, so the flight path remains:

    browser -> this web UI -> MAVProxy CLI -> ns-3 control -> mavbridge -> SITL
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request
import urllib.error

# FPV upstream: TCP server bas-fpv-mjpeg внутри bas-uav netns, доступен с хоста
# через control veth (10.10.0.2). Конфигурируется через env переменные, чтобы
# при изменении топологии (например, FPV в другом netns) не править код.
FPV_UPSTREAM_HOST = os.environ.get("BAS_FPV_UPSTREAM_HOST", "10.10.0.2")
FPV_UPSTREAM_PORT = int(os.environ.get("BAS_FPV_UPSTREAM_PORT", "8766"))
FPV_BOUNDARY = os.environ.get("BAS_FPV_BOUNDARY", "spionkop")
FPV_CONNECT_TIMEOUT_S = float(os.environ.get("BAS_FPV_CONNECT_TIMEOUT_S", "3.0"))
FPV_CHUNK_BYTES = 4096

from mavproxy_stage_2_4_driver import (
    ARM_COMMAND,
    DEFAULT_CONFIG,
    DEFAULT_MASTER,
    DEFAULT_MAVPROXY,
    FORCE_ARM_COMMAND,
    GUIDED_COMMAND,
    LAND_COMMAND,
    STATUS_POLL_COMMAND,
    STOP_MOVE_COMMAND,
    TAKEOFF_COMMAND_TEMPLATE,
    WATCH_COMMAND,
    EventLog,
    MavproxySession,
    TelemetryState,
    build_env,
    build_mavproxy_command,
    prepare_runtime_dirs,
    short_hash,
    write_command_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "web/gcs"

# Deep integration C: CV-детекты из ИССГР на карте пульта. ISSGR_URL задаётся
# через --issgr-url; если пусто — слой детектов просто не показывается.
ISSGR_URL: str | None = None
DETECTION_SENSOR_TYPE = "camera_object_detection"
# Origin двойника по умолчанию (как в gcs_to_issgr_publisher и cv_detector),
# на случай demo-режима без захвата home по GPS.
DEFAULT_ORIGIN_LAT = -35.363262
DEFAULT_ORIGIN_LON = 149.165237


def fetch_issgr(path: str, timeout: float = 3.0) -> dict[str, Any]:
    """GET ISSGR base + path → JSON. {} on error (слой детектов просто пуст)."""
    if not ISSGR_URL:
        return {}
    url = ISSGR_URL.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return {}


# Deep integration D: кибер-алерты от cyber_defense_monitor (NDJSON-файл).
# Пульт показывает их живьём баннером. Файл общий с витриной (Admin),
# путь задаётся BAS_CYBER_ALERTS (demo-обёртка ставит автоматически).
CYBER_ALERTS_PATH = os.environ.get("BAS_CYBER_ALERTS", "/tmp/bas_cyber_alerts.jsonl")
CYBER_ALERT_WINDOW_S = 90.0


def tail_cyber_alerts(window_s: float = CYBER_ALERT_WINDOW_S,
                      limit: int = 20) -> list[dict[str, Any]]:
    """Свежие алерты из NDJSON (cyber_defense_monitor --log-file), новые сверху."""
    path = Path(CYBER_ALERTS_PATH)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    now = time.time()
    out: list[dict[str, Any]] = []
    for line in lines[-300:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if now - float(rec.get("ts", 0.0)) > window_s:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("ts", 0.0), reverse=True)
    return out[:limit]


RF_GCS = {
    "north": -60.0,
    "east": 0.0,
    "height_m": 1.5,
}

# Gazebo/SDF poses are ENU: x=east, y=north, z=up. The Web GCS, MAVLink
# LOCAL_POSITION_NED and RF model use local NED: north/east/down. Keep the
# catalog below in NED after transposing SDF x/y, otherwise the radar overlay
# is rotated relative to the real Gazebo scene.
RF_OBSTACLES_BASIC = [
    {"id": "hangar_blocker", "name": "Hangar",
     "north": 0.0, "east": 45.0,
     "size_north_m": 32.0, "size_east_m": 20.0,
     "height_m": 18.0, "material": "metal"},
    {"id": "control_tower", "name": "Tower",
     "north": 32.0, "east": 82.0,
     "size_north_m": 9.0, "size_east_m": 9.0,
     "height_m": 24.0, "material": "concrete"},
]

# Stage 3 Urban scene — синхронизирован с gazebo/worlds/iris_runway_urban.sdf.
# Координаты в local NED (north/east метры от UAV origin), уже после ENU→NED
# transpose из SDF pose/size.
RF_OBSTACLES_URBAN = [
    # Existing RF demo obstacles (back-compat).
    *RF_OBSTACLES_BASIC,
    # Urban buildings.
    {"id": "bldg_office_tower", "name": "Office tower",
     "north": -40.0, "east": 60.0,
     "size_north_m": 15.0, "size_east_m": 15.0,
     "height_m": 40.0, "material": "concrete"},
    {"id": "bldg_apartment", "name": "Apartment block",
     "north": 40.0, "east": 60.0,
     "size_north_m": 15.0, "size_east_m": 25.0,
     "height_m": 25.0, "material": "brick"},
    {"id": "bldg_warehouse", "name": "Warehouse",
     "north": 0.0, "east": 100.0,
     "size_north_m": 40.0, "size_east_m": 30.0,
     "height_m": 12.0, "material": "metal"},
    {"id": "bldg_residential", "name": "Residential tower",
     "north": -50.0, "east": 120.0,
     "size_north_m": 12.0, "size_east_m": 12.0,
     "height_m": 60.0, "material": "concrete"},
    {"id": "bldg_mall", "name": "Mall",
     "north": 50.0, "east": 140.0,
     "size_north_m": 30.0, "size_east_m": 50.0,
     "height_m": 15.0, "material": "brick"},
    {"id": "bldg_commercial_1", "name": "Commercial",
     "north": 60.0, "east": 30.0,
     "size_north_m": 10.0, "size_east_m": 18.0,
     "height_m": 8.0, "material": "concrete"},
]

# Profile selector через env. По умолчанию — basic (back-compat для
# существующих RF demo wrappers).
_PROFILE = os.environ.get("BAS_RF_OBSTACLE_PROFILE", "basic").strip().lower()
RF_OBSTACLES = (
    RF_OBSTACLES_URBAN if _PROFILE == "urban" else RF_OBSTACLES_BASIC
)

MAV_FRAME_LOCAL_NED = 1
POSITION_TARGET_LOCAL_NED_POS_ONLY_MASK = 3576
DEFAULT_TARGET_SYSTEM = 1
DEFAULT_TARGET_COMPONENT = 0


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def segment_rect_clip(
    p0: tuple[float, float],
    p1: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    """Liang-Barsky clipping for a segment against an axis-aligned rectangle."""
    x0, y0 = p0
    x1, y1 = p1
    xmin, ymin, xmax, ymax = rect
    dx = x1 - x0
    dy = y1 - y0
    t0 = 0.0
    t1 = 1.0
    for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
        if abs(p) < 1e-9:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            t0 = max(t0, r)
        else:
            if r < t0:
                return None
            t1 = min(t1, r)
    return (t0, t1)


class OperatorController:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.log_dir = Path(args.log_dir).resolve()
        self.state = TelemetryState()
        self.session: MavproxySession | None = None
        self.events: EventLog | None = None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.recent_events: list[dict[str, Any]] = []
        self.target_ne: tuple[float, float] | None = None
        self.goto_active = False
        self.demo_position = [0.0, 0.0]
        self.demo_velocity = [0.0, 0.0]
        self.demo_armed = False
        self.demo_mode = "STANDBY"
        self.demo_alt = 0.0
        self.config_hash = ""
        self.mavproxy_command: list[str] = []
        self.command_lock = threading.RLock()
        self.last_rf_status: str | None = None
        self.last_rf_rssi: float | None = None
        self.goto_altitude_m: float | None = None
        # Home reference captured at the first GPS fix BEFORE arming.
        # Used to derive local NED from GLOBAL_POSITION_INT when the
        # SITL never publishes LOCAL_POSITION_NED (ArduCopter default
        # streamrate doesn't include it). Without this fallback the
        # operator drone marker on the SVG map stays frozen at origin.
        self.home_lat: float | None = None
        self.home_lon: float | None = None

    def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events = EventLog(self.log_dir / "events.jsonl", self.args.run_id)

        if self.args.demo:
            self.config_hash = "demo"
            self.record_event("ui_started", mode="demo", url=self.public_url)
        else:
            log_dir, aircraft_dir, home_dir, config_hash = prepare_runtime_dirs(self.args)
            self.log_dir = log_dir
            self.config_hash = config_hash
            command = build_mavproxy_command(self.args, aircraft_dir)
            if self.args.netns:
                command = ["ip", "netns", "exec", self.args.netns, *command]
            self.mavproxy_command = command
            write_command_manifest(log_dir / "mavproxy_command.json", command, self.args, config_hash)
            self.session = MavproxySession(
                command=command,
                env=build_env(home_dir),
                cwd=REPO_ROOT,
                stdout_log=log_dir / "mavproxy_stdout.log",
                state=self.state,
                mirror_stdout=False,
            )
            self.session.start()
            self.record_event(
                "mavproxy_started",
                pid=self.session.process.pid if self.session.process else None,
                command=command,
                master=self.args.master,
                netns=self.args.netns,
            )
            self.send_raw(WATCH_COMMAND, "watch")
            self.send_raw("link list", "link_list")
            self.send_raw(STATUS_POLL_COMMAND, "status")

        (self.log_dir / "operator_ui_manifest.json").write_text(
            json.dumps(self.manifest(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        threading.Thread(target=self.reader_loop, name="mavproxy-reader", daemon=True).start()
        threading.Thread(target=self.guidance_loop, name="goto-guidance", daemon=True).start()
        if self.args.rf_demo or self.args.rf_channel_path:
            self.write_rf_channel(self.rf_snapshot())
            threading.Thread(target=self.rf_loop, name="rf-channel", daemon=True).start()
        if self.args.demo:
            threading.Thread(target=self.demo_loop, name="demo-telemetry", daemon=True).start()

    @property
    def public_url(self) -> str:
        host = "127.0.0.1" if self.args.host in ("0.0.0.0", "::") else self.args.host
        return f"http://{host}:{self.args.port}/"

    def manifest(self) -> dict[str, Any]:
        return {
            "run_id": self.args.run_id,
            "mode": "demo" if self.args.demo else "live",
            "ui_url": self.public_url,
            "endpoint_chain": "Browser -> Web GCS -> MAVProxy CLI -> ns-3 control -> mavbridge -> SITL",
            "master": self.args.master,
            "netns": self.args.netns,
            "log_dir": str(self.log_dir),
            "mavproxy_command": self.mavproxy_command,
            "mission_upload_used": False,
            "direct_pymavlink_command_path_used": False,
            "mavproxy_command_path_used": True,
            "config_sha256": self.config_hash,
            "rf_demo_enabled": bool(self.args.rf_demo or self.args.rf_channel_path),
            "rf_channel_path": self.args.rf_channel_path,
        }

    def record_event(self, event_type: str, **fields: Any) -> None:
        event = {"ts": utc_now(), "event_type": event_type, **fields}
        with self.lock:
            self.recent_events.append(event)
            self.recent_events = self.recent_events[-160:]
        if self.events:
            self.events.emit(event_type, **fields)

    def reader_loop(self) -> None:
        next_poll = time.monotonic() + 1.0
        while not self.stop_event.is_set():
            if self.session:
                self.session.read_available(0.2)
                if not self.session.is_running():
                    self.record_event("mavproxy_exited")
                    return
                if time.monotonic() >= next_poll:
                    self.send_raw(STATUS_POLL_COMMAND, "telemetry_poll", quiet=True)
                    next_poll = time.monotonic() + 2.0
            else:
                time.sleep(0.2)

    def demo_loop(self) -> None:
        last = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            dt_s = now - last
            last = now
            self.demo_position[0] += self.demo_velocity[0] * dt_s
            self.demo_position[1] += self.demo_velocity[1] * dt_s
            if self.demo_mode == "TAKEOFF":
                self.demo_alt = min(float(self.args.takeoff_alt), self.demo_alt + 2.2 * dt_s)
                if self.demo_alt >= float(self.args.takeoff_alt) - 0.2:
                    self.demo_mode = "GUIDED"
            elif self.demo_mode == "LAND":
                self.demo_alt = max(0.0, self.demo_alt - 1.7 * dt_s)
                if self.demo_alt <= 0.1:
                    self.demo_armed = False
                    self.demo_mode = "LANDED"
            with self.lock:
                self.state.connected = True
                self.state.heartbeat_visible = True
                self.state.gps_visible = True
                self.state.gps_fix_ok = True
                self.state.position_visible = True
                self.state.autopilot_ready = True
                self.state.mode_visible = True
                self.state.arm_state_visible = True
                self.state.current_mode = self.demo_mode
                self.state.current_armed = self.demo_armed
                self.state.last_relative_alt_m = self.demo_alt
                self.state.max_relative_alt_m = max(self.state.max_relative_alt_m or 0.0, self.demo_alt)
                self.state.last_local_xy = (self.demo_position[0], self.demo_position[1])
                self.state.last_groundspeed_mps = math.hypot(*self.demo_velocity)
            time.sleep(0.2)

    def send_raw(self, command: str, name: str, quiet: bool = False) -> None:
        with self.lock:
            if self.args.demo:
                self.apply_demo_command(command)
            elif not self.session or not self.session.is_running():
                raise RuntimeError("MAVProxy session is not running")
            else:
                self.session.send(command)
        if not quiet:
            self.record_event("ui_command_sent", command_name=name, command=command)

    def wait_for(self, predicate, timeout: float, poll_command: str | None = STATUS_POLL_COMMAND) -> bool:
        deadline = time.monotonic() + timeout
        next_poll = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if predicate():
                return True
            if poll_command and time.monotonic() >= next_poll:
                try:
                    self.send_raw(poll_command, "wait_poll", quiet=True)
                except Exception:
                    pass
                next_poll = time.monotonic() + 1.0
            time.sleep(0.15)
        return predicate()

    def is_airborne(self) -> bool:
        alt = self.state.last_relative_alt_m or 0.0
        return bool(self.state.current_armed) and alt >= 1.0 and self.state.current_mode not in {"LAND", "LANDED"}

    def ensure_guided(self) -> None:
        if self.state.current_mode == "GUIDED":
            return
        self.send_raw(GUIDED_COMMAND, "guided_auto")
        self.wait_for(lambda: self.state.current_mode == "GUIDED", timeout=4.0)

    def ensure_armed(self) -> None:
        if self.state.current_armed is True:
            return
        # SITL без RC всегда заваливает pre-arm checks обычным ARM;
        # для повторного takeoff после landing особенно. Стартуем с
        # force-arm если флаг включён (run_stage_2_4_operator_ui.sh
        # ставит BAS_STAGE24_FORCE_ARM=1 по умолчанию), затем fallback
        # на обычный ARM как diagnostic.
        if self.args.force_arm:
            self.send_raw(FORCE_ARM_COMMAND, "force_arm_auto")
        else:
            self.send_raw(ARM_COMMAND, "arm_auto")
        if self.wait_for(lambda: self.state.current_armed is True, timeout=6.0):
            return
        # Fallback: если обычный ARM не прошёл за 6s, пробуем force-arm
        # (даже если флаг был False). Полезно когда оператор забыл
        # включить BAS_STAGE24_FORCE_ARM=1.
        if not self.args.force_arm:
            self.send_raw(FORCE_ARM_COMMAND, "force_arm_fallback")
            self.wait_for(lambda: self.state.current_armed is True, timeout=6.0)

    def ensure_airborne(self, altitude: float) -> None:
        self.ensure_guided()
        self.ensure_armed()
        if self.is_airborne():
            return
        self.send_raw(TAKEOFF_COMMAND_TEMPLATE.format(alt=altitude), "takeoff_auto")
        threshold = max(1.0, min(2.0, altitude * 0.2))
        self.wait_for(lambda: (self.state.last_relative_alt_m or 0.0) >= threshold, timeout=18.0)

    def apply_demo_command(self, command: str) -> None:
        if command == GUIDED_COMMAND:
            self.demo_mode = "GUIDED"
        elif command in (ARM_COMMAND, FORCE_ARM_COMMAND):
            self.demo_armed = True
        elif command.startswith("takeoff"):
            self.demo_mode = "TAKEOFF"
            self.demo_armed = True
        elif command == LAND_COMMAND:
            self.demo_mode = "LAND"
            self.demo_velocity = [0.0, 0.0]
        elif command == "disarm":
            self.demo_armed = False
        elif command.startswith("velocity"):
            parts = command.split()
            if len(parts) >= 4:
                self.demo_velocity = [float(parts[1]), float(parts[2])]
        elif command.startswith("message SET_POSITION_TARGET_LOCAL_NED"):
            parts = command.split()
            if len(parts) >= 10:
                target_north = float(parts[7])
                target_east = float(parts[8])
                dx = target_north - self.demo_position[0]
                dy = target_east - self.demo_position[1]
                dist = math.hypot(dx, dy)
                if dist <= self.args.goto_tolerance:
                    self.demo_velocity = [0.0, 0.0]
                else:
                    speed = min(self.args.goto_speed, dist)
                    self.demo_velocity = [dx / dist * speed, dy / dist * speed]

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", ""))
        speed = clamp(float(payload.get("speed", self.args.default_speed)), 0.2, 8.0)
        altitude = clamp(float(payload.get("altitude", self.args.takeoff_alt)), 2.0, 80.0)
        movement_actions = {"north", "south", "east", "west", "up", "down"}
        moves = {
            "north": f"velocity {speed:.2f} 0 0",
            "south": f"velocity {-speed:.2f} 0 0",
            "east": f"velocity 0 {speed:.2f} 0",
            "west": f"velocity 0 {-speed:.2f} 0",
            "up": f"velocity 0 0 {-speed:.2f}",
            "down": f"velocity 0 0 {speed:.2f}",
        }
        commands = {
            "guided": GUIDED_COMMAND,
            "arm": ARM_COMMAND,
            "force_arm": FORCE_ARM_COMMAND,
            "takeoff": TAKEOFF_COMMAND_TEMPLATE.format(alt=altitude),
            "land": LAND_COMMAND,
            "disarm": "disarm",
            "stop": STOP_MOVE_COMMAND,
            **moves,
        }
        if action not in commands:
            raise ValueError(f"Unknown command: {action}")
        with self.command_lock:
            if action == "takeoff":
                self.goto_active = False
                self.goto_altitude_m = None
                self.ensure_airborne(altitude)
            elif action == "arm":
                self.ensure_guided()
                self.ensure_armed()
            elif action in movement_actions:
                self.goto_active = False
                # Auto-takeoff: если не в воздухе, поднимем сами до
                # takeoff_alt и потом сразу выполним velocity-команду.
                # Без этого оператор жмёт WASD и ничего не происходит
                # (исходный backend бросал RuntimeError, frontend глотал
                # в console). UX-задача: однократное нажатие WASD само
                # подготавливает дрон к полёту.
                if not self.is_airborne():
                    self.ensure_airborne(altitude)
                self.ensure_guided()
                self.send_raw(commands[action], action)
            else:
                self.goto_active = False
                if action in {"stop", "land", "disarm"}:
                    self.goto_altitude_m = None
                self.send_raw(commands[action], action)
                if action in {"land", "disarm"}:
                    self.goto_active = False
        return {"ok": True, "action": action, "command": commands[action]}

    def handle_goto(self, payload: dict[str, Any]) -> dict[str, Any]:
        north = clamp(float(payload["north"]), -250.0, 250.0)
        east = clamp(float(payload["east"]), -250.0, 250.0)
        altitude = clamp(float(payload.get("altitude", self.args.takeoff_alt)), 2.0, 80.0)
        with self.command_lock:
            if not self.is_airborne():
                self.ensure_airborne(altitude)
            self.ensure_guided()
            with self.lock:
                self.target_ne = (north, east)
                self.goto_altitude_m = altitude
                self.goto_active = True
            self.record_event(
                "ui_goto_target_set",
                north_m=north,
                east_m=east,
                altitude_m=altitude,
                command_name="goto_local_position",
                command=self.goto_local_command(north, east, altitude),
            )
        return {"ok": True, "target": {"north": north, "east": east}}

    def goto_local_command(self, north: float, east: float, altitude_m: float) -> str:
        # MAVProxy's built-in `velocity`/`position` commands use BODY_NED.
        # For map GO TO we need an absolute local NED target, still delivered
        # through MAVProxy stdin via the `message` module.
        z_down = -abs(altitude_m)
        return (
            "message SET_POSITION_TARGET_LOCAL_NED "
            f"0 {DEFAULT_TARGET_SYSTEM} {DEFAULT_TARGET_COMPONENT} "
            f"{MAV_FRAME_LOCAL_NED} {POSITION_TARGET_LOCAL_NED_POS_ONLY_MASK} "
            f"{north:.2f} {east:.2f} {z_down:.2f} "
            "0 0 0 0 0 0 0 0"
        )

    def guidance_loop(self) -> None:
        while not self.stop_event.is_set():
            target = self.target_ne
            active = self.goto_active
            xy = self._derive_local_ne()
            altitude_m = self.goto_altitude_m or self.args.takeoff_alt
            if active and target and xy:
                if not self.is_airborne() or self.state.current_mode != "GUIDED":
                    time.sleep(0.75)
                    continue
                dx = target[0] - xy[0]
                dy = target[1] - xy[1]
                dist = math.hypot(dx, dy)
                if dist <= self.args.goto_tolerance:
                    self.goto_active = False
                    try:
                        self.send_raw(STOP_MOVE_COMMAND, "goto_stop")
                    except Exception as exc:
                        self.record_event("ui_command_error", command_name="goto_stop", error=str(exc))
                    self.record_event("ui_goto_reached", north_m=target[0], east_m=target[1], distance_m=dist)
                else:
                    try:
                        command = self.goto_local_command(target[0], target[1], altitude_m)
                        self.send_raw(command, "goto_local_position", quiet=True)
                    except Exception as exc:
                        self.record_event("ui_command_error", command_name="goto_local_position", error=str(exc))
            time.sleep(0.75)

    def rf_loop(self) -> None:
        while not self.stop_event.is_set():
            rf = self.rf_snapshot()
            self.write_rf_channel(rf)
            if rf.get("enabled") and rf.get("rssi_dbm") is not None:
                status = str(rf.get("status"))
                rssi = float(rf.get("rssi_dbm"))
                should_emit = status != self.last_rf_status
                if self.last_rf_rssi is None or abs(rssi - self.last_rf_rssi) >= 4.0:
                    should_emit = True
                if should_emit:
                    self.record_event(
                        "rf_link_state",
                        status=status,
                        los=rf.get("los"),
                        blocker=rf.get("blocker"),
                        rssi_dbm=round(rssi, 1),
                        loss_ratio=round(float(rf.get("loss_ratio", 0.0)), 3),
                        extra_delay_ms=round(float(rf.get("extra_delay_ms", 0.0)), 1),
                    )
                    self.last_rf_status = status
                    self.last_rf_rssi = rssi
            time.sleep(0.25)

    def write_rf_channel(self, rf: dict[str, Any]) -> None:
        if not self.args.rf_channel_path:
            return
        payload = {
            "wall_time": time.time(),
            "rf_demo": bool(rf.get("enabled")),
            "los": bool(rf.get("los", True)),
            "status": rf.get("status", "LOS"),
            "rssi_db": rf.get("rssi_dbm", -55.0),
            "rss_db": rf.get("rssi_dbm", -55.0),
            "path_loss_db": rf.get("path_loss_db", 75.0),
            "loss_ratio": rf.get("loss_ratio", 0.0),
            "extra_delay_ms": rf.get("extra_delay_ms", 0.0),
            "blocker": rf.get("blocker"),
        }
        path = Path(self.args.rf_channel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp_path.replace(path)

    def compute_rf_link(self, xy: tuple[float, float] | None) -> dict[str, Any]:
        enabled = bool(self.args.rf_demo or self.args.rf_channel_path)
        base = {
            "enabled": enabled,
            "gcs": RF_GCS,
            "obstacles": RF_OBSTACLES,
            "channel_path": self.args.rf_channel_path,
        }
        if not enabled or xy is None:
            return {
                **base,
                "los": True,
                "status": "LOS",
                "blocker": None,
                "distance_m": None,
                "rssi_dbm": None,
                "path_loss_db": None,
                "loss_ratio": 0.0,
                "extra_delay_ms": 0.0,
            }

        north, east = xy
        alt_m = max(float(self.state.last_relative_alt_m or 0.0), 0.0)
        g_n = float(RF_GCS["north"])
        g_e = float(RF_GCS["east"])
        g_h = float(RF_GCS["height_m"])
        dn = north - g_n
        de = east - g_e
        dz = alt_m - g_h
        distance_m = max(math.sqrt(dn * dn + de * de + dz * dz), 1.0)

        blocker: str | None = None
        for obstacle in RF_OBSTACLES:
            half_n = float(obstacle["size_north_m"]) / 2.0
            half_e = float(obstacle["size_east_m"]) / 2.0
            rect = (
                float(obstacle["north"]) - half_n,
                float(obstacle["east"]) - half_e,
                float(obstacle["north"]) + half_n,
                float(obstacle["east"]) + half_e,
            )
            clip = segment_rect_clip((g_n, g_e), (north, east), rect)
            if clip is None:
                continue
            t_mid = (clip[0] + clip[1]) / 2.0
            ray_height = g_h + t_mid * dz
            if ray_height <= float(obstacle["height_m"]):
                blocker = str(obstacle["id"])
                break

        path_loss_db = 40.05 + 20.0 * math.log10(distance_m)
        if blocker:
            path_loss_db += 28.0
        rssi_dbm = 20.0 - path_loss_db
        loss_ratio = 1.0 / (1.0 + math.exp(0.26 * (rssi_dbm + 86.0)))
        if not blocker:
            loss_ratio *= 0.08
        loss_ratio = clamp(loss_ratio, 0.0, 0.98)
        extra_delay_ms = clamp(loss_ratio * 180.0, 0.0, 180.0)

        return {
            **base,
            "los": blocker is None,
            "status": "LOS" if blocker is None else "NLOS",
            "blocker": blocker,
            "distance_m": distance_m,
            "rssi_dbm": rssi_dbm,
            "path_loss_db": path_loss_db,
            "loss_ratio": loss_ratio,
            "extra_delay_ms": extra_delay_ms,
        }

    def rf_snapshot(self) -> dict[str, Any]:
        return self.compute_rf_link(self._derive_local_ne())

    def _derive_local_ne(self) -> tuple[float, float] | None:
        """Fallback: вычислить local NED из GPS lat/lon относительно home.

        SITL ArduCopter не публикует LOCAL_POSITION_NED со standard
        streamrate, поэтому без этого fallback SVG-маркер дрона на UI
        остаётся в (0, 0) даже когда GPS уже двигается.
        """
        xy = self.state.last_local_xy
        if xy is not None:
            return xy
        lat = self.state.last_lat_deg
        lon = self.state.last_lon_deg
        if lat is None or lon is None:
            return None
        # Захватываем home при первом GPS fix ДО arm — иначе если
        # зафиксировать в воздухе, local NED будет начинаться от
        # текущей позиции, а не от точки старта.
        if self.home_lat is None and self.state.current_armed is not True:
            self.home_lat = lat
            self.home_lon = lon
            self.record_event("ui_home_captured", lat=lat, lon=lon)
        if self.home_lat is None:
            return None
        # Плоское приближение: 1° lat ≈ 111319.9 м,
        # 1° lon @ lat ≈ 111319.9 × cos(lat) м.
        deg2m_lat = 111319.9
        deg2m_lon = 111319.9 * max(math.cos(math.radians(self.home_lat)), 0.01)
        north_m = (lat - self.home_lat) * deg2m_lat
        east_m = (lon - self.home_lon) * deg2m_lon
        return (north_m, east_m)

    def snapshot(self) -> dict[str, Any]:
        xy = self._derive_local_ne()
        target = self.target_ne
        with self.lock:
            events = list(self.recent_events[-80:])
        return {
            "run_id": self.args.run_id,
            "mode": "demo" if self.args.demo else "live",
            "connected": self.state.connected,
            "heartbeat_visible": self.state.heartbeat_visible,
            "gps_fix_ok": self.state.gps_fix_ok,
            "autopilot_ready": self.state.autopilot_ready,
            "current_mode": self.state.current_mode,
            "armed": self.state.current_armed,
            "altitude_m": self.state.last_relative_alt_m,
            "max_altitude_m": self.state.max_relative_alt_m,
            "groundspeed_mps": self.state.last_groundspeed_mps,
            "heading_deg": self.state.last_heading_deg,
            "lat_deg": self.state.last_lat_deg,
            "lon_deg": self.state.last_lon_deg,
            "home_lat": self.home_lat,
            "home_lon": self.home_lon,
            "local": {"north": xy[0], "east": xy[1]} if xy else None,
            "local_source": "ned" if self.state.last_local_xy is not None else ("derived" if xy else None),
            "rf": self.compute_rf_link(xy),
            "target": {"north": target[0], "east": target[1], "active": self.goto_active} if target else None,
            "log_dir": str(self.log_dir),
            "endpoint_chain": "Web GCS -> MAVProxy CLI -> ns-3 control -> mavbridge -> SITL",
            "ui_url": self.public_url,
            "events": events,
            "mavproxy_running": bool(self.session and self.session.is_running()) if not self.args.demo else True,
        }

    def close(self) -> None:
        self.stop_event.set()
        self.write_report()
        if self.session:
            self.session.terminate()
        if self.events:
            self.events.close()

    def write_report(self) -> None:
        snapshot = self.snapshot()
        commands = [e for e in snapshot["events"] if e.get("event_type") in {"ui_command_sent", "ui_goto_target_set", "ui_goto_reached"}]
        lines = [
            "# Stage 2.4 Operator UI Report",
            "",
            "## Live GCS demo",
            "",
            "- GCS type: Web operator console + MAVProxy CLI backend",
            f"- UI URL: `{snapshot['ui_url']}`",
            f"- Endpoint chain: {snapshot['endpoint_chain']}",
            f"- MAVProxy endpoint: `{self.args.master}`",
            "- MAVProxy command path used: true",
            "- Direct pymavlink command path used: false",
            "- Mission upload used: false",
            f"- Run ID: `{self.args.run_id}`",
            f"- Final mode: `{snapshot.get('current_mode')}`",
            f"- Armed: {str(snapshot.get('armed')).lower()}",
            f"- Altitude, m: {snapshot.get('altitude_m')}",
            f"- Local position: {snapshot.get('local')}",
            f"- Target: {snapshot.get('target')}",
            f"- RF demo enabled: {str(bool(snapshot.get('rf', {}).get('enabled'))).lower()}",
            f"- RF status: `{snapshot.get('rf', {}).get('status')}`",
            f"- RF RSSI, dBm: {snapshot.get('rf', {}).get('rssi_dbm')}",
            f"- RF loss ratio: {snapshot.get('rf', {}).get('loss_ratio')}",
            "",
            "## Operator events",
            "",
        ]
        if commands:
            for event in commands[-80:]:
                label = event.get("command_name") or event.get("event_type")
                command = event.get("command", "")
                suffix = f" - `{command}`" if command else ""
                lines.append(f"- {event.get('ts', '')} {label}{suffix}")
        else:
            lines.append("- No operator commands recorded.")
        lines.append("")
        (self.log_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


class GcsHandler(BaseHTTPRequestHandler):
    server_version = "BASGCS/0.1"

    @property
    def controller(self) -> OperatorController:
        return self.server.controller  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[gcs-ui] " + fmt % args + "\n")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self.send_json(self.controller.snapshot())
            return
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "url": self.controller.public_url})
            return
        if parsed.path == "/api/fpv":
            self.send_json(self._probe_fpv())
            return
        if parsed.path == "/api/detections":
            self.send_json(self._detections())
            return
        if parsed.path == "/api/alerts":
            alerts = tail_cyber_alerts()
            self.send_json({"ok": True, "count": len(alerts), "alerts": alerts})
            return
        if parsed.path == "/camera.mjpg":
            self._proxy_fpv()
            return
        rel = "index.html" if parsed.path in ("", "/") else parsed.path.lstrip("/")
        path = (STATIC_DIR / rel).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists() or path.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/api/command":
                response = self.controller.handle_command(payload)
            elif self.path == "/api/goto":
                response = self.controller.handle_goto(payload)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_json(response)
        except Exception as exc:
            self.controller.record_event("ui_command_error", error=str(exc))
            self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _detections(self) -> dict[str, Any]:
        """CV-детекты из ИССГР → local NED относительно home.

        Дрон видит объекты камерой → cv_detector geo-tag-ит их в ИССГР
        (sensor_readings, camera_object_detection, value.ground_lat/lon).
        Здесь читаем их и переводим в NED, чтобы пульт нарисовал метки на
        своей тактической карте — оператор видит то же, что и витрина.
        """
        if not ISSGR_URL:
            return {"ok": True, "has_issgr": False, "count": 0, "detections": []}
        ctrl = self.controller
        home_lat = ctrl.home_lat if ctrl.home_lat is not None else DEFAULT_ORIGIN_LAT
        home_lon = ctrl.home_lon if ctrl.home_lon is not None else DEFAULT_ORIGIN_LON
        fc = fetch_issgr("/collections/sensor_readings/items?limit=500")
        deg2m_lat = 111319.9
        deg2m_lon = 111319.9 * max(math.cos(math.radians(home_lat)), 0.01)
        items: list[dict[str, Any]] = []
        for feat in fc.get("features", []):
            props = feat.get("properties", {})
            if props.get("sensor_type") != DETECTION_SENSOR_TYPE:
                continue
            val = props.get("value", {})
            glat = val.get("ground_lat")
            glon = val.get("ground_lon")
            if glat is None or glon is None:
                continue
            items.append({
                "class": val.get("class_name", "?"),
                "confidence": round(float(val.get("confidence", 0.0)), 2),
                "lat": glat,
                "lon": glon,
                "north": round((glat - home_lat) * deg2m_lat, 1),
                "east": round((glon - home_lon) * deg2m_lon, 1),
                "ts": props.get("timestamp", ""),
            })
        return {"ok": True, "has_issgr": True, "count": len(items), "detections": items}

    def _probe_fpv(self) -> dict[str, Any]:
        """Лёгкая проверка: открывается ли TCP-сокет на bas-fpv-mjpeg.

        Frontend дёргает /api/fpv до первого <img>, чтобы понять, есть ли
        смысл показывать видео-блок. Без отдельного probe браузер мог бы
        долго ретраить <img src="/camera.mjpg"> и получать ECONNREFUSED
        в консоли каждый раз — некрасиво.
        """
        try:
            with socket.create_connection(
                (FPV_UPSTREAM_HOST, FPV_UPSTREAM_PORT),
                timeout=FPV_CONNECT_TIMEOUT_S,
            ) as probe:
                probe.settimeout(0.5)
                # Не читаем — multipartmux всё равно сразу шлёт boundary,
                # просто факт connection success достаточен.
            return {
                "ok": True,
                "upstream": f"{FPV_UPSTREAM_HOST}:{FPV_UPSTREAM_PORT}",
            }
        except OSError as exc:
            return {
                "ok": False,
                "upstream": f"{FPV_UPSTREAM_HOST}:{FPV_UPSTREAM_PORT}",
                "error": str(exc),
            }

    def _proxy_fpv(self) -> None:
        """TCP-проксирование multipart MJPEG из bas-fpv-mjpeg в браузер.

        gst tcpserversink + multipartmux уже отдаёт корректный поток
        частей `--<boundary>\\r\\nContent-Type: image/jpeg\\r\\n\\r\\n<JPEG>\\r\\n`.
        Нам остаётся прислонить правильный HTTP заголовок и копировать
        байты как есть. Без re-encoding и без буферизации (sync=false на
        gst-стороне обеспечивает realtime).
        """
        try:
            upstream = socket.create_connection(
                (FPV_UPSTREAM_HOST, FPV_UPSTREAM_PORT),
                timeout=FPV_CONNECT_TIMEOUT_S,
            )
        except OSError as exc:
            self.send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"FPV stream not available: {exc}",
            )
            return

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace; boundary={FPV_BOUNDARY}",
            )
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            upstream.settimeout(5.0)
            while True:
                chunk = upstream.recv(FPV_CHUNK_BYTES)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Browser закрыл tab/обновил страницу — нормально, выход.
                    break
        except (OSError, socket.timeout):
            # Upstream gst упал или потерял источник — выйти молча.
            pass
        finally:
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            upstream.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage 2.4 live web GCS")
    parser.add_argument("--demo", action="store_true", help="serve UI with simulated telemetry; no MAVProxy")
    parser.add_argument("--host", default=os.environ.get("BAS_GCS_UI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BAS_GCS_UI_PORT", "8765")))
    parser.add_argument("--netns", default=os.environ.get("BAS_GCS_UI_NETNS", ""))
    parser.add_argument("--run-id", default=os.environ.get("BAS_RUN_ID", f"stage_2_4_gcs_ui_{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}"))
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--master", default=os.environ.get("BAS_STAGE24_MAVPROXY_MASTER", DEFAULT_MASTER))
    parser.add_argument("--mavproxy-bin", type=Path, default=Path(os.environ.get("BAS_MAVPROXY_BIN", str(DEFAULT_MAVPROXY))))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-system", type=int, default=int(os.environ.get("BAS_STAGE24_SOURCE_SYSTEM", "255")))
    parser.add_argument("--source-component", type=int, default=int(os.environ.get("BAS_STAGE24_SOURCE_COMPONENT", "230")))
    parser.add_argument("--streamrate", type=int, default=int(os.environ.get("BAS_STAGE24_STREAMRATE", "10")))
    parser.add_argument("--takeoff-alt", type=float, default=float(os.environ.get("BAS_STAGE24_TAKEOFF_ALT", "10")))
    parser.add_argument("--default-speed", type=float, default=float(os.environ.get("BAS_GCS_UI_SPEED", "2.5")))
    parser.add_argument("--goto-speed", type=float, default=float(os.environ.get("BAS_GCS_UI_GOTO_SPEED", "3.0")))
    parser.add_argument("--goto-tolerance", type=float, default=float(os.environ.get("BAS_GCS_UI_GOTO_TOLERANCE", "2.5")))
    parser.add_argument("--mode", default="ui")
    parser.add_argument("--force-arm", action="store_true", default=os.environ.get("BAS_STAGE24_FORCE_ARM", "0") == "1")
    parser.add_argument("--rf-demo", action="store_true", default=os.environ.get("BAS_GCS_RF_DEMO", "0") == "1")
    parser.add_argument("--rf-channel-path", default=os.environ.get("BAS_RF_CHANNEL_PATH", os.environ.get("BAS_SIONNA_CHANNEL_PATH", "")))
    parser.add_argument("--issgr-url", default=os.environ.get("BAS_ISSGR_URL", ""),
                        help="ИССГР base URL для CV-детектов на карте пульта (deep integration C)")
    args = parser.parse_args(argv)
    if args.log_dir is None:
        args.log_dir = str(REPO_ROOT / "logs" / args.run_id)
    args.mavproxy_bin = args.mavproxy_bin.resolve()
    if not args.demo and not args.mavproxy_bin.exists():
        parser.error(f"MAVProxy binary not found: {args.mavproxy_bin}")
    args.netns = args.netns or ""
    return args


def main(argv: list[str]) -> int:
    global ISSGR_URL
    args = parse_args(argv)
    if args.issgr_url:
        ISSGR_URL = args.issgr_url.rstrip("/")
    if not STATIC_DIR.exists():
        raise FileNotFoundError(f"Static UI directory not found: {STATIC_DIR}")
    controller = OperatorController(args)
    controller.start()
    server = ThreadingHTTPServer((args.host, args.port), GcsHandler)
    server.controller = controller  # type: ignore[attr-defined]
    print(f"Stage 2.4 operator UI: {controller.public_url}", flush=True)
    print(f"logs: {controller.log_dir}", flush=True)

    def request_shutdown(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, name="http-shutdown", daemon=True).start()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
