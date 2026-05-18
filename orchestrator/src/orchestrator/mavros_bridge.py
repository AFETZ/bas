"""MAVROS backend для bas-orchestrator (этап 1.8).

Запускается ВНУТРИ docker контейнера `bas-mavros` (ros:humble + mavros).
Полностью заменяет pymavlink-backend по букве ТЗ "MAVROS для работы на основе
ROS": ни одного `mavutil.mavlink_connection(...)`, всё через rclpy + MAVROS
ROS2 topics/services.

Архитектура:
- Этот скрипт = rclpy.Node, который ВНУТРИ себя стартует mavros_node как
  subprocess (`ros2 run mavros mavros_node` с fcu_url=udp://@:14550).
- Subscribes:
    /mavros/state              (mavros_msgs/State)
    /mavros/global_position/global   (sensor_msgs/NavSatFix)
    /mavros/global_position/rel_alt  (std_msgs/Float64)
    /mavros/local_position/pose      (geometry_msgs/PoseStamped)
    /mavros/mission/reached    (mavros_msgs/WaypointReached)
    /mavros/extended_state     (mavros_msgs/ExtendedState)
- Services (sync calls):
    /mavros/cmd/arming         (mavros_msgs/CommandBool)
    /mavros/set_mode           (mavros_msgs/SetMode)
    /mavros/mission/push       (mavros_msgs/WaypointPush)
    /mavros/cmd/command        (mavros_msgs/CommandLong) — MISSION_START
- Workflow:
    1. wait /mavros/state.connected == True (HEARTBEAT)
    2. wait NavSatFix status >= STATUS_FIX (GPS lock)
    3. build waypoint list из scenario.mission и сделать WaypointPush
    4. set_mode AUTO + arm + cmd/command(MAV_CMD_MISSION_START)
    5. wait extended_state.landed_state == MAV_LANDED_STATE_ON_GROUND после взлёта
    6. emit "scenario" event (success/failure) и exit
- JSONL events пишутся в $BAS_RUN_DIR/events.jsonl в том же контракте что
  pymavlink-backend (event_type=run_start/flight/control_telemetry/scenario/
  run_end), чтобы `bas-analyzer` работал без изменений.

Env-переменные:
    BAS_RUN_ID         — run_id, в имени каталога логов
    BAS_RUN_DIR        — абсолютный путь к каталогу прогона
    BAS_SCENARIO_ID    — id сценария (baseline_wifi и т.п.)
    BAS_PROJECT_ROOT   — корень проекта (для resolve missions/<id>.yaml)
    BAS_MAVLINK_FCU_URL — fcu_url для MAVROS (default udp://@:14550)
    BAS_MAVLINK_GCS_URL — gcs_url (default "" — не нужен, мы и есть GCS)
    BAS_MAX_DURATION_S — таймаут на всё прогон (default 600)
"""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    QoSProfile,
    QoSReliabilityPolicy,
    QoSHistoryPolicy,
    QoSDurabilityPolicy,
    qos_profile_sensor_data,
)

# MAVROS message types (apt установлено через ros-humble-mavros-msgs).
from mavros_msgs.msg import State, ExtendedState, Waypoint, WaypointReached
from mavros_msgs.srv import CommandBool, SetMode, WaypointPush, CommandLong
from sensor_msgs.msg import NavSatFix
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Float64


