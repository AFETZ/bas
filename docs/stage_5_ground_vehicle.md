# Stage 5 — Наземный транспорт (ArduRover) в среде моделирования

Закрывает пункт ТЗ «Разработка среды моделирования для БАС, **наземного
дорожного транспорта** и **наземного транспорта вне дорог**». БАС уже был на
ArduPilot ArduCopter; наземная техника добавлена на том же ArduPilot-стеке через
**ArduRover SITL** — это даёт переиспользование MAVLink/ns-3/ИССГР-инфраструктуры
без нового симулятора.

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

## Артефакты

| Файл | Роль |
|---|---|
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
