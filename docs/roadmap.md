# Roadmap и состояние работ

Актуально на 26.05.2026. Файл больше не является списком ожидающих решений:
личная зона Физулина А.В. по текущей матрице закрыта, Stage 3/4 backlog
переведён из архитектурных набросков в проверяемые артефакты.

## Закрытые этапы

| Этап | Статус | Ключевой результат |
|---|---|---|
| 1.0-1.4 | Закрыто | Skeleton, Docker, Gazebo/SITL, ns-3 TapBridge, базовый MAVLink |
| 1.5.0 | Закрыто | Shadow GCS через ns-3 control channel |
| 1.5.1 | Закрыто | AUTO mission через ns-3 на `wifi_good` и `degraded_lora` |
| 1.5.2 | Закрыто | RTP/H.264 payload, Gazebo camera, video metrics, outage correlation |
| 1.6 | Закрыто | WiFi vs LoRa comparison report + CSV |
| 1.7 | Закрыто | LoRa через Serial Port без IP-stack в радиопетле |
| 1.8 | Закрыто | ROS2/MAVROS backend как альтернативный путь управления |
| 2.1 | Закрыто | Sionna RT scene/radio map + dynamic channel hook |
| 2.1.e | Закрыто | Online Sionna RT PathSolver на каждый UAV update, `control+payload` hook |
| 2.2 | Закрыто | Cosys-AirSim overlay, Windows real GPU rendering, AirSim camera pull |
| 2.3 | Закрыто | Multi-UAV MVP: 2 ArduCopter SITL + 2 iris + mavp2p |
| 2.4 | Закрыто | Web GCS + MAVProxy ручное управление одним БАС |
| 2.4 RF/QGC/Auto | Закрыто | RF/FPV demo, QGroundControl bridge, Playwright auto-recorder |
| 3.x | Закрыто | Urban scene, ИССГР API, multicast sync, on-board DB, CV geo-tagging |
| 4.x sim bridges | Закрыто | ArduPilot↔AirSim JSON-FDM, MAVLink fanout router, real SITL ARM+takeoff |
| 4.x extensions | Закрыто | AirSim scene map, cyber defense, large maps, admin dashboard, parallel compute |

## Текущая демонстрационная вершина

Основной операторский сценарий:

```bash
sudo bash scripts/run_stage_2_4_auto_demo.sh
```

Что показывает:

- Gazebo + ArduPilot SITL + Web GCS на `http://127.0.0.1:8765/`;
- TAKEOFF, WASD/manual velocity и `GO TO` через MAVProxy stdin;
- FPV overlay + RF panel: LOS/NLOS, RSSI, loss, delay, график;
- scripted route с Playwright/ffmpeg записью видео и screenshots;
- события в `events.jsonl`, `ns3_events.jsonl`, `demo_report.md`.

Главный Stage 4 proof:

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh smoke
.venv/bin/python scripts/_real_sitl_e2e_smoke.py
```

Что доказывает:

- JSON-FDM smoke: 340 PWM frames, climb >2 м, yaw rotation;
- real ArduCopter `--model json:127.0.0.1`;
- `HEARTBEAT`, valid `GLOBAL_POSITION_INT`, PWM round-trip;
- `STABILIZE → ARM`, RC throttle takeoff, relative altitude climb >0.5 м,
  max motor PWM > hover.

## Что осталось за рамками текущего стенда

| Блок | Почему вне scope | Возможный следующий этап |
|---|---|---|
| HIL / field test | Репозиторий закрывает SITL, не реальный Pixhawk/дрон | Отдельный HIL стенд с hardware safety checklist |
| Production deployment | Это исследовательский стенд, не Kubernetes/observability продукт | Helm/Compose production profile + metrics/logging |
| Real OSM/satellite streaming | Сейчас есть algorithmic 20x20 км tiling и synthetic scenes | OSM/orthophoto importer + asset streaming |
| Full grant delivery pack | Текущая матрица разделяет личную зону и внешнюю рамку гранта | Отдельная общегрантовая матрица по всем исполнителям |

## Где смотреть детали

- [README.md](../README.md) — актуальный верхний обзор и команды.
- [STAGES.md](STAGES.md) — каталог stages 1.0-4.x.
- [DEMOS.md](DEMOS.md) — все demo/smoke entrypoints.
- [QUICKSTART.md](QUICKSTART.md) — рабочие команды по сценариям.
- [ARCHITECTURE.md](ARCHITECTURE.md) — текущая архитектура и IPC paths.
- [tz_compliance.md](tz_compliance.md) — матрица закрытия ТЗ.
- [LIMITATIONS.md](LIMITATIONS.md) — честный список не-production аспектов.
- [stage_4_arducopter_airsim_interface.md](stage_4_arducopter_airsim_interface.md) — JSON-FDM + ARM/takeoff proof.
- [stage_4_mavlink_sim_router.md](stage_4_mavlink_sim_router.md) — MAVLink fanout router.
