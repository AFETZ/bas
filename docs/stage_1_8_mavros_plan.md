# Этап 1.8 — MAVROS backend (checkpoint)

ТЗ Физулина: «MAVROS для работы на основе ROS». Runtime-flag
`--mavlink-backend pymavlink|mavros` в `bas-orchestrator` переключает
полностью между прямым pymavlink (1.5.x/2.1/1.7 paths, default) и MAVROS
ROS2 humble backend (новый, через docker `bas/mavros:dev`).

## Реализованная инфраструктура

| Артефакт | Файл | Статус |
|---|---|---|
| Docker image ROS2 humble + MAVROS 2.14 + geographiclib datasets | `docker/mavros/Dockerfile` | ✅ build OK (1.3 GB) |
| rclpy bridge Node (subprocess MAVROS + subscribers + service clients + JSONL emit) | `orchestrator/src/orchestrator/mavros_bridge.py` | ✅ Реализован |
| CLI flag `--mavlink-backend pymavlink\|mavros` | `orchestrator/src/orchestrator/main.py` | ✅ |
| Dispatch `_run_mavros()` (docker run + bind-mounts + env) | `orchestrator/src/orchestrator/run.py` | ✅ |
| Wrapper `scripts/run_stage_1_8_mavros.sh` | `scripts/run_stage_1_8_mavros.sh` | ✅ |
| MultiThreadedExecutor для асинхронных callback'ов | `mavros_bridge.py:main()` | ✅ |

## Проверенные части

Smoke прогон `bash scripts/run_stage_1_8_mavros.sh baseline_wifi` (запись
`logs/stage_1_8_mavros_baseline_wifi_20260518T180919Z/events.jsonl`):

- ✅ ROS2 humble + MAVROS 2.14 контейнер стартует, mavros_node + bridge
  работают в одной netns (`container:bas-uav-net`).
- ✅ MAVROS подключается к SITL по `tcp://127.0.0.1:5760` (`CON: Got
  HEARTBEAT, connected. FCU: ArduPilot`).
- ✅ EKF3 IMU0/1 init + GPS detection (`FCU: EKF3 IMU0 is using GPS`).
- ✅ Bridge subscribers создаются, callback `/mavros/state` приходит
  каждые ~1 сек (`connected=True armed=False mode=STABILIZE`).
- ✅ JSONL events совместимы с `bas-analyzer` (event_type=HEARTBEAT,
  flight, component); поле `mavlink_backend="mavros"` различает backend.
- ✅ MultiThreadedExecutor крутит callback'и асинхронно от mission
  workflow в main thread.

## Известная регрессия (требует доводки)

| Топик | QoS publisher | Callback в bridge |
|---|---|---|
| `/mavros/state` | Reliable / Transient Local | ✅ работает |
| `/mavros/extended_state` | Reliable / Transient Local | вероятно работает |
| `/mavros/global_position/global` (NavSatFix) | **BEST_EFFORT** / Volatile | ❌ subscription соз дан, но callback не приходит |
| `/mavros/global_position/rel_alt` (Float64) | BEST_EFFORT / Volatile | ❌ |
| `/mavros/local_position/pose` (PoseStamped) | BEST_EFFORT / Volatile | ❌ |

Без NavSatFix callback'а bridge висит в `wait_gps_fix` 120 с и выходит с
`gps_fix_timeout`. Mission upload (`/mavros/mission/push`) не вызывается,
mission AUTO не стартует.

Симметричный smoke probe (`/tmp/probe_sub.py` — pure rclpy subscribe на
`/mavros/state`) показал что callback'и **работают**: 11 callback за 12 с
в чистом python вне моего bridge. Это значит rclpy/DDS/QoS базово OK в
WSL2 для Reliable topics.

Гипотеза: для BEST_EFFORT publisher'ов my subscription QoS profile
`QoSProfile(reliability=BEST_EFFORT, history=KEEP_LAST, depth=10)` не
matchает publisher Durability `VOLATILE`. Нужно явно `durability=VOLATILE`
в QoSProfile или просто `qos_profile_sensor_data` preset.

## TODO для финиша 1.8

1. Заменить `qos_sensor` на `rclpy.qos.qos_profile_sensor_data`
   (preset с правильными BEST_EFFORT+VOLATILE+keep_last(5) policies).
2. Verify mission upload (`/mavros/mission/push` service) — нужен
   `start_index=0` и waypoint list с корректными `frame=GLOBAL_REL_ALT`,
   `command=22/16/21` (TAKEOFF/WAYPOINT/LAND).
3. Verify arming через `/mavros/cmd/arming` — на SITL без RC нужен
   `MAV_CMD_RUN_PREARM_CHECKS` или параметр `ARMING_CHECK=0`.
4. Verify `landed_state==1` (MAV_LANDED_STATE_ON_GROUND) для
   `is_complete=True`.

## Архитектура (после довода)

```
host: bash scripts/run_stage_1_8_mavros.sh baseline_wifi
        │
        ├── docker compose: uav-net + gazebo + sitl
        │      (SITL: tcp:0.0.0.0:5760 LISTEN, primary serial)
        │
        └── docker run --rm bas/mavros:dev
                network: container:bas-uav-net
                command: python3 -m orchestrator.mavros_bridge
                  │
                  ├── subprocess.Popen([ros2, run, mavros, mavros_node, ...
                  │       fcu_url=tcp://127.0.0.1:5760])
                  │       │
                  │       └── MAVROS connects to SITL → topics on /mavros/*
                  │
                  └── rclpy.Node (MavrosBridge) + MultiThreadedExecutor
                      ├── subs: state, NavSatFix, ExtendedState, etc.
                      ├── svc clients: cmd/arming, set_mode, mission/push
                      └── workflow: connected → gps_fix → wp_push →
                                    set_mode AUTO → arm → wait landed
```

Все события `events.jsonl` тагируются `mavlink_backend: "mavros"` для
отличия от pymavlink-backend в analyzer comparison.
