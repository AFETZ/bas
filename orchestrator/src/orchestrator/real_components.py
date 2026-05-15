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
        startup_timeout_s: float = 120.0,
        external_compose: bool = False,
    ) -> None:
        """external_compose=True: контейнеры подняты внешним скриптом, оркестратор
        НЕ делает docker compose up/down, не ждёт TCP-порт (предполагаем что он
        уже открыт). Используется для этапов 1.5.1+ где host-скрипт управляет
        жизненным циклом контейнеров и netns'ов."""
        self.compose_file = compose_file
        self.logger = logger
        self.mavlink_endpoint = mavlink_endpoint
        self.startup_timeout_s = startup_timeout_s
        self.external_compose = external_compose
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
        # Mission upload protocol state
        # (seq, wall_time, message_type) where type is "MISSION_REQUEST" or "_INT"
        self.last_mission_request: Optional[tuple[int, float, str]] = None
        self.last_mission_ack: Optional[tuple[int, float]] = None  # (type, wall_time)
        self.last_mission_current_seq: int = 0
        self._lock = threading.Lock()

    # ---------- lifecycle ----------

    def start(self) -> None:
        if not self.external_compose:
            self.logger.emit("component", component=self.name, phase="compose_up_start")
            self._run_compose(["up", "-d", "--no-build", "gazebo", "sitl"])
            self.logger.emit("component", component=self.name, phase="compose_up_done")

            if not self._wait_for_port(host="127.0.0.1", port=5760,
                                       timeout_s=self.startup_timeout_s):
                raise RuntimeError(
                    f"MAVLink порт 5760 не открылся за {self.startup_timeout_s} с. "
                    "Проверьте `docker logs bas-sitl` и `docker logs bas-gazebo`."
                )
        else:
            self.logger.emit("component", component=self.name, phase="external_compose_assumed",
                             endpoint=self.mavlink_endpoint)

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
                # Для udpout endpoint'ов: посылаем GCS heartbeat, чтобы peer
                # узнал наш адрес. Для udpin сначала ждём heartbeat от SITL.
                if self.mavlink_endpoint.startswith("udpout:"):
                    try:
                        self.mav.mav.heartbeat_send(
                            mavutil.mavlink.MAV_TYPE_GCS,
                            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                            0, 0, 0,
                        )
                    except Exception:
                        pass
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

        # Попросить поток телеметрии. Для degraded_lora держим частоту умеренной:
        # mission upload чувствителен к очередям, а 2 Гц хватает для контроля.
        for _ in range(3):
            try:
                self.mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0,
                )
            except Exception:
                pass
            self.mav.mav.request_data_stream_send(
                self._sys_id, self._comp_id,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 2, 1,
            )
            time.sleep(0.2)

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
        if not self.external_compose:
            self.logger.emit("component", component=self.name, phase="compose_down_start")
            self._run_compose(["down", "-v"], check=False)
            self.logger.emit("component", component=self.name, phase="compose_down_done")
        else:
            self.logger.emit("component", component=self.name, phase="external_compose_teardown_skipped")

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
            with self._lock:
                self.last_mission_current_seq = int(msg.seq)
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="MISSION_CURRENT",
                seq=int(msg.seq),
            )
        elif t == "COMMAND_ACK":
            # Критично для диагностики: что SITL ответил на ARM/команды.
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="COMMAND_ACK",
                command=int(msg.command),
                result=int(msg.result),  # 0=ACCEPTED 1=TEMP_REJECTED 2=DENIED 3=UNSUPPORTED 4=FAILED
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
            lower = text.lower()
            if "land complete" in lower:
                with self._lock:
                    self.mission_state = "landed"
        elif t == "MISSION_REQUEST" or t == "MISSION_REQUEST_INT":
            with self._lock:
                self.last_mission_request = (int(msg.seq), time.time(), t)
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type=t,
                seq=int(msg.seq),
            )
        elif t == "MISSION_ACK":
            with self._lock:
                self.last_mission_ack = (int(msg.type), time.time())
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="MISSION_ACK",
                ack_type=int(msg.type),  # 0=ACCEPTED 1=ERROR 2=UNSUPPORTED_FRAME ...
            )
        elif t == "MISSION_ITEM_REACHED":
            self.logger.emit(
                "control_telemetry",
                sim_time=sim_time,
                message_type="MISSION_ITEM_REACHED",
                seq=int(msg.seq),
            )


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
        """Выбирает стратегию полёта: AUTO (mission upload) или GUIDED (live commands).

        AUTO режим устойчивее к lossy MAVLink каналу - mission upload протокол
        имеет встроенные ретраи (SITL пере-запрашивает потерянные items), и
        после MISSION_START БАС летит автономно без необходимости в live
        SET_POSITION_TARGET каждый шаг. Этот режим выбран по умолчанию.

        GUIDED режим (старый): постоянно шлёт команды через канал, чувствителен
        к head-of-line blocking в TCP при потерях.
        """
        assert self.stack.mav is not None

        self._wait_for_gps_fix(min_fix=3, timeout_s=gps_wait_timeout_s)
        home_lat, home_lon, home_alt = self._wait_for_home(timeout_s=90.0)
        self.logger.emit("component", component=self.name, phase="using_sitl_home",
                         lat=home_lat, lon=home_lon, alt=home_alt)

        # AUTO режим через mission upload.
        self._set_mode("GUIDED")  # сначала GUIDED чтобы upload mission работал
        self._disable_arming_check()
        time.sleep(2.0)

        items = self._build_auto_mission(home_lat, home_lon, home_alt)
        self._upload_mission(items)
        self._set_mode("AUTO")
        self._arm()
        self._mission_start()

        # Запустить наблюдение за миссией в фоне.
        threading.Thread(
            target=self._fly_auto_mission,
            args=(home_lat, home_lon, len(items)),
            name="mission-runner",
            daemon=True,
        ).start()
        self.logger.emit("component", component=self.name, phase="mission_started",
                         n_waypoints=len(items), mode="AUTO")

    def _build_auto_mission(self, home_lat: float, home_lon: float,
                            home_alt: float) -> list:
        """Собрать MISSION_ITEM_INT'ы для AUTO режима.

        ArduCopter ожидает:
          item 0: HOME (frame=GLOBAL, cmd=NAV_WAYPOINT, current=1, lat/lon/alt home)
          item 1: TAKEOFF (cmd=NAV_TAKEOFF, frame=GLOBAL_RELATIVE_ALT, alt=takeoff_alt)
          item 2..N: WAYPOINTs (cmd=NAV_WAYPOINT, frame=GLOBAL_RELATIVE_ALT)
          item last: LAND (cmd=NAV_LAND)
        """
        from pymavlink.dialects.v20 import ardupilotmega as mavlink
        items = []
        seq = 0
        # item 0: HOME (любой mission в ArduCopter требует первый item=home).
        items.append(mavlink.MAVLink_mission_item_int_message(
            target_system=self.stack._sys_id,
            target_component=self.stack._comp_id,
            seq=seq,
            frame=mavlink.MAV_FRAME_GLOBAL,
            command=mavlink.MAV_CMD_NAV_WAYPOINT,
            current=1, autocontinue=1,
            param1=0, param2=0, param3=0, param4=0,
            x=int(home_lat * 1e7), y=int(home_lon * 1e7),
            z=float(home_alt),
            mission_type=0,
        ))
        seq += 1

        # Mission items из YAML waypoints.
        wps = self._waypoints_xyz()
        for wp in wps:
            if wp.is_takeoff:
                lat, lon = home_lat, home_lon
                cmd = mavlink.MAV_CMD_NAV_TAKEOFF
                alt = wp.z
            elif wp.is_land:
                lat, lon = 0.0, 0.0  # 0,0 = land at current position
                cmd = mavlink.MAV_CMD_NAV_LAND
                alt = 0.0
            else:
                lat, lon = self._offset_lat_lon(home_lat, home_lon, wp.x, wp.y)
                cmd = mavlink.MAV_CMD_NAV_WAYPOINT
                alt = wp.z
            items.append(mavlink.MAVLink_mission_item_int_message(
                target_system=self.stack._sys_id,
                target_component=self.stack._comp_id,
                seq=seq,
                frame=mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                command=cmd,
                current=0, autocontinue=1,
                param1=0, param2=0, param3=0, param4=0,
                x=int(lat * 1e7), y=int(lon * 1e7),
                z=float(alt),
                mission_type=0,
            ))
            seq += 1

        self.logger.emit("component", component=self.name, phase="mission_built",
                         n_items=len(items))
        return items

    def _upload_mission(self, items: list) -> None:
        """Загрузить миссию в SITL через стандартный MAVLink protocol.

        Протокол:
            GCS -> MISSION_COUNT(n)
            SITL -> MISSION_REQUEST/MISSION_REQUEST_INT(seq=0)
            GCS -> MISSION_ITEM_INT(seq=0)
            SITL -> MISSION_REQUEST(seq=1) ... (retry on loss)
            GCS -> MISSION_ITEM_INT(seq=1)
            ...
            SITL -> MISSION_ACK(type=0=ACCEPTED)
        В lossy канале SITL сам пере-запрашивает потерянные item'ы.
        """
        mav = self.stack.mav
        assert mav is not None
        n = len(items)
        self.logger.emit("component", component=self.name, phase="mission_upload_start",
                         n_items=n)

        # Reset listener state for upload protocol.
        with self.stack._lock:
            self.stack.last_mission_request = None
            self.stack.last_mission_ack = None

        # Повтор COUNT во время активной upload-сессии сбрасывает протокол у
        # ArduPilot, поэтому до первого request ретраим коротким burst'ом, а
        # после первого request даём автопилоту самому пере-запрашивать item'ы.
        self._send_mission_count(n, repeats=5)
        count_sent_at = time.time()
        last_request_at = 0.0  # время последнего MISSION_REQUEST от SITL

        upload_timeout_s = 300.0
        upload_deadline = time.time() + upload_timeout_s
        next_seq = 0
        last_item_sent_at: dict[int, float] = {}
        highest_request_seq_seen = -1
        while time.time() < upload_deadline:
            with self.stack._lock:
                ack = self.stack.last_mission_ack
                req = self.stack.last_mission_request

            if ack is not None:
                ack_type, _ = ack
                if ack_type == 0:
                    self.logger.emit("component", component=self.name,
                                     phase="mission_upload_ack", ack_type=ack_type)
                    return
                raise RuntimeError(f"MISSION_ACK error type={ack_type}")

            if req is not None:
                req_seq, req_t, req_type = req
                last_request_at = req_t
                if req_seq < n:
                    if req_seq < highest_request_seq_seen:
                        self.logger.emit("component", component=self.name,
                                         phase="mission_request_stale_ignored",
                                         seq=req_seq, highest_request_seq_seen=highest_request_seq_seen)
                        with self.stack._lock:
                            self.stack.last_mission_request = None
                        time.sleep(0.1)
                        continue
                    highest_request_seq_seen = max(highest_request_seq_seen, req_seq)

                    now = time.time()
                    last_sent = last_item_sent_at.get(req_seq, 0.0)
                    if now - last_sent < 5.0:
                        self.logger.emit("component", component=self.name,
                                         phase="mission_request_duplicate_ignored",
                                         seq=req_seq, since_last_send_s=now - last_sent)
                        with self.stack._lock:
                            self.stack.last_mission_request = None
                        time.sleep(0.1)
                        continue

                    # ArduPilot SITL в успешном wifi_good прогоне присылает
                    # legacy MISSION_REQUEST, но принимает MISSION_ITEM_INT.
                    # Legacy MISSION_ITEM в degraded_lora застревал на seq=0,
                    # поэтому для обоих request-вариантов шлём INT item burst'ом.
                    messages_to_send = [("MISSION_ITEM_INT", items[req_seq])]
                    sends_ok = 0
                    sent_formats = []
                    for fmt, msg_to_send in messages_to_send:
                        try:
                            mav.mav.send(msg_to_send)
                            sends_ok += 1
                            sent_formats.append(fmt)
                        except Exception as exc:
                            self.logger.emit("component", component=self.name,
                                             phase="mission_item_send_error",
                                             seq=req_seq, fmt=fmt,
                                             attempt=1,
                                             error=repr(exc))
                    last_item_sent_at[req_seq] = time.time()
                    self.logger.emit("component", component=self.name,
                                     phase="mission_item_sent", seq=req_seq,
                                     proto=req_type, repeats=sends_ok,
                                     formats=",".join(sent_formats))
                    next_seq = max(next_seq, req_seq + 1)
                    with self.stack._lock:
                        self.stack.last_mission_request = None

            # Если SITL замолчал — пере-шлём COUNT для перезапуска протокола.
            # До первого request это дешёвый retry потерянного COUNT. После request
            # ждём гораздо дольше, чтобы не cancel'ить уже начатый upload.
            silence_from = max(count_sent_at, last_request_at)
            silence_limit_s = 60.0 if last_request_at > 0.0 else 15.0
            if time.time() - silence_from > silence_limit_s:
                self._send_mission_count(n, repeats=5)
                count_sent_at = time.time()
                with self.stack._lock:
                    self.stack.last_mission_ack = None
                    self.stack.last_mission_request = None
                self.logger.emit("component", component=self.name,
                                 phase="mission_count_resent_after_silence", n=n,
                                 next_seq=next_seq, silence_limit_s=silence_limit_s)
                next_seq = 0
            time.sleep(0.3)

        raise RuntimeError(f"Mission upload не завершён за {upload_timeout_s:.0f}s (next_seq={next_seq}/{n})")

    def _send_mission_count(self, n: int, repeats: int = 1) -> None:
        mav = self.stack.mav
        assert mav is not None
        sent = 0
        for i in range(repeats):
            try:
                mav.mav.mission_count_send(
                    self.stack._sys_id, self.stack._comp_id, n, mission_type=0,
                )
                sent += 1
            except Exception as exc:
                self.logger.emit("component", component=self.name,
                                 phase="mission_count_send_error",
                                 attempt=i + 1, error=repr(exc))
            time.sleep(0.2)
        self.logger.emit("component", component=self.name,
                         phase="mission_count_sent", n=n, repeats=sent)

    def _to_legacy_item(self, item_int):
        """Конвертирует MISSION_ITEM_INT в legacy MISSION_ITEM (float coords)."""
        from pymavlink.dialects.v20 import ardupilotmega as mavlink
        return mavlink.MAVLink_mission_item_message(
            target_system=item_int.target_system,
            target_component=item_int.target_component,
            seq=item_int.seq,
            frame=item_int.frame,
            command=item_int.command,
            current=item_int.current,
            autocontinue=item_int.autocontinue,
            param1=item_int.param1, param2=item_int.param2,
            param3=item_int.param3, param4=item_int.param4,
            x=item_int.x / 1e7,  # lat: int32 1e-7 deg → float deg
            y=item_int.y / 1e7,  # lon
            z=item_int.z,
            mission_type=item_int.mission_type,
        )

    def _mission_start(self) -> None:
        """Send MAV_CMD_MISSION_START via command_long."""
        mav = self.stack.mav
        assert mav is not None
        for _ in range(3):
            mav.mav.command_long_send(
                self.stack._sys_id, self.stack._comp_id,
                mavutil.mavlink.MAV_CMD_MISSION_START,
                0, 0, 0, 0, 0, 0, 0, 0,
            )
            time.sleep(0.5)
        self.logger.emit("component", component=self.name, phase="mission_start_sent")

    def _fly_auto_mission(self, home_lat: float, home_lon: float, n_items: int) -> None:
        """Мониторинг AUTO миссии: ждём пока БАС долетит и landed."""
        # Стартовая высота и переход в landed.
        # Ждём пока mission_state="landed" (set по STATUSTEXT "Land complete")
        # ИЛИ MISSION_CURRENT.seq дойдёт до n_items-1 (LAND) и alt < 1m.
        wall_start = time.time()
        max_wait_s = 300  # 5 минут на всю миссию

        # Phase 1: ждём takeoff (alt > 5m или MISSION_CURRENT >= 2)
        while time.time() - wall_start < 90:
            _, _, alt = self.stack.last_position
            seq = self.stack.last_mission_current_seq
            if alt > 5.0 or seq >= 2:
                self.logger.emit("component", component=self.name,
                                 phase="takeoff_observed", alt=alt, mission_seq=seq)
                break
            time.sleep(1.0)
        else:
            self.logger.emit("component", component=self.name, phase="takeoff_not_observed",
                             alt=self.stack.last_position[2],
                             mission_seq=self.stack.last_mission_current_seq)
            with self.stack._lock:
                self.stack.mission_state = "failed"
            return

        # Phase 2: ждём landed.
        while time.time() - wall_start < max_wait_s:
            if self.stack.mission_state == "landed":
                self.logger.emit("component", component=self.name,
                                 phase="mission_complete", final_state="landed")
                return
            seq = self.stack.last_mission_current_seq
            _, _, alt = self.stack.last_position
            if seq >= n_items - 1 and alt < 1.0:
                with self.stack._lock:
                    self.stack.mission_state = "landed"
                self.logger.emit("component", component=self.name,
                                 phase="mission_complete", final_state="landed",
                                 last_seq=seq)
                return
            time.sleep(2.0)

        self.logger.emit("component", component=self.name, phase="mission_timeout",
                         mission_seq=self.stack.last_mission_current_seq,
                         alt=self.stack.last_position[2])
        with self.stack._lock:
            self.stack.mission_state = "failed"

    def _fly_mission(self, home_lat: float, home_lon: float) -> None:
        wps = self._waypoints_xyz()
        # Текущий streaming-target: непрерывно шлётся 2Гц независимо от потери
        # отдельных пакетов. {lat, lon, alt_rel_m} or None для остановки потока.
        self._stream_target: Optional[tuple[float, float, float]] = None
        self._stream_stop = threading.Event()
        threading.Thread(target=self._streaming_target_loop, daemon=True,
                         name="pos-target-stream").start()
        try:
            for idx, wp in enumerate(wps):
                self._fly_one_waypoint(idx, wp, home_lat, home_lon)
        except Exception as exc:
            self.logger.emit("component", component=self.name, phase="mission_error",
                             error=repr(exc))
            with self.stack._lock:
                self.stack.mission_state = "failed"
            self._stream_stop.set()
            return
        self._stream_stop.set()
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
            # NAV_TAKEOFF переводит ArduCopter из landed в active flight.
            # Stream NE запускается во время takeoff - меньше TCP backlog,
            # ArduCopter сам тянет вверх через NAV_TAKEOFF.
            self._takeoff(wp.z)
            self._wait_for_altitude(wp.z, tolerance_m=1.5, timeout_s=120.0)
            # После взлёта запускаем streaming для waypoint'ов.
            self._stream_target = (home_lat, home_lon, wp.z)
        elif wp.is_land:
            # Останавливаем поток позиций и переходим в LAND mode.
            self._stream_target = None
            time.sleep(0.5)
            self._land()
            self._wait_for_landed(timeout_s=180.0)
        else:
            lat, lon = self._offset_lat_lon(home_lat, home_lon, wp.x, wp.y)
            # Обновляем поток на новую цель - streaming loop её будет долбить.
            self._stream_target = (lat, lon, wp.z)
            self._wait_for_position(lat, lon, wp.z, tolerance_m=3.0, timeout_s=90.0)
        self.logger.emit("component", component=self.name, phase="waypoint_done", idx=idx)

    def _streaming_target_loop(self) -> None:
        """Фоновый поток: непрерывно шлёт self._stream_target в SITL с частотой 2Гц.

        Защищает полётный контур от потерь пакетов в lossy канале. ArduCopter
        принимает повторы как обновление цели - без проблем.
        """
        mav = self.stack.mav
        assert mav is not None
        type_mask = 0b110_111_111_000
        while not self._stream_stop.is_set():
            target = self._stream_target
            if target is not None:
                lat, lon, alt = target
                try:
                    mav.mav.set_position_target_global_int_send(
                        int(time.time() * 1000) & 0xFFFFFFFF,
                        self.stack._sys_id, self.stack._comp_id,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        type_mask,
                        int(lat * 1e7), int(lon * 1e7), float(alt),
                        0, 0, 0, 0, 0, 0, 0, 0,
                    )
                except Exception:
                    pass
            time.sleep(0.5)

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

    def _send_position_target(self, lat: float, lon: float, alt_rel_m: float, repeats: int = 3) -> None:
        mav = self.stack.mav
        assert mav is not None
        type_mask = 0b110_111_111_000  # ignore vel/accel/yaw, only position used
        for _ in range(repeats):
            mav.mav.set_position_target_global_int_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                self.stack._sys_id, self.stack._comp_id,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                type_mask,
                int(lat * 1e7), int(lon * 1e7), float(alt_rel_m),
                0, 0, 0, 0, 0, 0, 0, 0,
            )
            time.sleep(0.2)
        self.logger.emit("component", component=self.name, phase="position_target_sent",
                         lat=lat, lon=lon, alt_rel_m=alt_rel_m, repeats=repeats)

    def _wait_for_altitude(self, target_alt_m: float, tolerance_m: float, timeout_s: float) -> None:
        """Ждём target_alt с периодическим re-send NAV_TAKEOFF (раз в 10с)."""
        mav = self.stack.mav
        assert mav is not None
        deadline = time.time() + timeout_s
        last_resend = time.time()
        while time.time() < deadline:
            _, _, alt = self.stack.last_position
            if abs(alt - target_alt_m) <= tolerance_m:
                return
            # Re-send TAKEOFF every 10s in case earlier was lost.
            if time.time() - last_resend > 10.0 and alt < 1.0:
                mav.mav.command_long_send(
                    self.stack._sys_id, self.stack._comp_id,
                    mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                    0, 0, 0, 0, 0, 0, 0, target_alt_m,
                )
                last_resend = time.time()
                self.logger.emit("component", component=self.name,
                                 phase="takeoff_resent", alt_m=target_alt_m)
            time.sleep(0.5)
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
        mav = self.stack.mav
        assert mav is not None
        last_request = 0.0
        while time.time() < deadline:
            if time.time() - last_request >= 3.0:
                mav.mav.command_long_send(
                    self.stack._sys_id, self.stack._comp_id,
                    mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                    0, mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION, 0, 0, 0, 0, 0, 0,
                )
                last_request = time.time()
                self.logger.emit("component", component=self.name,
                                 phase="home_position_requested")
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

    def _disable_arming_check(self) -> None:
        """Установить ARMING_CHECK=0 + DISARM_DELAY=0 через PARAM_SET.

        В degraded_lora канале pre-arm проходит, но финальное MAV_CMD_ARM
        иногда теряется или ArduCopter не отвечает COMMAND_ACK через lossy
        link. ARMING_CHECK=0 убирает все checks. DISARM_DELAY=0 отключает
        auto-disarm (по дефолту 10с после ARM без throttle) - нужно потому
        что TAKEOFF в lossy канале может прийти спустя 30+ секунд после ARM.
        """
        mav = self.stack.mav
        assert mav is not None
        params = [
            (b"ARMING_CHECK", 0.0),
            (b"DISARM_DELAY", 0.0),
        ]
        for _ in range(3):
            for name, value in params:
                mav.mav.param_set_send(
                    self.stack._sys_id, self.stack._comp_id,
                    name, value, mavutil.mavlink.MAV_PARAM_TYPE_INT32,
                )
                time.sleep(0.2)
            time.sleep(0.3)
        self.logger.emit("component", component=self.name, phase="arming_check_disabled")

    def _arm(self, *, settle_wait_s: float = 70.0, retries: int = 6) -> None:
        """ARM с ретраями + force-arm + дублирование пакета.

        SITL требует ~60-70с пока EKF полностью интегрирует GPS ("EKF3 IMU0/1
        is using GPS"). Раньше этого arm денится без видимых причин в STATUSTEXT.
        Сам пакет ARM может потеряться через lossy ns-3 канал — поэтому в
        каждой попытке шлём команду 3 раза.
        """
        mav = self.stack.mav
        assert mav is not None

        self.logger.emit("component", component=self.name, phase="imu_settle_wait",
                         wait_s=settle_wait_s)
        time.sleep(settle_wait_s)

        # Снимаем все arming checks (важно для lossy канала где telemetry разорвана).
        self._disable_arming_check()
        time.sleep(3.0)

        # СТРАТЕГИЯ "polite client": не спамим, а ждём долго каждую попытку.
        # В lossy канале спам ARM создаёт TCP backlog и блокирует TAKEOFF.
        # Шлём ровно ОДНУ ARM команду, ждём 60s появления armed=True в HEARTBEAT.
        # При неудаче повторяем.
        for attempt in range(1, retries + 1):
            self.logger.emit("component", component=self.name, phase="arm_attempt",
                             attempt=attempt, force=True)
            mav.mav.command_long_send(
                self.stack._sys_id, self.stack._comp_id,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 1, 21196.0, 0, 0, 0, 0, 0,
            )
            deadline = time.time() + 60
            while time.time() < deadline:
                if self.stack.last_armed:
                    self.logger.emit("component", component=self.name, phase="armed",
                                     attempt=attempt)
                    return
                time.sleep(0.5)

        raise RuntimeError(
            f"Автопилот не армится за {retries}×60s попыток. "
            "Смотрите STATUSTEXT 'Arm: ...' и sitl.log."
        )

    def _takeoff(self, alt_m: float) -> None:
        """ОДНА NAV_TAKEOFF команда + повтор раз в 5s если высота не растёт.

        В lossy TCP канале спам команд создаёт backlog. Лучше slow-and-steady.
        """
        mav = self.stack.mav
        assert mav is not None
        mav.mav.command_long_send(
            self.stack._sys_id, self.stack._comp_id,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0, 0, 0, 0, 0, 0, 0, alt_m,
        )
        self.logger.emit("component", component=self.name, phase="takeoff_sent", alt_m=alt_m)
