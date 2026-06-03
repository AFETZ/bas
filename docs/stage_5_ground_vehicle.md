# Stage 5 — Наземный транспорт в среде моделирования (движок на выбор)

Закрывает пункт ТЗ «Разработка среды моделирования для БАС, **наземного
дорожного транспорта** и **наземного транспорта вне дорог**». БАС уже был на
ArduPilot ArduCopter; наземная техника добавлена с **выбором движка**:

| Движок | Чем хорош | Запуск |
|---|---|---|
| **ardupilot** (по умолч.) | ArduRover SITL — тот же ArduPilot-стек, что у БАС; реальная физика автопилота, MANUAL+RC | `--engine ardupilot` |
| **carla** | CARLA (пакет 0.9.12) — фотореалистичный UE4-движок из стека CAVISE; vehicle blueprints, реальные карты Town | `--engine carla` |

Пользователь сам выбирает, на чём моделировать наземный транспорт. **Оба движка
пишут в ИССГР один формат** `ground_vehicle.{wheeled,offroad}` под ручным
управлением — витрина и двойник показывают машину одинаково, независимо от
движка.

```bash
# ArduPilot ArduRover (реальный SITL):
bash scripts/run_stage_5_ground_vehicle_demo.sh --engine ardupilot

# CARLA (kinematic — без GPU; live — с запущенным CARLA сервером):
bash scripts/run_stage_5_ground_vehicle_demo.sh --engine carla --carla-mode kinematic
bash scripts/run_stage_5_ground_vehicle_demo.sh --engine carla --carla-mode live
```

---

## Движок 1 — ArduPilot ArduRover

ArduRover SITL даёт переиспользование MAVLink/ns-3/ИССГР-инфраструктуры без
нового симулятора.

## Что именно закрыто

| Класс техники | Frame ArduRover | Кинематика | Класс ИССГР |
|---|---|---|---|
| Наземный **дорожный** транспорт | `rover` | ackermann (как автомобиль) | `operational_situation.ground_vehicle.wheeled` |
| Наземный транспорт **вне дорог** | `rover-skid` | skid-steer (вездеход) | `operational_situation.ground_vehicle.offroad` |

Обе машины:
1. загружаются в SITL с реальной физикой ArduRover (`--model rover`),
2. отдают HEARTBEAT type=10 (`MAV_TYPE_GROUND_ROVER`),
3. управляются **вручную** (MANUAL mode + RC override руль/газ),
4. **реально едут** под EKF/GPS и
5. появляются и **двигаются** в цифровом двойнике ИССГР рядом с БАС.

## Архитектура

```
                 ручное вождение (RC override, sysid=255)
   rover_manual_drive.py ─────────────► ArduRover SITL ──┐ HEARTBEAT type=10
        (MANUAL + газ/руль)               (--model rover)│ GLOBAL_POSITION_INT
                                          instance 1     │
                                   SERIAL0:5770  SERIAL1:5772
                                                          │ телеметрия
                rover_to_issgr_publisher.py ◄─────────────┘
                  (GLOBAL_POSITION_INT → upsert)
                                │ POST /collections/uavs/items
                                ▼
                       ИССГР цифровой двойник :8770
                                │ GeoJSON FeatureCollection
                                ▼
                       Admin витрина :8810 (машина на карте рядом с БАС)
```

Ключ: драйвер шлёт команды в **SERIAL0** (5770, primary, single-client), а
publisher читает телеметрию из **SERIAL1** (5772) — так два клиента не
конфликтуют за единственный TCP-слот primary-порта SITL.

## Движок 2 — CARLA

CARLA приносит наземный транспорт из стека **CAVISE** (пакет `carla` 0.9.12,
conda-env `msvan3t_carla`). Реализован `scripts/carla_ground_vehicle.py` с двумя
под-режимами (`--carla-mode`):

| Режим | Что делает | Когда |
|---|---|---|
| `live` | Реальный CARLA сервер :2000: `world.spawn_actor(vehicle.*)` → ручной `carla.VehicleControl(throttle/steer)` → `Map.transform_to_geolocation` → ИССГР | есть запущенный CARLA UE4 сервер (GPU-хост) |
| `kinematic` | Велосипедная (bicycle) модель на чистом stdlib, та же геопривязка → ИССГР | без GPU/сервера (CI, демо) |
| `auto` | Пробует `live`; если сервера/пакета нет — `kinematic` | по умолчанию |

```
   ручное управление (throttle/steer S-маневр)
   carla.VehicleControl ──► CARLA сервер :2000 ──► vehicle.get_location()
        (live)                (vehicle.tesla/jeep)        │ transform_to_geolocation
                                                          ▼
   bicycle-модель (kinematic) ──► (восток_м, север_м) ──► lat/lon ──► ИССГР upsert
                                                                      ground_vehicle.*
```

Класс blueprints: дорожный (`wheeled`) → `vehicle.tesla.model3`; вне дорог
(`offroad`) → `vehicle.jeep.wrangler_rubicon` (4×4). Геопривязка единая: машина
выдаёт локальное ENU-смещение от референс-точки (по умолчанию home ArduRover),
поэтому CARLA-машина появляется там же, где ездил бы rover — рядом с БАС в
двойнике.

**Почему kinematic fallback, а не «нет CARLA — нет наземки»:** CARLA UE4 сервер,
как и AirSim, требует GPU-хост (в headless WSL2 — nullrhi). Kinematic-режим даёт
рабочую наземную модель в CI и без GPU, а live-путь активируется автоматически,
как только поднят сервер. Это тот же паттерн двух режимов, что у AirSim
(stub/linux/windows).

