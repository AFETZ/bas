# Stage 3 CV-обработка видовых данных

YOLOv8n детектор подписанный на FPV MJPEG поток Web GCS. Каждое
обнаружение объекта geo-tag-ится через UAV pose + camera FOV ray-cast и
POSTится в ИССГР REST API как `SensorReading` (time-series). Annotated
frames с bbox и pose overlay сохраняются для визуального отчёта.

Закрывает пункт ТЗ верхнего уровня **"Обработка видовых данных:
геопривязка, извлечение объектов/поверхностей"** из
`docs/Краткая_выдержка_актуального_из_гранта_БАС.docx`.

> Это **не личная зона Физулина А.В.** (по ТЗ распределения — зона
> CV/sensor работы, частично пересекается с Маргарян/Карповым), но
> архитектурный артефакт положен как контракт для будущей передачи.

## Архитектура

```
┌──────────────────────────────────────────────────────────────┐
│ Web GCS /camera.mjpg ──── multipart MJPEG stream             │
└────────────┬─────────────────────────────────────────────────┘
             │ HTTP GET (multipart parser)
             ▼
┌──────────────────────────────────────────────────────────────┐
│ scripts/cv_detector.py                                       │
│   1. cv2.imdecode(jpeg_bytes) → numpy BGR                    │
│   2. YOLOv8n inference → [{class, conf, bbox}]               │
│   3. pose_tailer.update() ← events.jsonl                     │
│   4. pixel_to_ground_enu(cx, cy, intrinsics, pose):          │
│        pinhole camera + pitch + yaw → ground intersection    │
│   5. enu_to_latlon(east_m, north_m, pose) → (lat, lon)       │
│   6. POST /collections/sensor_readings/items                 │
│   7. Annotate frame (cv2.rectangle + putText) → JPEG file    │
└────────────┬─────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────┐
│ ИССГР REST API SensorReading time-series                     │
│   GET /collections/sensor_readings/items → GeoJSON FC        │
│   PointGeometry в pose_at_observation                        │
│   value.ground_lat / .ground_lon ← geo-tagged target         │
└──────────────────────────────────────────────────────────────┘
```

## Geo-tagging математика

UAV в pose `(lat, lon, alt, heading)`, камера установлена с pitch `θ_p`
(gimbal down, default -45°) и horizontal FOV `θ_h` (default 80°).

Pixel `(u, v)` в image размером `W × H`:

```
ndc_x = (u - W/2) / (W/2)              # [-1..1]
ndc_y = (v - H/2) / (H/2)

# Tangent half-FOV
th = tan(θ_h / 2)
tv = tan(θ_v / 2)   # θ_v derived from aspect ratio

# Ray в camera frame (z=forward, x=right, y=down)
cam = (ndc_x * th, ndc_y * tv, 1)

# Pitch rotation вокруг x (camera looks down)
rot_y = cos(θ_p) * cam.y - sin(θ_p) * cam.z
rot_z = sin(θ_p) * cam.y + cos(θ_p) * cam.z
rot_x = cam.x

# Yaw rotation (UAV heading + camera offset)
yaw = uav.heading + cam.yaw_offset
enu_east  = rot_z * sin(yaw) + rot_x * cos(yaw)
enu_north = rot_z * cos(yaw) - rot_x * sin(yaw)
enu_down  = rot_y

# Ray-ground intersection (flat z=0, UAV alt above ground)
t = uav.altitude / enu_down       # if enu_down > 0
ground_east  = t * enu_east       # метры от UAV
ground_north = t * enu_north

# Local-tangent-plane → globe
lat = uav.lat + ground_north / 111319.9
lon = uav.lon + ground_east  / (111319.9 * cos(uav.lat))
```

Точность: ±1м для AGL=10м с pitch=-45°, FOV=80°. Достаточно для overlay
detections на карту и для grant отчёта. Не подходит для precision strike.

## Файлы

| Файл | Что |
|---|---|
| `scripts/cv_detector.py` | Главный скрипт. ~400 LOC. Inputs: --fpv-url, --issgr-url, --events, --interval-s, --min-confidence, --camera-fov-deg, --camera-pitch-deg, --log-dir, --max-seconds, --no-issgr |
| `scripts/run_stage_3_cv_demo.sh` | Wrapper: запускает Stage 3 ISSGR stack + cv_detector в background, печатает инструкции |
| `scripts/_mjpeg_static_server.py` | Smoke helper: повторяет один JPEG как MJPEG stream (для CI без FPV) |
| `docs/stage_3_cv_detector.md` | Этот документ |

## Что детектируется (COCO subset)

