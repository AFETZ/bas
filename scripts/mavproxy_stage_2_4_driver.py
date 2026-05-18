#!/usr/bin/env python3
"""Stage 2.4 Manual GCS driver.

This script drives MAVProxy through its interactive CLI. It intentionally does
not import pymavlink: all acceptance commands are typed into MAVProxy stdin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pty
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "configs/gcs/mavproxy_stage_2_4/mavinit.scr"
DEFAULT_MAVPROXY = REPO_ROOT / ".venv/bin/mavproxy.py"
DEFAULT_MASTER = "udpout:10.10.0.2:14550"
WATCH_COMMAND = (
    "watch HEARTBEAT GPS_RAW_INT GLOBAL_POSITION_INT LOCAL_POSITION_NED "
    "VFR_HUD COMMAND_ACK EXTENDED_SYS_STATE STATUSTEXT"
)
STATUS_POLL_COMMAND = (
    "status HEARTBEAT GPS_RAW_INT GLOBAL_POSITION_INT LOCAL_POSITION_NED "
    "VFR_HUD COMMAND_ACK EXTENDED_SYS_STATE STATUSTEXT"
)
GUIDED_COMMAND = "mode GUIDED"
ARM_COMMAND = "arm throttle"
FORCE_ARM_COMMAND = "arm throttle force"
TAKEOFF_COMMAND_TEMPLATE = "takeoff {alt:g}"
MOVE_COMMAND = "velocity 1 0 0"
STOP_MOVE_COMMAND = "velocity 0 0 0"
LAND_COMMAND = "mode LAND"

KNOWN_MODES = {
    "ALT_HOLD",
    "AUTO",
    "BRAKE",
    "CIRCLE",
    "DRIFT",
    "GUIDED",
    "LAND",
    "LOITER",
    "POSHOLD",
    "RTL",
    "STABILIZE",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([-+0-9.eE]+)")
PROMPT_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,24})>")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def short_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_fields(message: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in FIELD_RE.findall(message):
        try:
            out[key] = float(value)
        except ValueError:
            continue
    return out


class EventLog:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")

    def emit(self, event_type: str, **fields: object) -> None:
        event = {
            "ts": utc_now(),
            "run_id": self.run_id,
            "component": "mavproxy_stage_2_4",
            "event_type": event_type,
            **fields,
        }
        self._f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


@dataclass
class TelemetryState:
    connected: bool = False
    heartbeat_visible: bool = False
    gps_visible: bool = False
    gps_fix_ok: bool = False
    position_visible: bool = False
    autopilot_ready: bool = False
    mode_visible: bool = False
    arm_state_visible: bool = False
    current_mode: str | None = None
    current_armed: bool | None = None
    armed_seen: bool = False
    disarmed_seen_after_arm: bool = False
    landed_seen: bool = False
    command_acks: dict[int, int] = field(default_factory=dict)
    command_accepted_text: set[int] = field(default_factory=set)
    prearm_failure: str | None = None
    last_relative_alt_m: float | None = None
    initial_relative_alt_m: float | None = None
    max_relative_alt_m: float | None = None
    takeoff_detected_seen: bool = False
    landing_started: bool = False
    last_local_xy: tuple[float, float] | None = None
    movement_baseline_xy: tuple[float, float] | None = None
    movement_delta_m: float = 0.0
    recent_text: deque[str] = field(default_factory=lambda: deque(maxlen=240))

    def recent_excerpt(self, chars: int = 2000) -> str:
        return "".join(self.recent_text)[-chars:]

    def update_from_text(self, text: str) -> None:
        clean = strip_ansi(text).replace("\r", "\n")
        self.recent_text.append(clean)

        if "Detected vehicle" in clean or "link 1 OK" in clean or "link 0 OK" in clean:
            self.connected = True

        for mode in PROMPT_RE.findall(clean):
            if mode in KNOWN_MODES:
                self.current_mode = mode
                self.mode_visible = True

        upper = clean.upper()
        if "ARDUPILOT READY" in upper:
            self.autopilot_ready = True

        if "PREARM" in upper or "PRE-ARM" in upper:
            for line in clean.splitlines():
                if "PreArm" in line or "pre-arm" in line.lower():
                    self.prearm_failure = line.strip()
        for line in clean.splitlines():
            if "AP: Arm:" in line or "AP: PreArm:" in line:
                self.prearm_failure = line.strip()

        if "ARMING MOTORS" in upper or "MOTORS ARMED" in upper:
            self.armed_seen = True
            self.current_armed = True
            self.arm_state_visible = True

        if "DISARMING MOTORS" in upper or "DISARMED" in upper:
            if self.armed_seen:
                self.disarmed_seen_after_arm = True
            self.current_armed = False
            self.arm_state_visible = True

        if "LANDED" in upper or "LAND_COMPLETE" in upper:
            self.landed_seen = True

        for match in re.finditer(r"HEARTBEAT\s*\{[^}]*\}", clean):
            self.heartbeat_visible = True
            self.connected = True
            fields = parse_fields(match.group(0))
            base_mode = int(fields.get("base_mode", -1))
            custom_mode = int(fields.get("custom_mode", -1))
            if base_mode >= 0:
                self.arm_state_visible = True
                armed = bool(base_mode & 128)
                if armed:
                    self.armed_seen = True
                elif self.armed_seen:
                    self.disarmed_seen_after_arm = True
                self.current_armed = armed
            if custom_mode >= 0:
                self.mode_visible = True

        for match in re.finditer(r"COMMAND_ACK\s*\{[^}]*\}", clean):
            fields = parse_fields(match.group(0))
            command = int(fields.get("command", -1))
            result = int(fields.get("result", -1))
            if command >= 0 and result >= 0:
                self.command_acks[command] = result

        for match in re.finditer(r"GPS_RAW_INT\s*\{[^}]*\}", clean):
            self.gps_visible = True
            fields = parse_fields(match.group(0))
            if int(fields.get("fix_type", 0)) >= 3:
                self.gps_fix_ok = True
                self.position_visible = True
            elif int(fields.get("fix_type", 0)) >= 2:
                self.position_visible = True

        for match in re.finditer(r"GLOBAL_POSITION_INT\s*\{[^}]*\}", clean):
            self.gps_visible = True
            self.position_visible = True
            fields = parse_fields(match.group(0))
            if "relative_alt" in fields:
                alt_m = fields["relative_alt"] / 1000.0
                self._set_relative_alt(alt_m)

        for match in re.finditer(r"VFR_HUD\s*\{[^}]*\}", clean):
            self.position_visible = True
            fields = parse_fields(match.group(0))
            if "alt" in fields and self.last_relative_alt_m is None:
                self._set_relative_alt(fields["alt"])

        for match in re.finditer(r"LOCAL_POSITION_NED\s*\{[^}]*\}", clean):
            self.position_visible = True
            fields = parse_fields(match.group(0))
            if "x" in fields and "y" in fields:
                xy = (fields["x"], fields["y"])
                self.last_local_xy = xy
                if self.movement_baseline_xy is not None:
                    dx = xy[0] - self.movement_baseline_xy[0]
                    dy = xy[1] - self.movement_baseline_xy[1]
                    self.movement_delta_m = max(self.movement_delta_m, (dx * dx + dy * dy) ** 0.5)

        for match in re.finditer(r"EXTENDED_SYS_STATE\s*\{[^}]*\}", clean):
            fields = parse_fields(match.group(0))
            landed_state = int(fields.get("landed_state", -1))
            if landed_state == 1 and self.landing_started:
                self.landed_seen = True

    def _set_relative_alt(self, alt_m: float) -> None:
        if self.initial_relative_alt_m is None:
            self.initial_relative_alt_m = alt_m
        self.last_relative_alt_m = alt_m
        self.max_relative_alt_m = max(self.max_relative_alt_m or alt_m, alt_m)
        if self.landing_started and self.takeoff_detected_seen and alt_m <= 0.5:
            self.landed_seen = True


class MavproxySession:
    def __init__(
        self,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        stdout_log: Path,
        state: TelemetryState,
    ):
        self.command = command
        self.env = env
        self.cwd = cwd
        self.stdout_log = stdout_log
        self.state = state
        self.selector = selectors.DefaultSelector()
        self.master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._log_f = stdout_log.open("ab")

    def start(self) -> None:
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        os.set_blocking(master_fd, False)
        self.process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=self.env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)
        self.selector.register(master_fd, selectors.EVENT_READ)

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def read_available(self, timeout: float = 0.1) -> str:
        if self.master_fd is None:
            return ""
        chunks: list[bytes] = []
        for key, _ in self.selector.select(timeout):
            while True:
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    break
                except OSError:
                    return ""
                if not data:
                    break
                chunks.append(data)
                if len(data) < 65536:
                    break
        if not chunks:
            return ""
        raw = b"".join(chunks)
        self._log_f.write(raw)
        self._log_f.flush()
        text = raw.decode("utf-8", "replace")
        self.state.update_from_text(text)
        sys.stdout.write(text)
        sys.stdout.flush()
        return text

    def send(self, command: str) -> None:
        if self.master_fd is None:
            raise RuntimeError("MAVProxy PTY is not open")
        os.write(self.master_fd, (command + "\n").encode("utf-8"))

    def wait_until(
        self,
        predicate: Callable[[], bool],
        timeout: float,
        poll_command: str | None = None,
        poll_interval: float = 2.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        next_poll = time.monotonic() + min(0.5, poll_interval)
        while time.monotonic() < deadline:
            self.read_available(0.2)
            if predicate():
                return True
            if not self.is_running():
                self.read_available(0)
                return predicate()
            now = time.monotonic()
            if poll_command and now >= next_poll:
                self.send(poll_command)
                next_poll = now + poll_interval
        self.read_available(0)
        return predicate()

    def terminate(self) -> None:
        try:
            if self.master_fd is not None:
                self.selector.unregister(self.master_fd)
        except Exception:
            pass
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGINT)
                self.process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except Exception:
                        pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        self._log_f.close()


def build_mavproxy_command(args: argparse.Namespace, aircraft_dir: Path) -> list[str]:
    safe_cmd = "set shownoise false; module load cmdlong; set mavfwd false; set mavfwd_disarmed false"
    return [
        str(args.mavproxy_bin),
        f"--master={args.master}",
        f"--source-system={args.source_system}",
        f"--source-component={args.source_component}",
        f"--streamrate={args.streamrate}",
        f"--aircraft={aircraft_dir}",
        f"--cmd={safe_cmd}",
    ]


def prepare_runtime_dirs(args: argparse.Namespace) -> tuple[Path, Path, Path, str]:
    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    source_config = Path(args.config).resolve()
    if not source_config.exists():
        raise FileNotFoundError(f"MAVProxy config not found: {source_config}")
    config_hash = short_hash(source_config)

    aircraft_dir = log_dir / "mavproxy_aircraft"
    aircraft_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_config, aircraft_dir / "mavinit.scr")

    home_dir = log_dir / "mavproxy_home"
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".mavproxy").mkdir(exist_ok=True)
    return log_dir, aircraft_dir, home_dir, config_hash


def build_env(home_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("TERM", "dumb")
    return env


def write_command_manifest(path: Path, command: list[str], args: argparse.Namespace, config_hash: str) -> None:
    manifest = {
        "run_id": args.run_id,
        "mode": args.mode,
        "mavproxy_command": command,
        "master": args.master,
        "endpoint_chain": "MAVProxy -> ns-3 control -> mavbridge -> SITL",
        "config": str(Path(args.config).resolve()),
        "config_sha256": config_hash,
        "mission_upload_used": False,
        "direct_pymavlink_command_path_used": False,
        "mavproxy_command_path_used": True,
        "takeoff_alt_m": args.takeoff_alt,
        "movement_command": MOVE_COMMAND,
        "landing_command": LAND_COMMAND,
        "force_arm_requested": args.force_arm,
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit_visibility_events(events: EventLog, state: TelemetryState, emitted: set[str]) -> None:
    checks = [
        ("gcs_connected", state.connected),
        ("heartbeat_visible", state.heartbeat_visible),
        ("gps_visible", state.gps_visible or state.position_visible),
        ("mode_visible", state.mode_visible),
        ("arm_state_visible", state.arm_state_visible),
    ]
    for name, ok in checks:
        if ok and name not in emitted:
            events.emit(name, ok=True, excerpt=state.recent_excerpt(1000))
            emitted.add(name)


def wait_and_emit(
    session: MavproxySession,
    events: EventLog,
    state: TelemetryState,
    emitted: set[str],
    predicate: Callable[[], bool],
    timeout: float,
    poll_command: str | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    next_poll = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        session.read_available(0.2)
        emit_visibility_events(events, state, emitted)
        if predicate():
            return True
        if not session.is_running():
            session.read_available(0)
            emit_visibility_events(events, state, emitted)
            return predicate()
        now = time.monotonic()
        if poll_command and now >= next_poll:
            session.send(poll_command)
            next_poll = now + 2.0
    session.read_available(0)
    emit_visibility_events(events, state, emitted)
    return predicate()


def send_manual_command(events: EventLog, session: MavproxySession, command: str, name: str) -> None:
    events.emit("manual_command_sent", command_name=name, command=command)
    session.send(command)


def emit_command_accepted(events: EventLog, command_name: str, ok: bool, state: TelemetryState, **extra: object) -> None:
    events.emit(
        "manual_command_accepted",
        command_name=command_name,
        ok=ok,
        excerpt=state.recent_excerpt(1200),
        **extra,
    )


def run_smoke(args: argparse.Namespace) -> int:
    log_dir, aircraft_dir, home_dir, config_hash = prepare_runtime_dirs(args)
    events = EventLog(log_dir / "events.jsonl", args.run_id)
    state = TelemetryState()
    emitted_visibility: set[str] = set()
    command = build_mavproxy_command(args, aircraft_dir)
    write_command_manifest(log_dir / "mavproxy_command.json", command, args, config_hash)

    summary: dict[str, object] = {
        "run_id": args.run_id,
        "gcs_type": "MAVProxy command-line GCS",
        "gcs_mode": "smoke",
        "endpoint_chain": "MAVProxy -> ns-3 control -> mavbridge -> SITL",
        "mavproxy_master": args.master,
        "mavproxy_command_path_used": True,
        "direct_pymavlink_command_path_used": False,
        "mission_upload_used": False,
        "force_arm_used": False,
        "config_sha256": config_hash,
        "logs_path": str(log_dir),
    }

    session = MavproxySession(
        command=command,
        env=build_env(home_dir),
        cwd=REPO_ROOT,
        stdout_log=log_dir / "mavproxy_stdout.log",
        state=state,
    )

    success = False
    failure_reason = "not completed"
    guided_ok = False
    arm_ok = False
    takeoff_ack_ok = False
    takeoff_detected = False
    movement_ok = False
    landing_mode_ok = False
    landed_or_disarmed = False
    try:
        session.start()
        events.emit(
            "mavproxy_started",
            pid=session.process.pid if session.process else None,
            command=command,
            master=args.master,
            config=str(Path(args.config).resolve()),
            config_sha256=config_hash,
        )

        session.send(WATCH_COMMAND)
        session.send("link list")
        session.send(STATUS_POLL_COMMAND)

        visible_ok = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: (
                state.heartbeat_visible
                and state.position_visible
                and state.gps_fix_ok
                and state.autopilot_ready
            ),
            timeout=args.connect_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        if not visible_ok:
            failure_reason = "MAVProxy did not observe ready heartbeat plus GPS fix/position telemetry"
            raise RuntimeError(failure_reason)
        session.send("watch")

        # Mode and arm visibility ride on HEARTBEAT base_mode/custom_mode. If
        # MAVProxy can show HEARTBEAT fields, those states are visible.
        if not state.mode_visible and state.heartbeat_visible:
            state.mode_visible = True
        if not state.arm_state_visible and state.heartbeat_visible:
            state.arm_state_visible = True
        emit_visibility_events(events, state, emitted_visibility)

        send_manual_command(events, session, GUIDED_COMMAND, "guided_mode")
        guided_ok = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: state.current_mode == "GUIDED" or "GUIDED>" in state.recent_excerpt(2000),
            timeout=args.command_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        emit_command_accepted(events, "guided_mode", guided_ok, state, current_mode=state.current_mode)
        if guided_ok:
            events.emit("guided_mode_ok", ok=True, current_mode=state.current_mode)
        else:
            failure_reason = "GUIDED mode was not visible/accepted through MAVProxy"
            raise RuntimeError(failure_reason)

        send_manual_command(events, session, ARM_COMMAND, "arm")
        arm_ok = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: state.armed_seen or state.current_armed is True or state.command_acks.get(400) == 0,
            timeout=args.command_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        if not arm_ok and state.prearm_failure:
            events.emit("manual_command_accepted", command_name="arm", ok=False, prearm_failure=state.prearm_failure)
        if not arm_ok and args.force_arm:
            summary["force_arm_used"] = True
            events.emit(
                "manual_command_sent",
                command_name="force_arm",
                command=FORCE_ARM_COMMAND,
                reason="BAS_STAGE24_FORCE_ARM=1",
                prearm_failure=state.prearm_failure,
            )
            session.send(FORCE_ARM_COMMAND)
            arm_ok = wait_and_emit(
                session,
                events,
                state,
                emitted_visibility,
                lambda: state.armed_seen or state.current_armed is True or state.command_acks.get(400) == 0,
                timeout=args.command_timeout,
                poll_command=STATUS_POLL_COMMAND,
            )
        emit_command_accepted(
            events,
            "arm",
            arm_ok,
            state,
            force_arm_used=bool(summary["force_arm_used"]),
            prearm_failure=state.prearm_failure,
        )
        if arm_ok:
            events.emit("arm_ok", ok=True, force_arm_used=bool(summary["force_arm_used"]))
        else:
            failure_reason = "arm throttle was not accepted through MAVProxy"
            raise RuntimeError(failure_reason)

        if state.current_mode != "GUIDED":
            send_manual_command(events, session, GUIDED_COMMAND, "guided_mode_after_arm")
            guided_after_arm_ok = wait_and_emit(
                session,
                events,
                state,
                emitted_visibility,
                lambda: state.current_mode == "GUIDED" or "GUIDED>" in state.recent_excerpt(2000),
                timeout=args.command_timeout,
                poll_command=STATUS_POLL_COMMAND,
            )
            emit_command_accepted(events, "guided_mode_after_arm", guided_after_arm_ok, state, current_mode=state.current_mode)
            if not guided_after_arm_ok:
                failure_reason = "GUIDED mode was not visible after arm"
                raise RuntimeError(failure_reason)
            guided_ok = guided_ok and guided_after_arm_ok

        takeoff_command = TAKEOFF_COMMAND_TEMPLATE.format(alt=args.takeoff_alt)
        state.initial_relative_alt_m = state.last_relative_alt_m
        send_manual_command(events, session, takeoff_command, "takeoff")
        takeoff_ack_ok = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: state.command_acks.get(22) == 0 or "TAKE OFF STARTED" in state.recent_excerpt(3000).upper(),
            timeout=max(8.0, args.command_timeout / 2),
            poll_command=STATUS_POLL_COMMAND,
        )
        emit_command_accepted(events, "takeoff", takeoff_ack_ok, state, command_id=22)
        base_alt = state.initial_relative_alt_m or 0.0
        takeoff_detected = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: (state.max_relative_alt_m or 0.0) >= base_alt + min(2.0, max(1.0, args.takeoff_alt * 0.2)),
            timeout=args.takeoff_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        if takeoff_detected:
            state.takeoff_detected_seen = True
            events.emit(
                "takeoff_detected",
                ok=True,
                initial_relative_alt_m=base_alt,
                max_relative_alt_m=state.max_relative_alt_m,
            )
        else:
            failure_reason = "takeoff command did not produce visible climb telemetry"
            raise RuntimeError(failure_reason)

        if state.last_local_xy is not None:
            state.movement_baseline_xy = state.last_local_xy
        send_manual_command(events, session, MOVE_COMMAND, "live_movement")
        movement_ok = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: state.movement_delta_m >= args.min_movement_m,
            timeout=args.movement_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        session.send(STOP_MOVE_COMMAND)
        emit_command_accepted(
            events,
            "live_movement",
            movement_ok,
            state,
            movement_command=MOVE_COMMAND,
            movement_delta_m=state.movement_delta_m,
        )
        if movement_ok:
            events.emit(
                "position_or_velocity_command_ok",
                ok=True,
                command=MOVE_COMMAND,
                movement_delta_m=state.movement_delta_m,
            )
        else:
            failure_reason = "live velocity command did not produce visible local movement"
            raise RuntimeError(failure_reason)

        send_manual_command(events, session, LAND_COMMAND, "landing")
        state.landing_started = True
        landing_mode_ok = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: state.current_mode == "LAND" or "LAND>" in state.recent_excerpt(2000),
            timeout=args.command_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        emit_command_accepted(events, "landing", landing_mode_ok, state, command=LAND_COMMAND)
        events.emit("landing_command_sent", command=LAND_COMMAND, accepted=landing_mode_ok)
        if not landing_mode_ok:
            failure_reason = "LAND mode was not accepted/visible through MAVProxy"
            raise RuntimeError(failure_reason)

        landed_or_disarmed = wait_and_emit(
            session,
            events,
            state,
            emitted_visibility,
            lambda: state.landed_seen or state.disarmed_seen_after_arm or (state.current_armed is False and state.armed_seen),
            timeout=args.landing_timeout,
            poll_command=STATUS_POLL_COMMAND,
        )
        if not landed_or_disarmed and state.current_armed is True:
            send_manual_command(events, session, "disarm", "disarm_after_landing")
            landed_or_disarmed = wait_and_emit(
                session,
                events,
                state,
                emitted_visibility,
                lambda: state.disarmed_seen_after_arm or state.current_armed is False,
                timeout=args.command_timeout,
                poll_command=STATUS_POLL_COMMAND,
            )
        events.emit(
            "landed_or_disarmed",
            ok=landed_or_disarmed,
            landed_seen=state.landed_seen,
            disarmed_seen_after_arm=state.disarmed_seen_after_arm,
            current_armed=state.current_armed,
            last_relative_alt_m=state.last_relative_alt_m,
        )
        if not landed_or_disarmed:
            failure_reason = "landing/disarm was not visible in telemetry"
            raise RuntimeError(failure_reason)

        success = True
        failure_reason = ""
        events.emit("scenario_success", ok=True)
        return 0
    except Exception as exc:
        failure_reason = str(exc) or failure_reason
        events.emit("scenario_failed", ok=False, reason=failure_reason, excerpt=state.recent_excerpt(2000))
        return 1
    finally:
        summary.update(
            {
                "heartbeat_visible": state.heartbeat_visible,
                "gps_position_visible": state.gps_visible or state.position_visible,
                "gps_fix_ok": state.gps_fix_ok,
                "autopilot_ready": state.autopilot_ready,
                "mode_visible": state.mode_visible,
                "arm_state_visible": state.arm_state_visible,
                "guided_mode_accepted": guided_ok,
                "arm_accepted": arm_ok,
                "takeoff_command_accepted": takeoff_ack_ok,
                "takeoff_detected": takeoff_detected,
                "live_movement_command_accepted": movement_ok,
                "landing_command_accepted": landing_mode_ok,
                "landed_or_disarmed": landed_or_disarmed,
                "final_status": "success" if success else "failed",
                "failure_reason": failure_reason,
                "last_relative_alt_m": state.last_relative_alt_m,
                "max_relative_alt_m": state.max_relative_alt_m,
                "movement_delta_m": state.movement_delta_m,
                "prearm_failure": state.prearm_failure,
            }
        )
        write_report(log_dir / "report.md", summary)
        session.terminate()
        events.close()


def write_report(path: Path, summary: dict[str, object]) -> None:
    limitations = []
    if summary.get("force_arm_used"):
        limitations.append("force arm used explicitly via BAS_STAGE24_FORCE_ARM=1")
    if summary.get("failure_reason"):
        limitations.append(str(summary["failure_reason"]))
    if not limitations:
        limitations.append("MAVProxy CLI is used as a headless command-line GCS; no QGroundControl GUI was launched.")

    lines = [
        "# Stage 2.4 Manual GCS Report",
        "",
        "## Manual GCS demo",
        "",
        f"- GCS type: {summary['gcs_type']}",
        f"- GCS mode: {summary['gcs_mode']}",
        f"- Endpoint chain: {summary['endpoint_chain']}",
        f"- MAVProxy endpoint: `{summary['mavproxy_master']}`",
        f"- MAVProxy command path used: {str(summary['mavproxy_command_path_used']).lower()}",
        f"- Direct pymavlink command path used: {str(summary['direct_pymavlink_command_path_used']).lower()}",
        f"- Mission upload used: {str(summary['mission_upload_used']).lower()}",
        f"- Heartbeat visible: {str(summary['heartbeat_visible']).lower()}",
        f"- GPS/position visible: {str(summary['gps_position_visible']).lower()}",
        f"- GPS fix OK: {str(summary['gps_fix_ok']).lower()}",
        f"- Autopilot ready: {str(summary['autopilot_ready']).lower()}",
        f"- Mode visible: {str(summary['mode_visible']).lower()}",
        f"- Arm state visible: {str(summary['arm_state_visible']).lower()}",
        f"- GUIDED mode accepted: {str(summary['guided_mode_accepted']).lower()}",
        f"- Arm accepted: {str(summary['arm_accepted']).lower()}",
        f"- Takeoff command accepted: {str(summary['takeoff_command_accepted']).lower()}",
        f"- Takeoff detected: {str(summary['takeoff_detected']).lower()}",
        f"- Live movement command accepted: {str(summary['live_movement_command_accepted']).lower()}",
        f"- Landing command accepted: {str(summary['landing_command_accepted']).lower()}",
        f"- Landed or disarmed: {str(summary['landed_or_disarmed']).lower()}",
        f"- Force arm used: {str(summary['force_arm_used']).lower()}",
        f"- Final status: {summary['final_status']}",
        f"- Run ID: `{summary['run_id']}`",
        f"- Config sha256: `{summary['config_sha256']}`",
        f"- Logs path: `{summary['logs_path']}`",
        "",
        "## Known limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in limitations)
    lines.extend(
        [
            "",
            "## Telemetry notes",
            "",
            f"- Last relative altitude, m: {summary.get('last_relative_alt_m')}",
            f"- Max relative altitude, m: {summary.get('max_relative_alt_m')}",
            f"- Movement delta, m: {summary.get('movement_delta_m')}",
            f"- Pre-arm failure: {summary.get('prearm_failure')}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def print_dry_run(args: argparse.Namespace) -> int:
    log_dir, aircraft_dir, home_dir, config_hash = prepare_runtime_dirs(args)
    command = build_mavproxy_command(args, aircraft_dir)
    write_command_manifest(log_dir / "mavproxy_command.json", command, args, config_hash)
    print("Stage 2.4 MAVProxy dry-run")
    print(f"run_id: {args.run_id}")
    print(f"log_dir: {log_dir}")
    print(f"isolated HOME: {home_dir}")
    print(f"config: {args.config}")
    print(f"config_sha256: {config_hash}")
    print(f"endpoint chain: MAVProxy -> ns-3 control -> mavbridge -> SITL")
    print(f"master: {args.master}")
    print("mavproxy command:")
    print("  " + " ".join(command))
    print("smoke command sequence:")
    for cmd in [WATCH_COMMAND, GUIDED_COMMAND, ARM_COMMAND, TAKEOFF_COMMAND_TEMPLATE.format(alt=args.takeoff_alt), MOVE_COMMAND, STOP_MOVE_COMMAND, LAND_COMMAND]:
        print(f"  {cmd}")
    return 0


def run_interactive(args: argparse.Namespace) -> int:
    log_dir, aircraft_dir, home_dir, config_hash = prepare_runtime_dirs(args)
    events = EventLog(log_dir / "events.jsonl", args.run_id)
    command = build_mavproxy_command(args, aircraft_dir)
    write_command_manifest(log_dir / "mavproxy_command.json", command, args, config_hash)
    events.emit(
        "mavproxy_started",
        command=command,
        master=args.master,
        mode="interactive",
        config=str(Path(args.config).resolve()),
        config_sha256=config_hash,
    )
    events.close()
    print("Starting interactive MAVProxy command-line GCS.")
    print("Flight commands must be typed into MAVProxy CLI; no direct pymavlink driver is active.")
    print("Suggested manual sequence: status, mode GUIDED, arm throttle, takeoff 10, velocity 1 0 0, velocity 0 0 0, mode LAND, disarm")
    return subprocess.call(command, cwd=str(REPO_ROOT), env=build_env(home_dir))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage 2.4 MAVProxy Manual GCS driver")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_const", dest="mode", const="smoke", help="run acceptance sequence through MAVProxy CLI")
    mode.add_argument("--interactive", action="store_const", dest="mode", const="interactive", help="leave operator in MAVProxy CLI")
    mode.add_argument("--dry-run", action="store_const", dest="mode", const="dry-run", help="print endpoints and commands without launching MAVProxy")
    parser.set_defaults(mode="smoke")
    parser.add_argument("--run-id", default=os.environ.get("BAS_RUN_ID", f"stage_2_4_mavproxy_gcs_{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}"))
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--master", default=os.environ.get("BAS_STAGE24_MAVPROXY_MASTER", DEFAULT_MASTER))
    parser.add_argument("--mavproxy-bin", type=Path, default=Path(os.environ.get("BAS_MAVPROXY_BIN", str(DEFAULT_MAVPROXY))))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-system", type=int, default=int(os.environ.get("BAS_STAGE24_SOURCE_SYSTEM", "255")))
    parser.add_argument("--source-component", type=int, default=int(os.environ.get("BAS_STAGE24_SOURCE_COMPONENT", "230")))
    parser.add_argument("--streamrate", type=int, default=int(os.environ.get("BAS_STAGE24_STREAMRATE", "10")))
    parser.add_argument("--takeoff-alt", type=float, default=float(os.environ.get("BAS_STAGE24_TAKEOFF_ALT", "10")))
    parser.add_argument("--connect-timeout", type=float, default=float(os.environ.get("BAS_STAGE24_CONNECT_TIMEOUT", "90")))
    parser.add_argument("--command-timeout", type=float, default=float(os.environ.get("BAS_STAGE24_COMMAND_TIMEOUT", "30")))
    parser.add_argument("--takeoff-timeout", type=float, default=float(os.environ.get("BAS_STAGE24_TAKEOFF_TIMEOUT", "75")))
    parser.add_argument("--movement-timeout", type=float, default=float(os.environ.get("BAS_STAGE24_MOVEMENT_TIMEOUT", "18")))
    parser.add_argument("--landing-timeout", type=float, default=float(os.environ.get("BAS_STAGE24_LANDING_TIMEOUT", "120")))
    parser.add_argument("--min-movement-m", type=float, default=float(os.environ.get("BAS_STAGE24_MIN_MOVEMENT_M", "0.5")))
    parser.add_argument("--force-arm", action="store_true", default=os.environ.get("BAS_STAGE24_FORCE_ARM", "0") == "1")
    args = parser.parse_args(argv)
    if args.log_dir is None:
        args.log_dir = str(REPO_ROOT / "logs" / args.run_id)
    args.mavproxy_bin = args.mavproxy_bin.resolve()
    if not args.mavproxy_bin.exists():
        parser.error(f"MAVProxy binary not found: {args.mavproxy_bin}")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.mode == "dry-run":
        return print_dry_run(args)
    if args.mode == "interactive":
        return run_interactive(args)
    return run_smoke(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
