# Регрессия Gazebo camera/gimbal — текущий статус

**Статус:** рабочий demo path восстановлен, включая Gazebo camera video.
SITL + Gazebo + ns-3 миссии проходят на стабильном ArduPilot FDM plugin stack,
а видеопоток Gazebo-камеры даёт fixed onboard POV camera на модели
`iris_with_pov_camera`. Мир с `iris_with_gimbal` остаётся known-bad режимом и
включается только явно для отладки старой gimbal/zoom-камеры.

## Что ломалось

Проблемный старый режим:

```bash
sudo env BAS_GAZEBO_WORLD=iris_runway.sdf BAS_VIDEO_SOURCE=camera \
  bash scripts/run_stage_2_1_sionna.sh
```

Симптомы:

- Gazebo загружает `ArduPilotPlugin`.
- Plugin bind'ит UDP `127.0.0.1:9002` внутри `bas-uav` netns.
- SITL подключается к serial `5760` и шлёт servo-пакеты на UDP `9002`.
- Plugin не отправляет JSON sensor-пакеты обратно в SITL.
- SITL бесконечно пишет `No JSON sensor message received, resending servos`.
- MAVLink HEARTBEAT не доходит до orchestrator, поэтому AUTO mission не
  стартует.

Та же ошибка воспроизводилась после rebuild `ardupilot_gazebo`, после попытки
`lock_step=0`, и после pin Gazebo Sim пакетов обратно на `gz-sim 8.10.0`.
То есть старая гипотеза "только gz-sim 8.11 regression" оказалась неполной.

## Доказательство

`tcpdump` в loopback namespace `bas-uav` показал точную форму FDM-сбоя:

- С `iris_with_gimbal`: SITL шлёт UDP на `127.0.0.1:9002`, но ответов
  `9002 -> SITL` с JSON sensors нет.
- С `iris_with_ardupilot`: UDP сразу становится двусторонним; SITL пишет
  `JSON received`, затем mission нормально идёт дальше.

Probe-прогоны:

- Падающий camera/gimbal probe:
  `logs/fdm_probe_bridge_20260517T211650Z`
- Успешный camera-free probe:
  `logs/fdm_probe_no_camera_20260517T212134Z`

Главное различие — модель/мир Gazebo, а не ns-3, mavbridge или orchestrator.

## Реализованный фикс

Добавлена локальная модель и мир:

- `gazebo/models/bas_iris_with_pov_camera/model.sdf`
- `gazebo/worlds/iris_runway_ardupilot.sdf`

`bas_iris_with_pov_camera` основана на стабильной `iris_with_ardupilot` /
`iris_with_standoffs` схеме, но добавляет fixed link `pov_camera_link` с
`sensor name="pov_camera"` и `GstCameraPlugin`. Камера закреплена на БАС,
смотрит вперёд с лёгким наклоном вниз и отдаёт RTP на `127.0.0.1:5600`.
Старые `gimbal_small_3d` и `CameraZoomPlugin` не используются.

Поведение по умолчанию:

- Все video source, включая `BAS_VIDEO_SOURCE=camera`, по умолчанию используют
  `iris_runway_ardupilot.sdf`.
- `BAS_VIDEO_SOURCE=camera` включает onboard POV camera через
  `/world/iris_runway/model/iris_with_pov_camera/link/pov_camera_link/sensor/pov_camera/image/enable_streaming`
  и retap'ит H.264 RTP с `127.0.0.1:5600` в payload channel.
- Старый `iris_with_gimbal` можно включить только руками через
  `BAS_GAZEBO_WORLD=iris_runway.sdf`.
- Любой другой `BAS_GAZEBO_WORLD=...` по-прежнему может переопределить мир.

Это возвращает demo path с настоящей SITL/Gazebo динамикой полёта, MAVLink
mission control, ns-3 control/payload сетью, analyzer metrics и Gazebo camera
video от лица БАС без gimbal/zoom плагинов.

## Проверенные успешные прогоны

Stage 1.5.2 onboard Gazebo POV camera:

```text
logs/stage_1_5_2_mission_wifi_good_20260518T094329Z
RC=0
video_rx.mp4=14M
camera=iris_with_pov_camera::pov_camera_link::pov_camera
landed=True
waypoints=7/7
max altitude=30.0 m
control PDR=1.000
payload PDR=1.000
```

Stage 1.5.2 synthetic video:

```text
logs/stage_1_5_2_mission_wifi_good_20260517T212349Z
RC=0
video_rx.mp4=2.5M
landed=True
waypoints=7/7
```

Stage 2.1 Sionna dynamic channel + synthetic video:

```text
logs/stage_2_1_sionna_wifi_good_20260517T212800Z
RC=0
video_rx.mp4=2.4M
landed=True
waypoints=7/7
max altitude=30.0 m
control PDR=1.000
payload PDR=1.000
sync events=19
mean real_time_factor=1.000
max position desync=0.00 m
```

## Команды для демо

Рекомендуемый текущий демо-прогон с Gazebo camera:

```bash
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_2_1_sionna.sh
```

Synthetic fallback:

```bash
sudo env BAS_VIDEO_SOURCE=videotestsrc BAS_VIDEO_CAMERA_STRICT=0 \
  bash scripts/run_stage_2_1_sionna.sh
```

Артефакты будут в `logs/stage_2_1_sionna_*`:

- `report.md` — summary по полёту, сети и видео.
- `events.jsonl` — timeline mission/telemetry.
- `ns3_events.jsonl` — события control/payload сети.
- `video_rx.mp4` — записанный received video.

Stage 1.5.2 без Sionna:

```bash
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

## Что ещё открыто

Старый iris gimbal camera path всё ещё сломан:

```bash
sudo env BAS_GAZEBO_WORLD=iris_runway.sdf BAS_VIDEO_SOURCE=camera \
  bash scripts/run_stage_2_1_sionna.sh
```

Следующий полезный debug step — внутри `iris_with_gimbal` / rendering path:
сравнить Gazebo update/sensor callbacks с `iris_with_ardupilot`, затем
убирать или изолировать gimbal camera части, пока FDM ответы не вернутся.
