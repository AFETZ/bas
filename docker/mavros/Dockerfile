# MAVROS backend для bas-orchestrator (этап 1.8).
#
# ROS2 humble + MAVROS 2.14 + custom rclpy bridge. Заменяет pymavlink-backend
# по букве ТЗ "MAVROS для работы на основе ROS". Mission upload идёт через
# mavros_msgs/srv/WaypointPush, ARM через mavros_msgs/srv/CommandBool,
# режим через mavros_msgs/srv/SetMode — никакого прямого pymavlink в радио.
#
# MAVROS node стартует внутри контейнера, подключается к SITL через
# udp://0.0.0.0:14550@ (mavbridge SITL←→UDP14550), переводит MAVLink в
# ROS2 topics. Наш bridge node (`mavros_bridge.py`) подписывается на topics,
# делает mission lifecycle и пишет JSONL events в /work/logs/<run_id>/events.jsonl
# в том же контракте что pymavlink-backend — чтобы analyzer работал unchanged.

FROM ros:humble-ros-base-jammy

ENV DEBIAN_FRONTEND=noninteractive

# Шаг 1: MAVROS из официального packages.ros.org репозитория.
# ros-humble-mavros pulls in libmavconn, mavlink, rclpy, etc.
# ros-humble-mavros-extras — для дополнительных плагинов (image, vibration и т.п.,
# не обязательно для mission AUTO, но полезно для будущих расширений).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-humble-mavros \
        ros-humble-mavros-msgs \
        ros-humble-mavros-extras \
        python3-pip \
        python3-yaml \
        wget \
    && rm -rf /var/lib/apt/lists/*

# Шаг 2: geographiclib datasets — runtime dependency MAVROS для GPS frame
# transformations (без этого CRITICAL warnings + некоторые msgs не публикуются).
# Скрипт ставится через ros-humble-mavros, но в headless установке его надо
# вызвать руками.
RUN /opt/ros/humble/lib/mavros/install_geographiclib_datasets.sh || \
    (echo "fallback install" && \
     geographiclib-get-geoids egm96-5 && \
     geographiclib-get-gravity egm96 && \
     geographiclib-get-magnetic emm2015)

WORKDIR /work

# orchestrator/ smount как ro в runtime (--volume ./orchestrator:/work/orchestrator:ro),
# а наш bridge node сидит там же. PYTHONPATH покрывает оба варианта запуска.
ENV PYTHONPATH=/work/orchestrator/src
ENV PYTHONUNBUFFERED=1

# Default command: source ROS, запустить mavros_node И bridge параллельно.
# mavros_node.launch у v2.14 на humble — это apm.launch (ArduPilot profile).
# Bridge node читает MAVROS topics и эмитит JSONL events.
#
# CMD-параметры из env / docker run cmd:
#   BAS_RUN_ID — run_id, под который писать events.jsonl
#   BAS_PROJECT_ROOT — host path (используется bridge для resolve mission file)
#   BAS_MAVLINK_FCU_URL — fcu_url для MAVROS (default udp://@127.0.0.1:14550)
#   BAS_MAVLINK_GCS_URL — gcs_url для MAVROS (default "")
#   BAS_SCENARIO_ID — какой сценарий грузить (по default baseline_wifi)
CMD ["bash", "-lc", "source /opt/ros/humble/setup.bash && exec python3 -m orchestrator.mavros_bridge"]
