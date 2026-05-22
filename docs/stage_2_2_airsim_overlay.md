# Stage 2.2 — Cosys-AirSim overlay

Архитектурный bridge между Gazebo физикой и Cosys-AirSim visual/sensor
рендером. Закрывает backlog-пункт "AirSim overlay" в рамках архитектуры,
**не** включая в репозиторий 10+ ГБ UE5 binary (оператор поднимает
Cosys-AirSim Editor отдельно на Windows или Linux-host с GPU).

## Зачем Cosys-AirSim, а не оригинальный AirSim

Решение принято по результатам анализа (Drive folder + GitHub):

| Критерий | Original AirSim | **Cosys-AirSim** |
|---|---|---|
| Поддержка | заброшен (Microsoft) | ✅ Активно (Cosys-Lab, KU Leuven) |
| Unreal Engine | UE4 (deprecated) | ✅ UE5.5 |
| ROS2 | community wrapper | ✅ Native C++ wrapper |
| Linux precompiled binary | устаревший | ✅ Ubuntu 22.04 + Vulkan |
| Sensors | RGB / depth / IMU / GPS | ✅ + GPU-LiDAR, RADAR, Echo Sonar, UWB/WiFi RF, annotation cameras |
| Physics | UE default | ✅ Custom high-frequency для multirotor |
| ArduPilot SITL | работает | ✅ Совместим (msgpack-rpc API без изменений) |

Источники:
- <https://github.com/Cosys-Lab/Cosys-AirSim>
- <https://cosys-airsim.com/>
- CHANGELOG.md → последний release `5.5-v3.3` (UE5.5, апрель 2026)

## Архитектура

```
┌──────────────────────────────────────┐
│  Gazebo iris + ArduPilot SITL        │
│  (физика, MAVLink, FDM)              │
└─────────┬────────────────────────────┘
          │ orchestrator events.jsonl
          │ {"event_type":"flight",
          │  "position":{"lat","lon","alt_rel_m","heading_deg"}}
          ▼
┌──────────────────────────────────────┐
│  scripts/airsim_bridge.py            │
│  ── tail events.jsonl                │
│  ── haversine: lat/lon → NED метры   │
│  ── yaw_deg → quaternion             │
│  ── msgpack-rpc: simSetVehiclePose   │
│  ── poll camera, LiDAR snapshots     │
└─────────┬────────────────────────────┘
          │ msgpack-rpc TCP :41451
          ▼
┌──────────────────────────────────────┐
│  Cosys-AirSim (UE5.5)                │
│  PROD режим:                         │
│    real UE Editor / packaged build   │
│    visual + sensor rendering         │
│  STUB режим:                         │
│    scripts/airsim_stub_server.py     │
│    msgpack-rpc API для CI smoke      │
└──────────────────────────────────────┘
```

Bridge — единственный артефакт в репозитории. UE5 binary остаётся
оператору как внешний компонент (по аналогии с QGroundControl на
Windows для Stage 2.4 QGC bridge).

## Файлы

- `scripts/airsim_client.py` — минимальный msgpack-rpc client (~250
  строк, без зависимости от legacy `airsim` PyPI pkg, который не
  собирается под Python 3.12).
- `scripts/airsim_stub_server.py` — msgpack-rpc stub-сервер
  (имитирует Cosys-AirSim API endpoint для headless smoke).
- `scripts/airsim_bridge.py` — Gazebo→AirSim pose forwarder +
  camera/LiDAR pull.
- `scripts/run_stage_2_2_airsim_overlay.sh` — wrapper, поднимает
  full stack (Gazebo + SITL + Web GCS + bridge + опциональный stub).

## Запуск

### Headless smoke (CI, default)

```bash
sudo bash scripts/run_stage_2_2_airsim_overlay.sh
```

Включает stub-сервер на 41451. Bridge подключается, forward'ит pose,
тратит ~0% дополнительного CPU. UE5 не нужен.

Артефакты:
```
logs/<run_id>/
  airsim_bridge.log             ← bridge stdout
  airsim_pose_forward.jsonl     ← каждый pose что отправлен (lat,lon,alt → NED)
  airsim_stub_pose.jsonl        ← что stub-сервер получил (sanity check)
  airsim_camera/                ← empty в stub (нет real renderer)
```

### Реальный Cosys-AirSim на Windows

1. Скачать precompiled binary с
   <https://github.com/Cosys-Lab/Cosys-AirSim/releases> (последний
   release `5.5-v3.3`, Windows .zip).