### Live CARLA через Windows-хост (как AirSim) — ПРОВЕРЕНО

CARLA сервер запускается на Windows с RTX (off-screen), WSL-клиент достаёт его по
сети:

```powershell
# 1) Windows: поднять CARLA сервер (off-screen, использует GPU):
C:\CARLA_0.9.12\WindowsNoEditor\CarlaUE4.exe -carla-rpc-port=2000 `
    -quality-level=Low -RenderOffScreen -nosound
```
```bash
# 2) WSL: запустить наземку на движке CARLA (host резолвится сам):
bash scripts/run_stage_5_ground_vehicle_demo.sh --engine carla --carla-mode live
```

`--carla-host auto` сам находит Windows-хост: в WSL2 NAT это default gateway
(`ip route`), сервер на нём слушает `:2000`. Перед запуском клиент делает
preflight-probe порта (если firewall блокирует — сразу понятная ошибка, а не
10-секундное зависание).

**Проверено на реальном сервере** (CARLA 0.9.12 на Windows RTX, WSL2→`172.30.16.1:2000`):
`reload_world` → spawn `vehicle.tesla.model3` в Town10HD → ручной throttle/steer
(до **11 м/с**) с авто-вы-застреванием (реверс при упоре в здание) → машина live
в ИССГР как `ground_vehicle.wheeled`, MANUAL, armed=True, max_dist ~76 м.

Тонкости, найденные при доводке live (закрыты в коде с комментариями):
- `client.reload_world()` на старте — иначе залипший после прошлого прогона
  synchronous-режим не даёт прогнать физику и машина стоит;
- `fixed_delta_seconds=0.05` (не 0.1) — на пределе substepping CARLA физика
  колёс не интегрируется;
- мягкий руль (0.08) — сильный S-манёвр за ~3с уводит машину в здание;
- origin снимается после 20 warmup-тиков (спавн чуть над землёй даёт (0,0)).

## Артефакты

| Файл | Роль |
|---|---|
| `scripts/carla_ground_vehicle.py` | **CARLA движок**: live (carla 0.9.12 spawn+VehicleControl+geoloc) + kinematic (bicycle) + ИССГР upsert |
| `scripts/_carla_ground_smoke.py` | Smoke CARLA kinematic → ИССГР (проехала >10 м). Вшит в `run_all_smokes.sh` (offline, CI) как `carla_ground_kinematic` |
| `scripts/_rover_sitl_smoke.py` | End-to-end smoke: SITL → GPS → ARM → проехал >10 м к цели. Вшит в `run_all_smokes.sh --live` как `rover_sitl_ground` |
| `scripts/rover_manual_drive.py` | Ручное вождение: MANUAL + force-ARM + RC throttle/steering с S-манёвром |
| `scripts/rover_to_issgr_publisher.py` | Интерфейс «наземный транспорт ↔ ИССГР»: MAVLink-телеметрия → upsert объекта `ground_vehicle.*` |
| `scripts/run_stage_5_ground_vehicle_demo.sh` | Live-демо: ИССГР + ArduRover + publisher + витрина + вождение, KPI каждые 5 с |
| `orchestrator/src/orchestrator/issgr/classifier.py` | Классы `OPS_GROUND_WHEELED` / `OPS_GROUND_OFFROAD` |

## Как запустить

```bash
# Дорожный автомобиль (ackermann):
bash scripts/run_stage_5_ground_vehicle_demo.sh

# Вездеход вне дорог (skid-steer):
BAS_ROVER_FRAME=rover-skid bash scripts/run_stage_5_ground_vehicle_demo.sh

# Только smoke (без витрины), в составе live-набора:
.venv/bin/python scripts/_rover_sitl_smoke.py --frame rover
bash scripts/run_all_smokes.sh --live      # rover_sitl_ground в списке
```

Браузер открывается на витрине `http://127.0.0.1:8810/` — наземная машина видна
как объект `ground_vehicle` и двигается по карте.

## Проверенный результат

* **Дорожный rover** (smoke): `Drove 35.1 м → closest 4.9 м to target`,
  HEARTBEAT type=10, ARMED=True.
* **Live-демо в ИССГР**: машина проехала ~120 м чистого смещения за окно демо,
  отображалась как `MANUAL armed=True`, класс `ground_vehicle.wheeled`, позиция
  обновлялась 0.4 с (`(-35.363284,149.165299) → (-35.362243,149.165679)`).
* **Off-road rover-skid**: грузится, армится, едет под skid-steer.

## Инженерные заметки (почему сначала не ехало)

Две неочевидные тонкости ArduPilot, найденные при интеграции:

1. **`RC_CHANNELS_OVERRIDE` принимается только от GCS с `sysid == SYSID_MYGCS`
   (по умолчанию 255).** Драйвер с любым другим source_system армит машину, но
   газ молча игнорируется — машина стоит. Решение: `source_system=255`.
2. **RC override «протухает» без живого GCS-heartbeat.** Драйвер шлёт
   `HEARTBEAT (MAV_TYPE_GCS)` в фоне 1 Гц, иначе ArduPilot считает линк мёртвым
   и сбрасывает override. Побочно: SITL роутит этот GCS-heartbeat и на
   телеметрийный порт, поэтому publisher фильтрует heartbeat по типу (берёт
   только автопилотный, не GCS), иначе `armed/mode` читаются с GCS как
   `False/0`.

Обе тонкости подтверждаются практикой ArduPilot SITL и закрыты в коде с
комментариями.
