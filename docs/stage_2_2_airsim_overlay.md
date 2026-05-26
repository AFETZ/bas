# Stage 2.2 — Cosys-AirSim overlay (полный deploy)

Архитектурный bridge между Gazebo физикой и Cosys-AirSim visual/sensor
рендером. Включает **готовый Linux UE5 packaged binary** (auto-download
~637 MB) который запускается headless из wrapper'а; UE5 binary остаётся
вне git репозитория (как Docker images), но wrapper сам качает + ставит
+ запускает его.

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

### Четыре режима

| `BAS_AIRSIM_MODE` | Что запускается | Image API |
|---|---|---|
| `stub` (default) | scripts/airsim_stub_server.py — msgpack-rpc API stub | empty (CI smoke) |
| `linux` | Cosys-AirSim Blocks Linux auto-download → headless `-nullrhi` | empty (WSL2 нет NVIDIA Vulkan ICD; DZN не поддерживает UE5 SM6) |
| **`windows`** | **Cosys-AirSim Blocks.exe auto-download → cmd.exe interop, native Windows + RTX 5070 Ti GPU rendering** | ✅ **РЕАЛЬНЫЕ PNG** (verified 7 cameras: front_center, fpv, etc.) |
| `off` | ничего; bridge connects к external AirSim на `BAS_AIRSIM_HOST` | зависит от того что у оператора |

### Headless smoke (CI, default)

```bash
sudo bash scripts/run_stage_2_2_airsim_overlay.sh
```

Включает stub-сервер на 41451. Bridge подключается, forward'ит pose,
тратит ~0% дополнительного CPU. UE5 не нужен.

### Полный deploy с реальным Cosys-AirSim Linux build

```bash
sudo env BAS_AIRSIM_MODE=linux bash scripts/run_stage_2_2_airsim_overlay.sh
```

Wrapper:
1. Скачивает `Blocks_packaged_Linux_55_33.zip` (637 MB) с GitHub
   releases в `~/cosys-airsim/` если ещё нет;
2. Распаковывает (после первого раза кэшируется);
3. Создаёт `~/Documents/AirSim/settings.json` с
   `SimMode=Multirotor`, `SimpleFlight`, `ApiServerEndpoint=0.0.0.0:41451`;
4. Запускает Blocks headless: `./Blocks.sh -RenderOffscreen -nullrhi
   -nosound -nosplash`;
5. Ждёт пока API endpoint :41451 откроется (≈10–30 с);
6. Запускает базовый Stage 2.4 stack;
7. Запускает bridge — pose forwarding в **реальный** Cosys-AirSim
   UE5 рендер, server_version=4, 200+ scene objects.

Verified end-to-end:
```
[airsim] connected 127.0.0.1:41451 ping=True server_version=4
[airsim] scene objects (209): ['ChaosDebugDrawActor', 'Cone_5',
         'Cylinder2', 'Cylinder3', ...]
[airsim] detected REAL Cosys-AirSim (server v4)
[airsim-bridge] mode=RPC

360 pose forwards (lat,lon,alt → NED) → AirSim simSetVehiclePose
```

`-nullrhi` mode даёт работающий AirSim plugin (annotation system, 200+
объектов, multirotor SimpleFlight, RPC API) **без** GPU rendering. Это
полноценное архитектурное доказательство overlay pattern: bridge
действительно говорит с UE5 + AirSim plugin, не stub. Camera/LiDAR API
calls возвращают empty bytes — для реального rendering нужен либо
`-RenderOffscreen` без `-nullrhi` (LLVMpipe software, медленно), либо
NVIDIA GPU passthrough в WSL2, либо запуск Windows-build.

Артефакты:
```
logs/<run_id>/
  airsim_bridge.log             ← bridge stdout
  airsim_pose_forward.jsonl     ← каждый pose что отправлен (lat,lon,alt → NED)
  airsim_stub_pose.jsonl        ← что stub-сервер получил (sanity check)
  airsim_camera/                ← empty в stub (нет real renderer)
```

### Полный deploy через `BAS_AIRSIM_MODE=windows` (рекомендованный для real GPU)

```bash
sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh
```

Wrapper:
1. Скачивает `Blocks_packaged_Windows_55_33.zip` (556 MB) в
   `/mnt/c/Users/$USER/cosys-airsim/`
2. Распаковывает через `Expand-Archive` (PowerShell)
3. Создаёт `/mnt/c/Users/$USER/Documents/AirSim/settings.json` с
   `ApiServerEndpoint=0.0.0.0:41451`
4. Запускает `Blocks.exe` через `cmd.exe /c start /B` (детач)
5. Determines Windows host IP (gateway = `ip route show default`,
   обычно `172.30.16.1` в WSL2 NAT mode)
6. Запускает bridge с `BAS_AIRSIM_HOST=<windows-ip>`

**ОДНОРАЗОВАЯ настройка Windows Firewall** (если refused):

В админ-PowerShell на Windows:
```powershell
New-NetFirewallRule -DisplayName "CosysAirSim 41451" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 41451
```

или в WSL → admin cmd через RunAs:
```bash
powershell.exe -Command 'Start-Process cmd.exe -Verb RunAs -ArgumentList "/c","netsh advfirewall firewall add rule name=CosysAirSim41451 dir=in action=allow protocol=TCP localport=41451"'
```

Verified end-to-end (real Windows GPU rendering):
```
ping=True, server_version=4
209 scene objects
simGetImage "front_center" → 45718 bytes PNG (256×144 RGBA)
simGetImage "0"             → 57074 bytes PNG
simGetImage "fpv"           → 43252 bytes PNG
simGetImage "back_center"   → 36686 bytes PNG
simGetImage "bottom_center" → 39250 bytes PNG
```

### Manual install (alternative)

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
  Для альтернативного closed-loop physics use case используйте Stage 4
  `JsonFdmBridge`: ArduPilot `--model json:127.0.0.1` → internal X-config
  6DOF dynamics → IMU/GPS/quaternion JSON обратно в SITL. Это отдельный
  путь от Gazebo overlay и verified real ARM+takeoff smoke'ом.
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
