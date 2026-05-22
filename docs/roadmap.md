# Roadmap и состояние работ

Актуально на 20.05.2026. Файл больше не является списком ожидающих решений:
личная зона Физулина А.В. по текущей матрице закрыта. Ниже зафиксировано, что
уже реализовано, и что остаётся как внешний backlog полного проекта.

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
| 2.4 | Закрыто | Web GCS + MAVProxy ручное управление одним БАС |
| 2.4 RF demo | Закрыто | Gazebo obstacles + live LOS/NLOS/RSSI/loss/delay graph |

## Текущая демонстрационная вершина

Основной видео-сценарий:

```bash
sudo bash scripts/run_stage_2_4_rf_demo.sh
```

Что показывает:

- Gazebo GUI с БАС, runway, полем и препятствиями;
- Browser Web GCS на `http://127.0.0.1:8765/`;
- TAKEOFF, WASD/manual velocity и `GO TO` через MAVProxy stdin;
- `GO TO` как `SET_POSITION_TARGET_LOCAL_NED`, а не body-frame velocity;
- live RF panel: LOS/NLOS, RSSI, loss, delay, график;
- события в `events.jsonl` и `ns3_events.jsonl`.

## Что осталось за рамками этой закрытой части

| Блок | Почему не закрыт здесь | Возможный следующий этап |
|---|---|---|
| AirSim / Cosys-AirSim overlay | Это отдельная связка Gazebo physics -> AirSim high-realism sensors, зона других исполнителей и отдельной интеграции | `2.2-airsim-overlay` |
| ~~Multi-UAV / swarm~~ | **MVP закрыт 21.05.2026**: 2 SITL + 2 Gazebo iris + mavp2p multi-router — см. backlog ниже | — |
| ~~QGroundControl как внешний GUI~~ | **Закрыто 21.05.2026** через `bluenviron/mavp2p` bridge — см. backlog ниже | — |
| ~~Полный real-time Sionna ray tracing~~ | **Закрыто 21.05.2026**: `sionna_channel_publisher.py --rt-online` делает live PathSolver на каждом UAV update — см. backlog ниже | — |
| ИССГР объектная БД/API/CV | Это полный грантовый контур, шире моделирующего стенда | Отдельный репозиторий/модуль |

## Backlog без обещаний

Эти пункты полезны, но не нужны для заявления “личная зона закрыта”:

1. ~~`--sionnaTargetFlow=control|payload|both` в `two_channel.cc`~~ — **закрыто
   21.05.2026**: CLI option в ns-3, default `payload` (back-compat),
   `BAS_SIONNA_TARGET_FLOW=both` по умолчанию включён в
   `run_stage_2_4_fpv_rf_demo.sh`. При NLOS RF model деформирует и видео-канал,
   и MAVLink-команды синхронно. Verified: `flow_id:"control+payload",
   loss_ratio:0.606, extra_delay_ms:109` в `ns3_events.jsonl`.
2. ~~QGroundControl bridge как дополнительный внешний GUI~~ — **закрыто
   21.05.2026**: `scripts/run_stage_2_4_qgc_demo.sh` поднимает
   `bluenviron/mavp2p` (MAVLink router) вместо `mavbridge` socat;
   `tcpc:5760 udps:14550 udps:14560` даёт одновременный доступ MAVProxy
   через ns-3 + QGC через host-side socat UDP relay (`0.0.0.0:14560 →
   10.10.0.2:14560`). QGC на Windows подключается на `<WSL eth0 IP>:14560`.
   Verified: 100+ MAVLink v2 frames/5с (HEARTBEAT, GPS, ATTITUDE, SYS_STATUS)
   reach host:14560. Pattern from `uxduck/ardupilot-sitl-docker` +
   `mavlink-router/Intel` + `bluenviron/mavp2p`. Docs:
   `docs/stage_2_4_qgc_setup.md`.
