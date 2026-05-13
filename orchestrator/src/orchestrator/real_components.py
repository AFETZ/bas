"""Реальные компоненты для прогона через Docker.

Этап 1.2: gazebo + sitl поднимаются через docker compose; MAVLink-листенер
переводит телеметрию ArduPilot в события JSONL единого журнала; MissionRunner
загружает waypoints, армит автопилот и контролирует завершение миссии.

ns-3 пока не подключён в радио-петлю - этап 1.4.
"""
from __future__ import annotations

import math
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pymavlink import mavutil

from .config import Mission
from .logger import EventLogger


# -----------------------------------------------------------------------------
# Управление контейнерами через docker compose
# -----------------------------------------------------------------------------

class DockerComposeFlightStack:
    """Поднимает контейнеры gazebo + sitl и держит MAVLink-листенер.

    Контейнеры стартуют через `docker compose -f <file> up -d gazebo sitl`.
    Stop останавливает их через `docker compose down`. Листенер MAVLink работает
    в фоновом потоке и пишет события flight + control_telemetry в общий журнал.
    """

    name = "docker-compose:gazebo+sitl"

    def __init__(
        self,
        compose_file: Path,
        logger: EventLogger,
        mavlink_endpoint: str = "tcp:127.0.0.1:5760",
        startup_timeout_s: float = 60.0,
    ) -> None:
        self.compose_file = compose_file
        self.logger = logger
        self.mavlink_endpoint = mavlink_endpoint
        self.startup_timeout_s = startup_timeout_s
        self.mav: Optional[mavutil.mavfile] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._sys_id = 0
        self._comp_id = 0
        # Снапшот последних значений для удобного доступа MissionRunner.
        self.last_heartbeat_time: float = 0.0
        self.last_flight_mode: str = ""
        self.last_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.last_armed: bool = False
        self.last_gps_fix: int = 0
        self.home_position: Optional[tuple[float, float, float]] = None  # lat, lon, alt_m
        self.mission_state: str = ""
        self._lock = threading.Lock()

    # ---------- lifecycle ----------

    def start(self) -> None:
        self.logger.emit("component", component=self.name, phase="compose_up_start")
        # docker compose up -d gazebo sitl
        self._run_compose(["up", "-d", "--no-build", "gazebo", "sitl"])
        self.logger.emit("component", component=self.name, phase="compose_up_done")

        if not self._wait_for_port(host="127.0.0.1", port=5760, timeout_s=self.startup_timeout_s):
            raise RuntimeError(
                f"MAVLink порт 5760 не открылся за {self.startup_timeout_s} с. "
                "Проверьте `docker logs bas-sitl` и `docker logs bas-gazebo`."
            )

        # SITL биндит порт сразу, но MAVLink-поток стартует только после
        # инициализации (EKF, GPS, sensors). До этого ранние подключения
        # SITL разрывает с логом "Closed connection on SERIAL0".
        settle_s = 10.0
        self.logger.emit("component", component=self.name, phase="mavlink_settle_wait",
                         seconds=settle_s)
        time.sleep(settle_s)

        # Ретраи HEARTBEAT - на холодном старте бывает что первая попытка не успевает.
        hb_deadline = time.time() + self.startup_timeout_s
        last_error: Optional[Exception] = None
        while time.time() < hb_deadline:
            try:
                self.mav = mavutil.mavlink_connection(self.mavlink_endpoint, source_system=254)
                self.logger.emit("component", component=self.name, phase="mavlink_connecting",
                                 endpoint=self.mavlink_endpoint)
                hb = self.mav.recv_match(type="HEARTBEAT", blocking=True, timeout=10)
                if hb is not None:
                    break
                self.logger.emit("component", component=self.name, phase="heartbeat_retry",
                                 reason="no_heartbeat_in_10s")
                self.mav.close()
                self.mav = None
            except Exception as exc:
                last_error = exc
                self.logger.emit("component", component=self.name, phase="heartbeat_retry",
                                 reason=repr(exc))
                time.sleep(2.0)
        else:
            raise RuntimeError(
                f"HEARTBEAT не пришёл за {self.startup_timeout_s} с (последняя ошибка: {last_error})"
            )
        self._sys_id = hb.get_srcSystem()
        self._comp_id = hb.get_srcComponent()
        self.logger.emit("component", component=self.name, phase="heartbeat",
                         sys_id=self._sys_id, comp_id=self._comp_id,
                         autopilot=int(hb.autopilot), type=int(hb.type))

        # Попросить максимальную частоту телеметрии.
        self.mav.mav.request_data_stream_send(
            self._sys_id, self._comp_id,
            mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1,
        )

        # Запустить листенер в потоке.
        self._listener_thread = threading.Thread(target=self._listener_loop, name="mavlink-listener", daemon=True)
        self._listener_thread.start()

    def step(self, sim_time: float, wall_dt: float) -> None:
        # В реальном режиме компонент крутится сам. step используется только
        # для периодических проверок (например, что HEARTBEAT свежий).
        if self.last_heartbeat_time and (time.time() - self.last_heartbeat_time) > 5.0:
            self.logger.emit("component", component=self.name, phase="heartbeat_stale",
                             age_s=time.time() - self.last_heartbeat_time)

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener_thread:
            self._listener_thread.join(timeout=3.0)
        if self.mav:
            try:
                self.mav.close()
            except Exception:
                pass
        self.logger.emit("component", component=self.name, phase="compose_down_start")
        self._run_compose(["down", "-v"], check=False)
        self.logger.emit("component", component=self.name, phase="compose_down_done")

    @property
    def is_complete(self) -> bool:
        return self.mission_state in {"landed", "complete", "failed"}

    # ---------- internals ----------

    def _run_compose(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["docker", "compose", "-f", str(self.compose_file), *args]
        return subprocess.run(cmd, check=check, capture_output=True, text=True)

    @staticmethod
    def _wait_for_port(host: str, port: int, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except OSError:
                time.sleep(0.5)
        return False

    def _listener_loop(self) -> None:
        assert self.mav is not None
        # Используем sim_time = текущее значение HEARTBEAT-времени относительно старта.
        sim_t0 = time.time()
        while not self._stop_event.is_set():
            msg = self.mav.recv_match(blocking=True, timeout=0.5)
            if msg is None:
                continue
            t = msg.get_type()
            sim_time = time.time() - sim_t0
            try:
                self._handle_mavlink(t, msg, sim_time)
            except Exception as exc:
                self.logger.emit("component", component=self.name, phase="listener_error",
                                 error=repr(exc), msg_type=t)

    def _handle_mavlink(self, t: str, msg, sim_time: float) -> None:
        if t == "HEARTBEAT":
            with self._lock:
                self.last_heartbeat_time = time.time()
                self.last_armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                # Имя custom_mode преобразуется в строку через ardupilotmega.
                mode_name = mavutil.mode_string_v10(msg)
                self.last_flight_mode = mode_name
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="HEARTBEAT",
                sys_id=msg.get_srcSystem(),
                base_mode=int(msg.base_mode),
                custom_mode=int(msg.custom_mode),
                flight_mode=self.last_flight_mode,
                armed=self.last_armed,
            )
        elif t == "GLOBAL_POSITION_INT":
            # позиция: lat/lon в 1e-7, alt в мм AMSL, relative_alt в мм
            with self._lock:
                self.last_position = (msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000.0)
            self.logger.emit(
                "flight",
                sim_time=sim_time,
                vehicle_id="uav-1",
                position={
                    "lat": msg.lat / 1e7,
                    "lon": msg.lon / 1e7,
                    "alt_amsl_m": msg.alt / 1000.0,
                    "alt_rel_m": msg.relative_alt / 1000.0,
                },
                velocity_mps={"vx": msg.vx / 100.0, "vy": msg.vy / 100.0, "vz": msg.vz / 100.0},
                heading_deg=msg.hdg / 100.0 if msg.hdg != 65535 else None,
                flight_mode=self.last_flight_mode,
                mission_state=self.mission_state or "in_progress",
            )
        elif t == "GPS_RAW_INT":
            with self._lock:
                self.last_gps_fix = int(msg.fix_type)
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="GPS_RAW_INT",
                fix_type=int(msg.fix_type),
                satellites=int(msg.satellites_visible),
                eph=int(msg.eph),
            )
        elif t == "HOME_POSITION":
            with self._lock:
                self.home_position = (msg.latitude / 1e7, msg.longitude / 1e7, msg.altitude / 1000.0)
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="HOME_POSITION",
                lat=msg.latitude / 1e7,
                lon=msg.longitude / 1e7,
                alt_amsl_m=msg.altitude / 1000.0,
            )
        elif t == "MISSION_CURRENT":
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="MISSION_CURRENT",
                seq=int(msg.seq),
            )
        elif t == "STATUSTEXT":
            text = msg.text.decode("utf-8", errors="replace") if isinstance(msg.text, bytes) else str(msg.text)
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="STATUSTEXT",
                severity=int(msg.severity),
                text=text,
            )
            # Из STATUSTEXT можно выудить факт посадки.
            lower = text.lower()
            if "land complete" in lower or "disarming" in lower and self.last_position[2] < 1.0:
                with self._lock:
                    self.mission_state = "landed"