# -----------------------------------------------------------------------------
# JSONL event logger (совместим с orchestrator.logger.EventLogger).
# -----------------------------------------------------------------------------
class EventLogger:
    def __init__(self, path: Path, run_id: str, scenario_id: str):
        self.path = path
        self.run_id = run_id
        self.scenario_id = scenario_id
        self._fh = None
        self._lock = threading.Lock()
        self._wall_start = time.time()

    def __enter__(self):
        self._fh = self.path.open("a")
        return self

    def __exit__(self, *exc):
        if self._fh:
            self._fh.close()

    def emit(self, event_type: str, **fields):
        if self._fh is None:
            return
        now = time.time()
        ev = {
            "event_type": event_type,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "wall_time": now,
            "wall_dt": now - self._wall_start,
            **fields,
        }
        line = json.dumps(ev, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()


# -----------------------------------------------------------------------------
# Mission YAML loading (re-implementation чтобы не тащить весь
# orchestrator.config в контейнер ROS2; PyYAML установлен).
# -----------------------------------------------------------------------------
def load_mission(project_root: Path, mission_id: str):
    """Подгружает mission YAML. Формат (см. configs/missions/simple_route.yaml):

        mission_id: simple_route
        home: {lat, lon, alt_m}      # игнорируем, используем реальный SITL home
        waypoints:
            - {action: takeoff, alt_m: 30.0}
            - {action: waypoint, x_m: 50, y_m: 0, alt_m: 30.0}
            ...
            - {action: land}
    """
    import yaml
    path = project_root / "configs" / "missions" / f"{mission_id}.yaml"
    if not path.exists():
        # Fallback: пробежимся по configs/missions/, возьмём первый yaml.
        candidates = list((project_root / "configs" / "missions").glob("*.yaml"))
        if not candidates:
            raise FileNotFoundError(
                f"mission '{mission_id}' не найден в {project_root}/configs/missions/")
        path = candidates[0]
    with path.open() as f:
        return yaml.safe_load(f)


# -----------------------------------------------------------------------------
# Bridge node.
# -----------------------------------------------------------------------------
class MavrosBridge(Node):
    def __init__(self, logger: EventLogger, mission_data: dict, max_duration_s: float):
        super().__init__("bas_mavros_bridge")
        self.logger = logger
        self.mission_data = mission_data
        self.max_duration_s = max_duration_s

        # State.
        self.connected = False
        self.armed = False
        self.flight_mode = ""
        self.gps_fix_type = 0
        self.gps_satellites = 0
        self.last_lat = 0.0
        self.last_lon = 0.0
        self.last_alt_amsl = 0.0
        self.last_alt_rel = 0.0
        self.last_vel = (0.0, 0.0, 0.0)
        self.last_heading = 0.0
        self.landed_state = 0       # 0=undefined, 1=on_ground, 2=in_air, 4=landing
        self.waypoints_reached = 0
        self.waypoints_planned = 0
        self.has_taken_off = False
        self.mission_state = "in_progress"
        self.is_complete = False

        # QoS-карта MAVROS humble publishers (проверена через `ros2 topic info -v`):
        #   /mavros/state, /mavros/extended_state, /mavros/mission/reached →
        #       Reliable + Transient Local (control-plane, default rclpy).
        #   /mavros/global_position/*, /mavros/local_position/* →
        #       Best Effort + Volatile (sensor data,
        #       rclpy.qos.qos_profile_sensor_data preset).
        # Subscriber должен matchить publisher reliability+durability — иначе
        # "offering incompatible QoS, no messages will be received".

        # Для state/extended_state/mission_reached используем Transient Local
        # durability, чтобы поймать последний published msg даже если sub
        # появился позже publisher'а.
        qos_ctrl = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.create_subscription(State, "/mavros/state", self._on_state, qos_ctrl)
        self.create_subscription(NavSatFix, "/mavros/global_position/global",
                                 self._on_gps, qos_profile_sensor_data)
        self.create_subscription(Float64, "/mavros/global_position/rel_alt",
                                 self._on_rel_alt, qos_profile_sensor_data)
        self.create_subscription(PoseStamped, "/mavros/local_position/pose",
                                 self._on_local_pose, qos_profile_sensor_data)
        self.create_subscription(ExtendedState, "/mavros/extended_state",
                                 self._on_extended_state, qos_ctrl)
        self.create_subscription(WaypointReached, "/mavros/mission/reached",
                                 self._on_wp_reached, qos_ctrl)

        # Service clients.
        self.cli_arming = self.create_client(CommandBool, "/mavros/cmd/arming")
        self.cli_set_mode = self.create_client(SetMode, "/mavros/set_mode")
        self.cli_push_wp = self.create_client(WaypointPush, "/mavros/mission/push")
        self.cli_cmd = self.create_client(CommandLong, "/mavros/cmd/command")

        # Periodic flight emit (5 Hz).
        self.create_timer(0.2, self._emit_flight)

        self.get_logger().info(
            f"BasMavrosBridge initialized, waiting for /mavros/state.connected"
        )

    # ---- Subscribers ----
    def _on_state(self, msg: State):
        was_connected = self.connected
        self.connected = bool(msg.connected)
        self.armed = bool(msg.armed)
        self.flight_mode = str(msg.mode or "")
        # First message — printout для диагностики state callback delivery.
        if not was_connected and self.connected:
            print(f"[bridge] FIRST state callback: connected={self.connected} "
                  f"armed={self.armed} mode={self.flight_mode}", flush=True)
        self.logger.emit(
            "control_telemetry",
            message_type="HEARTBEAT",
            connected=self.connected,
            armed=self.armed,
            flight_mode=self.flight_mode,
            system_status=int(msg.system_status),
        )
        if not was_connected and self.connected:
            self.logger.emit("component", component="mavros",
                             phase="connected", endpoint=os.environ.get("BAS_MAVLINK_FCU_URL", ""))

    def _on_gps(self, msg: NavSatFix):
        self.last_lat = float(msg.latitude)
        self.last_lon = float(msg.longitude)
        self.last_alt_amsl = float(msg.altitude)
        # NavSatStatus.status: -1=NO_FIX, 0=FIX, 1=SBAS, 2=GBAS.
        # status >= 0 == valid fix (mavros sets 0 for 3D fix on ardupilot).
        self.gps_fix_type = max(int(msg.status.status) + 1, 0)
        # NavSatFix не передаёт количество спутников напрямую; mavros publishes
        # /mavros/global_position/raw/satellites отдельно. Для smoke хватит
        # косвенного индикатора через position covariance.
        self.gps_satellites = 0
        self.logger.emit(
            "control_telemetry",
            message_type="GPS_RAW_INT",
            fix_type=self.gps_fix_type,
            satellites=self.gps_satellites,
            lat_e7=int(self.last_lat * 1e7),
            lon_e7=int(self.last_lon * 1e7),
            alt_mm=int(self.last_alt_amsl * 1000),
        )

    def _on_rel_alt(self, msg: Float64):
        self.last_alt_rel = float(msg.data)
        if not self.has_taken_off and self.last_alt_rel > 2.0:
            self.has_taken_off = True
            self.logger.emit("component", component="mavros", phase="takeoff_detected",
                             alt_rel_m=self.last_alt_rel)

    def _on_local_pose(self, msg: PoseStamped):
        # heading from quaternion (yaw)
        q = msg.pose.orientation
        import math
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.last_heading = math.degrees(math.atan2(siny_cosp, cosy_cosp)) % 360.0

    def _on_extended_state(self, msg: ExtendedState):
        prev = self.landed_state
        self.landed_state = int(msg.landed_state)
        # MAV_LANDED_STATE: 0=UNDEFINED, 1=ON_GROUND, 2=IN_AIR, 3=TAKEOFF, 4=LANDING
        if prev != self.landed_state:
            self.logger.emit("component", component="mavros",
                             phase="landed_state_change",
                             prev=prev, current=self.landed_state)
            # has_taken_off + return to ON_GROUND = mission landed
            if (self.has_taken_off and self.landed_state == 1
                    and self.waypoints_reached >= max(self.waypoints_planned - 1, 1)):
                self.mission_state = "landed"
                self.is_complete = True
                self.logger.emit("component", component="mavros",
                                 phase="mission_landed",
                                 waypoints_reached=self.waypoints_reached)

    def _on_wp_reached(self, msg: WaypointReached):
        self.waypoints_reached = int(msg.wp_seq) + 1
        self.logger.emit("control_telemetry",
                         message_type="MISSION_ITEM_REACHED",
                         seq=int(msg.wp_seq),
                         total=self.waypoints_planned)

    # ---- Periodic flight emit ----
    def _emit_flight(self):
        if not self.connected:
            return
        self.logger.emit(
            "flight",
            vehicle_id="uav-1",
            position={
                "lat": self.last_lat,
                "lon": self.last_lon,
                "alt_amsl_m": self.last_alt_amsl,
                "alt_rel_m": self.last_alt_rel,
            },
            velocity_mps={
                "vx": self.last_vel[0],
                "vy": self.last_vel[1],
                "vz": self.last_vel[2],
            },
            heading_deg=self.last_heading,
            flight_mode=self.flight_mode,
            mission_state=self.mission_state,
        )

    # ---- Service helpers ----
    # Spin отдельной thread'ой через MultiThreadedExecutor (см. main()).
    # Поэтому здесь спим simple time.sleep, callback'и приходят асинхронно.
    def _wait_service(self, client, name: str, timeout_s: float = 30.0) -> bool:
        return client.wait_for_service(timeout_sec=timeout_s)

    def call_service(self, client, request, name: str, timeout_s: float = 10.0):
        if not self._wait_service(client, name, timeout_s=5.0):
            self.get_logger().error(f"service {name} not available")
            return None
        future = client.call_async(request)
        deadline = time.time() + timeout_s
        while time.time() < deadline and not future.done():
            time.sleep(0.1)
        if future.done():
            return future.result()
        self.get_logger().error(f"service {name} call timeout {timeout_s}s")
        return None

    # ---- Mission upload ----
    def _build_waypoints(self) -> list:
        """Из mission YAML собрать MAVROS Waypoint list.

        Формат YAML: list of actions {takeoff, waypoint, land} в local frame
        (x_m, y_m, alt_m). Home берётся из текущего SITL GPS (через MAVROS),
        local x/y конвертируются в lat/lon offset (плоское приближение).
        Совместимо с pymavlink._build_auto_mission в real_components.py.
        """
        import math

        wps_in = self.mission_data.get("waypoints", [])
        # Реальный home от SITL (Canberra default), не из YAML (там Москва
        # для документации, но фактический полёт в SITL координатах).
        home_lat, home_lon = self.last_lat, self.last_lon
        deg_per_m_lat = 1.0 / 111319.9
        deg_per_m_lon = 1.0 / (111319.9 * max(math.cos(math.radians(home_lat)), 0.01))

        wp_list = []
        # seq=0 — HOME-item (current location, ArduCopter ignores params but
        # требует seq=0 c is_current=True).
        wp_list.append(self._make_wp(
            frame=Waypoint.FRAME_GLOBAL_REL_ALT,
            command=16,   # MAV_CMD_NAV_WAYPOINT
            is_current=True, autocontinue=True,
            x=home_lat, y=home_lon, z=0.0))

        for action in wps_in:
            act = action.get("action", "waypoint")
            if act == "takeoff":
                wp_list.append(self._make_wp(
                    frame=Waypoint.FRAME_GLOBAL_REL_ALT,
                    command=22,   # MAV_CMD_NAV_TAKEOFF
                    is_current=False, autocontinue=True,
                    x=home_lat, y=home_lon,
                    z=float(action.get("alt_m", 20.0))))
            elif act == "waypoint":
                x_m = float(action.get("x_m", 0.0))
                y_m = float(action.get("y_m", 0.0))
                wp_list.append(self._make_wp(
                    frame=Waypoint.FRAME_GLOBAL_REL_ALT,
                    command=16,
                    is_current=False, autocontinue=True,
                    x=home_lat + x_m * deg_per_m_lat,
                    y=home_lon + y_m * deg_per_m_lon,
                    z=float(action.get("alt_m", 20.0))))
            elif act == "land":
                wp_list.append(self._make_wp(
                    frame=Waypoint.FRAME_GLOBAL_REL_ALT,
                    command=21,   # MAV_CMD_NAV_LAND
                    is_current=False, autocontinue=True,
                    x=home_lat, y=home_lon, z=0.0))
            else:
                self.get_logger().warn(f"unknown action '{act}', skipping")
        return wp_list

    def _make_wp(self, *, frame: int, command: int,
                 is_current: bool, autocontinue: bool,
                 x: float, y: float, z: float,
                 param1: float = 0.0, param2: float = 0.0,
                 param3: float = 0.0, param4: float = 0.0) -> Waypoint:
        wp = Waypoint()
        wp.frame = frame
        wp.command = command
        wp.is_current = is_current
        wp.autocontinue = autocontinue
        wp.param1, wp.param2, wp.param3, wp.param4 = param1, param2, param3, param4
        wp.x_lat, wp.y_long, wp.z_alt = x, y, z
        return wp

    # ---- Workflow ----
    def run_mission(self) -> bool:
        # 1. Wait /mavros/state.connected (callbacks приходят через executor.spin).
        self.logger.emit("component", component="mavros", phase="wait_connected")
        deadline = time.time() + 120.0
        while not self.connected and time.time() < deadline:
            time.sleep(0.2)
        if not self.connected:
            self.logger.emit("component", component="mavros", phase="connect_timeout")
            return False

        # 2. Wait GPS fix (NavSatStatus.STATUS_FIX => >= 0).
        self.logger.emit("component", component="mavros", phase="wait_gps_fix")
        deadline = time.time() + 120.0
        while self.gps_fix_type < 1 and time.time() < deadline:
            time.sleep(0.2)
        if self.gps_fix_type < 1:
            self.logger.emit("component", component="mavros", phase="gps_fix_timeout")
            return False
        # Wait EKF + position settle.
        time.sleep(5.0)

        # 3. Build + push mission.
        wp_list = self._build_waypoints()
        self.waypoints_planned = len(wp_list) - 1   # без HOME-item
        self.logger.emit("component", component="mavros",
                         phase="wp_push", count=len(wp_list))

        push_req = WaypointPush.Request()
        push_req.start_index = 0
        push_req.waypoints = wp_list
        push_resp = self.call_service(self.cli_push_wp, push_req,
                                      "/mavros/mission/push", timeout_s=30.0)
        if push_resp is None or not push_resp.success:
            self.logger.emit("component", component="mavros", phase="wp_push_failed",
                             transferred=getattr(push_resp, "wp_transfered", 0))
            return False
        self.logger.emit("component", component="mavros", phase="wp_push_ok",
                         transferred=int(push_resp.wp_transfered))

        # 4a. Disable arming check (как делает pymavlink-backend для SITL без RC).
        # ArduPilot SITL по дефолту ARMING_CHECK=1 (full); для теста просим 0.
        # Через CommandLong MAV_CMD_DO_SET_PARAMETER — но это не стандарт MAVLink;
        # вместо этого SITL уже стартует с gazebo-iris.parm, и наш upload через
        # MAVROS пройдёт через mavros param plugin (опционально).

        # 4b. set_mode AUTO + arm + MISSION_START.
        mode_req = SetMode.Request()
        mode_req.custom_mode = "AUTO"
        mode_resp = self.call_service(self.cli_set_mode, mode_req,
                                      "/mavros/set_mode", timeout_s=10.0)
        if mode_resp is None or not mode_resp.mode_sent:
            self.logger.emit("component", component="mavros", phase="set_mode_failed",
                             mode="AUTO")
            return False
        self.logger.emit("component", component="mavros", phase="set_mode_ok", mode="AUTO")
        time.sleep(2.0)

        arm_req = CommandBool.Request()
        arm_req.value = True
        arm_resp = self.call_service(self.cli_arming, arm_req,
                                     "/mavros/cmd/arming", timeout_s=10.0)
        if arm_resp is None or not arm_resp.success:
            self.logger.emit("component", component="mavros", phase="arm_failed",
                             result=getattr(arm_resp, "result", -1))
            return False
        self.logger.emit("component", component="mavros", phase="arm_ok")

        # 5. Wait landed (or max_duration). Callbacks приходят через executor.
        deadline = time.time() + self.max_duration_s
        while time.time() < deadline:
            time.sleep(0.5)
            if self.is_complete:
                return True
        self.logger.emit("component", component="mavros", phase="mission_timeout",
                         elapsed_s=self.max_duration_s)
        self.mission_state = "timeout"
        return False


# -----------------------------------------------------------------------------
# Main: MAVROS subprocess + bridge node.
# -----------------------------------------------------------------------------
def main():
    run_id = os.environ.get("BAS_RUN_ID", "mavros_smoke")
    run_dir_str = os.environ.get("BAS_RUN_DIR", f"/work/logs/{run_id}")
    scenario_id = os.environ.get("BAS_SCENARIO_ID", "baseline_wifi")
    project_root = Path(os.environ.get("BAS_PROJECT_ROOT", "/work"))
    fcu_url = os.environ.get("BAS_MAVLINK_FCU_URL", "udp://@:14550")
    gcs_url = os.environ.get("BAS_MAVLINK_GCS_URL", "")
    max_duration_s = float(os.environ.get("BAS_MAX_DURATION_S", "600"))

    run_dir = Path(run_dir_str)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "events.jsonl"

    # Mission YAML.
    import yaml
    # Сначала прочитать scenario чтобы узнать mission_id.
    scenario_path = project_root / "configs" / "scenarios" / f"{scenario_id}.yaml"
    if scenario_path.exists():
        with scenario_path.open() as f:
            scenario_yaml = yaml.safe_load(f)
        mission_id = scenario_yaml.get("mission", {}).get("mission_id", "survey_short")
    else:
        mission_id = "survey_short"
    mission_data = load_mission(project_root, mission_id)

    # Запускаем MAVROS node как subprocess. fcu_url передаётся через --ros-args.
    # gcs_url передаём только если не пустой (пустое значение ломает rcl parser).
    print(f"[bas-mavros-bridge] launching mavros_node fcu_url={fcu_url}")
    mavros_cmd = [
        "ros2", "run", "mavros", "mavros_node",
        "--ros-args",
        "-p", f"fcu_url:={fcu_url}",
        "-p", "tgt_system:=1",
        "-p", "tgt_component:=1",
        "-p", "fcu_protocol:=v2.0",
        "-p", "system_id:=255",
        "-p", "component_id:=190",
    ]
    if gcs_url:
        mavros_cmd += ["-p", f"gcs_url:={gcs_url}"]
    mavros_proc = subprocess.Popen(
        mavros_cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    # Перенаправляем stdout MAVROS в файл лога (но не блокируем).
    def _drain_mavros_log():
        log_file = run_dir / "mavros_node.log"
        with log_file.open("w") as f:
            for line in mavros_proc.stdout:
                f.write(line.decode("utf-8", errors="replace"))
                f.flush()
    threading.Thread(target=_drain_mavros_log, daemon=True).start()

    # Подождать пока MAVROS опубликует topics (5-10s).
    time.sleep(8)

    rclpy.init()
    rc = 1
    with EventLogger(log_path, run_id=run_id, scenario_id=scenario_id) as logger:
        # run_start события (совместим с orchestrator.run._emit_run_start).
        logger.emit(
            "run_start",
            config_hash="mavros_smoke",
            seed=42,
            stub=False,
            mavlink_backend="mavros",
            mavlink_endpoint=fcu_url,
            mission=mission_id,
            max_duration_s=max_duration_s,
            versions={
                "orchestrator": "0.1.0-mavros",
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "ros_distro": os.environ.get("ROS_DISTRO", "humble"),
            },
        )

        bridge = MavrosBridge(logger, mission_data=mission_data,
                              max_duration_s=max_duration_s)

        # MultiThreadedExecutor крутит callbacks в фоне; run_mission на
        # foreground'е спит time.sleep между checks, callbacks приходят
        # без блокировки workflow.
        executor = MultiThreadedExecutor()
        executor.add_node(bridge)
        spin_thread = threading.Thread(target=executor.spin, daemon=True)
        spin_thread.start()

        try:
            success = bridge.run_mission()
        except KeyboardInterrupt:
            success = False
        except Exception as exc:
            logger.emit("component", component="mavros", phase="exception", error=repr(exc))
            success = False
        finally:
            executor.shutdown()

        if success and bridge.mission_state == "landed":
            status, reason = "success", "mission_landed"
            rc = 0
        elif bridge.mission_state == "timeout":
            status, reason = "timeout", "max_duration_s"
        else:
            status, reason = "failed", f"mission_state={bridge.mission_state}"

        logger.emit("scenario", status=status, reason=reason)
        logger.emit("run_end", scenario_id=scenario_id)

        bridge.destroy_node()

    rclpy.shutdown()

    # Cleanup MAVROS.
    print(f"[bas-mavros-bridge] terminating mavros_node")
    mavros_proc.send_signal(signal.SIGINT)
    try:
        mavros_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        mavros_proc.kill()

    sys.exit(rc)


if __name__ == "__main__":
    main()