2. Распаковать. В корне будет `Blocks.exe` (или другая environment).
3. В `Documents/AirSim/settings.json` (создаётся при первом запуске)
   указать ArduPilot SITL:
   ```json
   {
     "SettingsVersion": 2.0,
     "SimMode": "Multirotor",
     "ClockType": "SteppableClock",
     "Vehicles": {
       "Copter": {
         "VehicleType": "ArduCopter",
         "UseSerial": false,
         "UseTcp": true,
         "TcpPort": 5760,
         "LocalHostIp": "<WSL_IP>",
         "ControlIp": "<WSL_IP>",
         "ControlPort": 9002,
         "Sensors": {
           "front_center_camera": { "SensorType": 7, "Enabled": true }
         }
       }
     }
   }
   ```
   `<WSL_IP>` — IP WSL eth0 (см. `wsl hostname -I`).
4. Запустить `Blocks.exe` (или другую environment) — UE5 окно с
   симуляцией откроется.
5. В WSL2 запустить наш bridge с указанием Windows IP:
   ```bash
   sudo env BAS_AIRSIM_STUB=0 BAS_AIRSIM_HOST=<WINDOWS_IP> \
        bash scripts/run_stage_2_2_airsim_overlay.sh
   ```
   `<WINDOWS_IP>` — IP хост-машины с Cosys-AirSim Editor; см.
   `ipconfig` на Windows.
6. Bridge подключится, начнёт пушить Gazebo pose → AirSim. UE5
   будет рисовать сцену с дроном в реальном времени.

### Реальный Cosys-AirSim на Linux (UE5)

Аналогично, но запустить Linux binary в WSL2 через WSLg или
на отдельной Linux-машине с GPU. См. <https://cosys-airsim.com/docs/install/linux>.

## Smoke test без UE5

```bash
sudo bash scripts/run_stage_2_2_airsim_overlay.sh
# (через 2 мин закрыть Ctrl+C)
tail -3 logs/<run_id>/airsim_pose_forward.jsonl
tail -3 logs/<run_id>/airsim_stub_pose.jsonl
```

Поля `airsim_pose_forward.jsonl`:
```json
{
  "wall_time": 1779380000.12,
  "lat": -35.363, "lon": 149.165, "alt_rel_m": 10.0,
  "ned_north": 0.0, "ned_east": 0.0, "ned_down": -10.0,
  "yaw_deg": 90.0,
  "rpc_sent": true,           ← false если AirSim не отвечает
  "rpc_error": null
}
```

## Известные ограничения / не закрыто

- **AirLib физика vs Gazebo физика**: Cosys-AirSim имеет свой
  high-frequency physics engine. В overlay-режиме мы передаём
  готовую pose из Gazebo через `simSetVehiclePose` — AirSim **не**
  делает свою физику, только renders. Это и есть "overlay" pattern.
  Для альтернативного "AirSim как physics" use case — переключать
  ArduPilot SITL на `--model AirSim` (а не наш `--model JSON` для
  Gazebo), это уже другая архитектурная конфигурация.
- **Sensor sync**: AirSim camera/LiDAR в overlay режиме рендерится
  в pose который ставит bridge; задержка bridge ↔ AirSim определяет
  jitter сенсорных данных. Для real-time SLAM это критично, для
  визуализации демо — приемлемо.
- **WSL2 GPU passthrough для UE5**: технически работает через WSLg
  + nvidia container toolkit, но требует значительной настройки.
  Рекомендуем оператору запускать Cosys-AirSim на Windows host или
  на отдельной Linux-машине.

## ТЗ-привязка

Per `docs/Проект_участия_группы_ПВАТС_УЛ_САПР_СтепанянцВГ_2026.docx`:

> Gazebo должен использоваться в качестве симулятора физики полета,
> результат моделирования которой должен быть передан в AirSim,
> который используется для высокореалистичного моделирования
> окружающей обстановки и сенсоров БАС.
>
> Исполнители: Андрончев А.Д., Федотенков А.А.

Это **не** личная зона Физулина А.В. (см. `docs/tz_compliance.md`),
но архитектурный интерфейс (`scripts/airsim_bridge.py`) — наш вклад
для последующей передачи Андрончеву/Федотенкову. Stub + docs
позволяют тестировать bridge в CI без полного UE5 стека.

## Pattern source

- <https://github.com/Cosys-Lab/Cosys-AirSim> — основной проект
- <https://ardupilot.org/dev/docs/sitl-with-airsim.html> — ArduPilot
  SITL ↔ AirSim integration официальный гайд
- <https://discuss.ardupilot.org/t/gsoc-2019-airsim-simulator-support-for-ardupilot-sitl/42890>
  — original GSoC integration
- Cosys-AirSim CHANGELOG (UE5.5, ROS2, GPU-LiDAR)