# -----------------------------------------------------------------------------
# MissionRunner: загрузка миссии, армирование, контроль завершения
# -----------------------------------------------------------------------------

@dataclass
class WaypointXYZ:
    x: float
    y: float
    z: float
    is_takeoff: bool = False
    is_land: bool = False


class MissionRunner:
    """Загружает миссию в SITL и наблюдает за её выполнением.

    Использует тот же MAVLink-канал что и DockerComposeFlightStack.mav.
    """

    name = "mission-runner"

    def __init__(self, stack: DockerComposeFlightStack, mission: Mission, logger: EventLogger) -> None:
        self.stack = stack
        self.mission = mission
        self.logger = logger

    def upload_and_start(self, *, gps_wait_timeout_s: float = 60.0) -> None:
        """Выполнить миссию в GUIDED режиме (без mission upload протокола).

        Поток: GPS -> home -> GUIDED -> ARM -> цикл по waypoints через
        SET_POSITION_TARGET_GLOBAL_INT -> LAND. Завершение фиксируется по
        STATUSTEXT "land complete" в листенере либо по достижению последнего WP.
        """
        assert self.stack.mav is not None

        # 1. Дождаться 3D-GPS-фикса.
        self._wait_for_gps_fix(min_fix=3, timeout_s=gps_wait_timeout_s)

        # 2. SITL home как точка отсчёта (YAML home - только метаданные).
        home_lat, home_lon, home_alt = self._wait_for_home(timeout_s=30.0)
        self.logger.emit("component", component=self.name, phase="using_sitl_home",
                         lat=home_lat, lon=home_lon, alt=home_alt)

        # 3. GUIDED + ARM.
        self._set_mode("GUIDED")
        self._arm()

        # 4. Запустить миссию в фоновом потоке (run.py наблюдает за stack.is_complete).
        threading.Thread(
            target=self._fly_mission,
            args=(home_lat, home_lon),
            name="mission-runner",
            daemon=True,
        ).start()
        self.logger.emit("component", component=self.name, phase="mission_started",
                         n_waypoints=len(self._waypoints_xyz()))

    def _fly_mission(self, home_lat: float, home_lon: float) -> None:
        wps = self._waypoints_xyz()
        try:
            for idx, wp in enumerate(wps):
                self._fly_one_waypoint(idx, wp, home_lat, home_lon)
        except Exception as exc:
            self.logger.emit("component", component=self.name, phase="mission_error",
                             error=repr(exc))
            with self.stack._lock:
                self.stack.mission_state = "failed"
            return
        # Если последний WP не был LAND - всё равно посадить.
        if not any(w.is_land for w in wps):
            self._land()
            try:
                self._wait_for_landed(120.0)
            except TimeoutError as exc:
                self.logger.emit("component", component=self.name, phase="land_timeout",
                                 error=repr(exc))
        with self.stack._lock:
            if self.stack.mission_state not in {"landed", "failed"}:
                self.stack.mission_state = "complete"
        self.logger.emit("component", component=self.name, phase="mission_complete",
                         final_state=self.stack.mission_state)

    def _fly_one_waypoint(self, idx: int, wp: WaypointXYZ,
                          home_lat: float, home_lon: float) -> None:
        self.logger.emit("component", component=self.name, phase="waypoint_start",
                         idx=idx, x=wp.x, y=wp.y, z=wp.z,
                         is_takeoff=wp.is_takeoff, is_land=wp.is_land)
        if wp.is_takeoff:
            self._takeoff(wp.z)
            self._wait_for_altitude(wp.z, tolerance_m=1.5, timeout_s=45.0)
        elif wp.is_land:
            self._land()
            self._wait_for_landed(timeout_s=120.0)
        else:
            lat, lon = self._offset_lat_lon(home_lat, home_lon, wp.x, wp.y)
            self._send_position_target(lat, lon, wp.z)
            self._wait_for_position(lat, lon, wp.z, tolerance_m=3.0, timeout_s=60.0)
        self.logger.emit("component", component=self.name, phase="waypoint_done", idx=idx)

    # ---------- internals ----------

    def _waypoints_xyz(self) -> list[WaypointXYZ]:
        out: list[WaypointXYZ] = []
        for wp in self.mission.waypoints:
            action = wp.get("action")
            if action == "takeoff":
                out.append(WaypointXYZ(0.0, 0.0, float(wp["alt_m"]), is_takeoff=True))
            elif action == "waypoint":
                out.append(WaypointXYZ(float(wp["x_m"]), float(wp["y_m"]), float(wp["alt_m"])))
            elif action == "land":
                out.append(WaypointXYZ(0.0, 0.0, 0.0, is_land=True))
        return out

    @staticmethod
    def _offset_lat_lon(lat: float, lon: float, dx_m: float, dy_m: float) -> tuple[float, float]:
        """Сместить координату на dx_m (восток) / dy_m (север) в метрах.

        Локально-плоская аппроксимация - ок для дистанций < 1 км.
        """
        d_lat = dy_m / 111320.0
        d_lon = dx_m / (111320.0 * math.cos(math.radians(lat)))
        return lat + d_lat, lon + d_lon

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        from math import radians, sin, cos, asin, sqrt
        R = 6_371_000.0
        p1, p2 = radians(lat1), radians(lat2)
        dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
        return 2 * R * asin(sqrt(a))

    def _send_position_target(self, lat: float, lon: float, alt_rel_m: float) -> None:
        mav = self.stack.mav
        assert mav is not None
        # type_mask: игнорируем velocity (3-5), accel (6-8), yaw (10), yaw_rate (11).
        # Биты для игнора: vx,vy,vz=8,16,32; ax,ay,az=64,128,256; yaw=1024, yaw_rate=2048.
        type_mask = 0b110_111_111_000
        mav.mav.set_position_target_global_int_send(
            int(time.time() * 1000) & 0xFFFFFFFF,
            self.stack._sys_id, self.stack._comp_id,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            type_mask,
            int(lat * 1e7), int(lon * 1e7), float(alt_rel_m),
            0, 0, 0, 0, 0, 0, 0, 0,
        )
        self.logger.emit("component", component=self.name, phase="position_target_sent",
                         lat=lat, lon=lon, alt_rel_m=alt_rel_m)

    def _wait_for_altitude(self, target_alt_m: float, tolerance_m: float, timeout_s: float) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            _, _, alt = self.stack.last_position
            if abs(alt - target_alt_m) <= tolerance_m:
                return
            time.sleep(0.3)
        raise TimeoutError(f"высота {target_alt_m:.1f}m не достигнута за {timeout_s}s "
                          f"(текущая {self.stack.last_position[2]:.2f})")

    def _wait_for_position(self, target_lat: float, target_lon: float, target_alt: float,
                            tolerance_m: float, timeout_s: float) -> None:
        deadline = time.time() + timeout_s
        d = float("inf")
        while time.time() < deadline:
            lat, lon, alt = self.stack.last_position
            d = self._haversine_m(target_lat, target_lon, lat, lon)
            d_alt = abs(alt - target_alt)
            if d <= tolerance_m and d_alt <= tolerance_m:
                return
            time.sleep(0.3)
        raise TimeoutError(f"WP ({target_lat:.6f}, {target_lon:.6f}, {target_alt:.1f}m) "
                          f"не достигнут за {timeout_s}s (last d={d:.2f}m)")

    def _wait_for_landed(self, timeout_s: float) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.stack.mission_state == "landed":
                return
            _, _, alt = self.stack.last_position
            if alt < 0.5 and not self.stack.last_armed:
                with self.stack._lock:
                    self.stack.mission_state = "landed"
                return
            time.sleep(0.3)
        raise TimeoutError("LAND не завершился")

    def _land(self) -> None:
        self._set_mode("LAND")
        self.logger.emit("component", component=self.name, phase="land_commanded")

    def _wait_for_home(self, timeout_s: float) -> tuple[float, float, float]:
        deadline = time.time() + timeout_s
        # Попросить HOME_POSITION (запрос-сообщение через MAV_CMD_REQUEST_MESSAGE).
        mav = self.stack.mav
        assert mav is not None
        mav.mav.command_long_send(
            self.stack._sys_id, self.stack._comp_id,
            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
            0, mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION, 0, 0, 0, 0, 0, 0,
        )
        while time.time() < deadline:
            if self.stack.home_position is not None:
                return self.stack.home_position
            time.sleep(0.3)
        raise RuntimeError(f"HOME_POSITION не получен за {timeout_s} с")

    def _wait_for_gps_fix(self, min_fix: int, timeout_s: float) -> None:
        self.logger.emit("component", component=self.name, phase="wait_gps_fix", min_fix=min_fix)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.stack.last_gps_fix >= min_fix:
                self.logger.emit("component", component=self.name, phase="gps_fix_ok",
                                 fix_type=self.stack.last_gps_fix)
                return
            time.sleep(0.5)
        raise RuntimeError(f"GPS fix>={min_fix} не получен за {timeout_s} с")

    def _set_mode(self, mode_name: str) -> None:
        mav = self.stack.mav
        assert mav is not None
        mode_id = mav.mode_mapping().get(mode_name)
        if mode_id is None:
            raise ValueError(f"Неизвестный режим {mode_name}")
        mav.mav.set_mode_send(
            self.stack._sys_id,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id,
        )
        self.logger.emit("component", component=self.name, phase="set_mode", mode=mode_name)
        time.sleep(0.5)

    def _arm(self, *, settle_wait_s: float = 25.0, retries: int = 4) -> None:
        """ARM с ретраями + force-arm если не удалось.

        SITL может выдавать "Arm: Accels inconsistent" первые ~20-25 секунд пока
        IMU/EKF не устаканятся. Сначала ждём settle_wait_s после GPS, потом arm.
        """
        mav = self.stack.mav
        assert mav is not None

        # Дождаться устаканивания IMU.
        self.logger.emit("component", component=self.name, phase="imu_settle_wait",
                         wait_s=settle_wait_s)
        time.sleep(settle_wait_s)

        for attempt in range(1, retries + 1):
            force = attempt >= 3  # последние 2 попытки - force arm.
            param2 = 21196.0 if force else 0.0
            mav.mav.command_long_send(
                self.stack._sys_id, self.stack._comp_id,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 1, param2, 0, 0, 0, 0, 0,
            )
            self.logger.emit("component", component=self.name, phase="arm_attempt",
                             attempt=attempt, force=force)
            deadline = time.time() + 6
            while time.time() < deadline:
                if self.stack.last_armed:
                    self.logger.emit("component", component=self.name, phase="armed",
                                     attempt=attempt, force=force)
                    return
                time.sleep(0.2)

        raise RuntimeError(
            f"Автопилот не армится за {retries} попыток (даже с force). "
            "Смотрите STATUSTEXT 'Arm: ...' в events.jsonl."
        )

    def _takeoff(self, alt_m: float) -> None:
        mav = self.stack.mav
        assert mav is not None
        mav.mav.command_long_send(
            self.stack._sys_id, self.stack._comp_id,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0, 0, 0, 0, 0, 0, 0, alt_m,
        )
        self.logger.emit("component", component=self.name, phase="takeoff_sent", alt_m=alt_m)