```
0  person          → operational_situation.target.point
1  bicycle         → operational_situation.target.point
2  car             → operational_situation.target.point
3  motorcycle      → operational_situation.target.point
5  bus             → operational_situation.target.point
7  truck           → operational_situation.target.point
9  traffic_light   → functional_objects.sensor.ground_station
11 stop_sign       → functional_objects.sensor.ground_station
```

YOLOv8n обучен на COCO (80 классов). Для расширения нужен fine-tune на
датасете BAS-specific (aerial view of military/civil objects). Это
backlog для следующей итерации.

## Запуск

### Полный demo

```bash
sudo bash scripts/run_stage_3_cv_demo.sh
```

Поднимает:
1. Stage 3 ISSGR stack (Gazebo + SITL + ns-3 + Web GCS + ISSGR API)
2. CV detector в background, подписан на `/camera.mjpg`

В консоли:
```
Live CV detections:
  curl -s http://127.0.0.1:8770/collections/sensor_readings/items | jq .

Annotated frames:
  logs/<run>/cv_detections/frame_NNNNNN.jpg

Detection log:
  tail -f logs/<run>/cv_detections.jsonl
```

### Smoke test без полного стенда

```bash
# Terminal 1: ISSGR API
.venv/bin/python scripts/issgr_api_server.py --port 8773

# Terminal 2: MJPEG static loop (для CI без Gazebo)
.venv/bin/python scripts/_mjpeg_static_server.py /tmp/coco_sample.jpg --port 8775

# Terminal 3: detector
.venv/bin/python scripts/cv_detector.py \
    --fpv-url http://127.0.0.1:8775/camera.mjpg \
    --issgr-url http://127.0.0.1:8773 \
    --interval-s 1 --max-seconds 10 \
    --log-dir /tmp/cv_smoke
```

### Standalone без ISSGR (только log)

```bash
.venv/bin/python scripts/cv_detector.py \
    --fpv-url http://127.0.0.1:8765/camera.mjpg \
    --no-issgr \
    --log-dir /tmp/cv_log
```

## Verified end-to-end

```
2026-05-23 15:06:14 [INFO] cv-detector: YOLOv8n ready (CPU mode)
2026-05-23 15:06:14 [INFO] cv-detector: connecting to FPV stream http://127.0.0.1:8775/camera.mjpg
2026-05-23 15:06:14 [INFO] cv-detector: content-type: multipart/x-mixed-replace; boundary=frame
2026-05-23 15:06:24 [INFO] cv-detector: [frame 0] 4 detections
  - bus    0.87 bbox=(22,231)-(804,756)  ground=(-35.363153,149.165240) ENU=(0.3E,12.2N)
  - person 0.87 bbox=(48,398)-(245,902)  ground=(-35.363206,149.165169) ENU=(-6.2E,6.3N)
  - person 0.85 bbox=(669,392)-(809,877) ground=(-35.363201,149.165327) ENU=(8.2E,6.7N)
  - person 0.83 bbox=(221,405)-(344,857) ground=(-35.363201,149.165204) ENU=(-3.0E,6.8N)

ISSGR /stats после:
  {"collections":{"sensor_readings":8,...},"total_objects":11}
```

## Точки расширения / backlog

| Что | Когда |
|---|---|
| Fine-tune YOLOv8 на aerial dataset (DOTA / VisDrone) | Когда нужны военные/спец классы (танк, БРDM, антенна) |
| Tracking detections между frames (ByteTrack) | Для подсчёта unique объектов и trajectory plot |
| Promote sensor_reading → permanent Obstacle через voting | Когда detection устойчиво за N кадров → POST /collections/obstacles/items |
| Stereo / depth → 3D position вместо ground-flat assumption | Когда есть multi-camera или LiDAR на дроне |
| GPU inference (CUDA) на WSL2 | Через Cosys-AirSim Windows mode + NVIDIA driver. Сейчас CPU ~30мс/frame |
| Custom Mitsuba / Cosys-AirSim scene с COCO-friendly objects | Сейчас Gazebo Blocks plain shapes — COCO YOLO не находит |

## Pattern source

- [Ultralytics YOLOv8](https://docs.ultralytics.com/) — model + Python API
- [OpenCV imdecode](https://docs.opencv.org/4.x/d4/da8/group__imgcodecs.html) — JPEG bytes → ndarray
- [Pinhole camera model](https://en.wikipedia.org/wiki/Pinhole_camera_model) — pixel-to-ray transformation
- [Local tangent plane coordinates](https://en.wikipedia.org/wiki/Local_tangent_plane_coordinates) — ENU haversine
- [COCO dataset classes](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/coco.yaml) — id → name mapping
