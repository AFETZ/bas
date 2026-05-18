# Регрессия Gazebo camera/gimbal — закрыта

**Статус:** закрыта. Демо-путь восстановлен в исходный v0.9-вариант: upstream
`iris_runway.sdf` + `iris_with_gimbal` модель с onboard gimbal POV camera.
Mission AUTO + Gazebo POV camera в одном прогоне проверены: landed=True,
7/7 waypoints, video_rx.mp4 ≈16 МБ (POV с борта дрона — видны лопасти
ротора и тень БАС на runway).

Корень проблемы — **апстрим gz-sim 8.11**, не сама модель `iris_with_gimbal`
и не `CameraZoomPlugin`. Pin'нем gz-sim8 на 8.10.0 в `docker/gazebo/Dockerfile`
+ сборка `ardupilot_gazebo` против этой версии — FDM JSON loop становится
стабильным, и iris_with_gimbal используется как штатная модель.

## История

1. **v0.9 (`eb93999`, 16.05.2026)** — `iris_with_gimbal` + `iris_runway.sdf`
   работали из коробки: real Gazebo camera POV, mission AUTO,
   `logs/stage_1_5_2_mission_wifi_good_20260516T124004Z` — landed=True,
   video_rx.mp4 14 МБ.
2. **17.05.2026** — rebuild контейнера `bas/gazebo-harmonic:dev` подтянул
   свежий `gz-harmonic` apt-метапакет, в котором `libgz-sim8` мигрировал
   с 8.10 на 8.11. В новом 8.11 нарушается ABI/таймаут plugin update loop
   ArduPilotPlugin; SITL начал писать `No JSON sensor message received,
   resending servos`, mission не стартовала. Это было ошибочно атрибутировано
   модели/`CameraZoomPlugin`, и в обход родилась локальная workaround-модель
   `bas_iris_with_pov_camera` + `iris_runway_ardupilot.sdf` (commit `6baabef`).
3. **18.05.2026** — workaround оказался отступлением от буквы ТЗ: вместо
   бортового POV получалось fake-PoV без видимых лопастей. Диагноз
   пересмотрен: в `docker/gazebo/Dockerfile` уже стоял pin `GZ_SIM8_VERSION=8.10.0-1~jammy`
   (`docker-compose.shared-netns.yml` + `apt-mark hold` через .deb из
   osrf-distributions S3). Этого хватило для FDM. Workaround-модель удалена,
   скрипты возвращены на upstream `iris_runway.sdf` + `iris_with_gimbal`,
   прогон landed=True повторён.

## Доказательство пост-фикса (commit роллбэка)

`tcpdump -i lo -nn udp port 9002 or udp port 9003` в bas-uav netns при
gimbal+iris_runway.sdf:

```
12:06:02.030256 IP 127.0.0.1.9002 > 127.0.0.1.47804: UDP, length 482
12:06:02.090734 IP 127.0.0.1.9002 > 127.0.0.1.47804: UDP, length 482
12:06:02.151276 IP 127.0.0.1.9002 > 127.0.0.1.47804: UDP, length 482
...
20 packets captured за ~1.2 c (rate ~16Hz = lock_step=1 JSON sensors → SITL)
```

Это именно ответы Gazebo `ArduPilotPlugin` (порт 9002) → SITL JSON-sensor
канал. До rollback'а в этом направлении было ноль пакетов под gz-sim 8.11.

Полный прогон с настоящей POV gimbal камерой:

```
logs/stage_1_5_2_mission_wifi_good_20260518T120647Z
RC=0
landed=True
waypoints=7/7
max altitude=30.0 м
control PDR=1.000
payload PDR=1.000
camera enable topic: /world/iris_runway/model/iris_with_gimbal/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming
camera tx sanity: video_tx.jsonl=131 записей за 16 с warm-up
video_rx.mp4=15.9 МБ
extracted frames 30s/50s/80s/100s/120s/150s/180s
  — лопасти + тень БАС видны над runway (frames 30-120s)
  — лопасти + травяное поле (frames 150-180s, поворот на дальние wp)
```

## Текущий стек

| Слой | Значение |
|---|---|
| Gazebo base image | `ubuntu:22.04` + OSRF `gazebo-stable` apt repo |
| gz-sim8 | **pin 8.10.0-1~jammy** (S3 .deb + `apt-mark hold`) |
| ardupilot_gazebo | upstream HEAD (`082a0fe`, Iris collisions, 2026-04-02) |
| World | upstream `iris_runway.sdf` (`/work/ardupilot_gazebo/worlds/`) |
| Model | upstream `iris_with_gimbal` (`/work/ardupilot_gazebo/models/`) |
| Camera | `gimbal_small_3d::pitch_link::camera` + `GstCameraPlugin` → 127.0.0.1:5600 |
| FDM bridge | ArduPilotPlugin UDP 9002/9003 (lock_step=1, JSON FDM) |
| Demo trigger | `gz topic -t .../enable_streaming -m gz.msgs.Boolean -p 'data: true'` |

## Команды для демо

Stage 1.5.2 (mission + Gazebo POV camera):

```bash
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

Stage 2.1 (то же + динамический Sionna RT канал):

```bash
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_2_1_sionna.sh
```

Synthetic fallback (для smoke без Gazebo камеры):

```bash
sudo env BAS_VIDEO_SOURCE=videotestsrc BAS_VIDEO_CAMERA_STRICT=0 \
  bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

GUI режим (открыть окно Gazebo через WSLg):

```bash
sudo env BAS_VIDEO_SOURCE=camera BAS_GAZEBO_GUI=1 \
  bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

Артефакты прогона в `logs/stage_1_5_2_mission_*` / `logs/stage_2_1_sionna_*`:

- `report.md` — summary по полёту, сети и видео.
- `events.jsonl` — timeline mission/telemetry.
- `ns3_events.jsonl` — события control/payload сети.
- `video_rx.mp4` — записанный received H.264 видеопоток (≥10 МБ под camera
  source, можно вытащить кадры через `ffmpeg -ss <t> -i video_rx.mp4 -frames:v 1`).

## Чего НЕ делать (анти-паттерны)

- **Не убирать pin gz-sim8 8.10.0 из Dockerfile.** Это и есть фикс. Если
  OSRF apt качнёт latest, FDM снова посыпется под 8.11.
- **Не создавать кастомные iris-модели для обхода.** `iris_with_gimbal`
  upstream — стандартная модель ArduPilot ↔ Gazebo demo path, она правильная.
  Любая локальная "POV camera attached к фиксированному link'у" — это
  отступление от ТЗ "видеопоток камеры Gazebo" с борта БАС.
- **Не игнорировать `[Wrn] CameraZoomPlugin: No scene or camera sensors
  available`.** Это безопасный warning от camera zoom плагина в headless
  режиме (он не находит scene manager на момент первой инициализации, но
  позже всё прицепляется). FDM от него не страдает.