3. ~~Online Sionna RT (real-time ray tracing)~~ — **закрыто 21.05.2026**:
   `sionna_channel_publisher.py --rt-online` запускает live `PathSolver`
   call на каждое UAV-обновление позиции вместо `radio_maps/*.npz`
   lookup. Загружает Mitsuba scene `scene/iris_runway.xml` один раз
   (с runway + 3 obstacles + materials), на UAV update двигает
   `Receiver` и вызывает `rt.PathSolver()(scene, max_depth=2)`. WSL2 без
   OptiX SDK → pin `llvm_ad_mono_polarized` (CPU JIT), **~42-55мс per
   ray-tracing call** в нашей сцене — это 18Hz max, ns-3 поллит 10Hz.
   Wrapper `scripts/run_stage_2_4_rt_online_demo.sh` ставит
   `BAS_SIONNA_RT_ONLINE=1` + публикует в `/tmp/bas_stage24_rt.json`
   (отдельно от UI rf_loop в `/tmp/bas_stage24_rf.json`). Pattern из
   `robpegurri/ns3-rt` + paper "Ns3 meets Sionna" (arXiv 2412.20524) +
   5G LENA blog. Verified: `channel_model="rt_online"`,
   `channel_latency_ms=35.8-42.1`, `rss_db=-55.9`, `path_loss_db=78.9`,
   `loss_ratio=1.6e-05` (LOS).
4. ~~Multi-UAV topology в ns-3~~ — **MVP закрыт 21.05.2026**:
   `scripts/run_stage_2_4_multi_uav_demo.sh` поднимает 2 ArduCopter SITL
   экземпляра (`-I0 sysid=1`, `-I1 sysid=2`) + 2 iris модели в кастомном
   мире `gazebo/worlds/iris_runway_multi.sdf` (UAV1 fdm_port 9002, UAV2
   fdm_port 9012 через локальную копию модели `gazebo/models/
   iris_with_ardupilot_uav2/`) + единый `mavrouter-multi` (mavp2p
   tcpc:5760 + tcpc:5770 + udps:14550) multiplex-ит обоих в общий
   UDP endpoint для MAVProxy через ns-3. Verified: `mavp2p v1.3.2 router
   started with 3 endpoints; channel opened tcp:5760 sid=1; channel
   opened tcp:5770 sid=2; channel opened udp:GCS sid=255`. Pattern из
   `arthurrichards77/ardupilot_sitl_docker` + `Intelligent-Quads/iq_tutorials`.
   MVP single ns-3 channel для обоих UAV (общие радио-условия); Web UI
   пока показывает только UAV1 маркер — расширение per-UAV ns-3 каналов
   и multi-marker UI оставлено как extension backlog.
5. AirSim overlay: перенос pose из Gazebo в AirSim и возврат сенсорных потоков в
   payload channel.
6. ~~Автоматический demo recorder~~ — **закрыто 22.05.2026**:
   `scripts/run_stage_2_4_auto_demo.sh` запускает выбранный demo stack
   (default `run_stage_2_4_fpv_rf_demo.sh`), потом `auto_demo_recorder.py`
   (Playwright + ffmpeg) выполняет жёсткую траекторию (GUIDED → ARM →
   TAKEOFF → 5×GOTO с обходом ангара через LOS и NLOS → LAND → DISARM),
   снимает screenshots в каждой waypoint, параллельно пишет MJPEG поток
   с борта (`video/fpv.mjpeg.mp4` ~31MB/120с) и Playwright Web GCS
   capture (`video/web_gcs.webm` ~8MB), генерирует `demo_report.md`
   с timeline + ссылками на видео + ключевыми скриншотами. Verified
   end-to-end: 10/10 steps OK, NLOS-кадр поймал RSSI=−87.9 dBm,
   loss=62%, delay=111ms.
5. AirSim overlay: перенос pose из Gazebo в AirSim и возврат сенсорных потоков в
   payload channel.
6. Автоматический demo recorder: запуск сценария, браузер, Gazebo GUI и сбор
   видео/скриншотов в один отчёт.

## Где смотреть детали

- [architecture.md](architecture.md) — итоговая архитектура.
- [tz_compliance.md](tz_compliance.md) — матрица закрытия ТЗ.
- [stage_2_4_manual_gcs.md](stage_2_4_manual_gcs.md) — ручное управление и RF demo.
- [stage_1_7_lora_serial_plan.md](stage_1_7_lora_serial_plan.md) — LoRa Serial.
- [stage_1_8_mavros_plan.md](stage_1_8_mavros_plan.md) — MAVROS.
- [stage_2_1_sionna_plan.md](stage_2_1_sionna_plan.md) — Sionna RT.
