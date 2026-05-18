# Этап 2.1 — Sionna RT: физически обоснованная радиокарта в ns-3

Замена статичного `RateErrorModel` в `two_channel.cc` на dynamic radio model,
параметры которой получены из ray-tracing'а Sionna RT по 3D-сцене Gazebo.

## Definition of Done

1. Sionna 0.18 (или новее) установлена в WSL Ubuntu-Restore, smoke на готовом
   reference-примере (`sionna.rt.examples`) даёт ray-traced radio map для
   пары TX/RX в простой сцене.
2. 3D-сцена `iris_runway.sdf` экспортирована в формат, который ест Sionna RT
   (Mitsuba 3 XML или OBJ/glTF). Включает: runway, iris model (точка), опциональные
   препятствия (здания/деревья).
3. Pre-compute скрипт `compute_radio_map.py` строит таблицу
   `(x, y, z) → (path_loss_dB, mean_delay_ns, doppler_hz)` на сетке точек
   над runway (grid например 50×50×5 = 12,500 точек).
4. ns-3 `SionnaErrorModel` (новый класс) загружает таблицу при старте,
   получает текущую UAV-позицию из orchestrator'а (через файл-watch или socket),
   делает 3D-interpolation и применяет path_loss → packet_drop, delay → channel_delay.
5. Новый run-скрипт `run_stage_2_1_sionna.sh` прогоняет mission с включённым
   Sionna-каналом; mission landed=True на `wifi_good_sionna` (низкая
   плотность препятствий) и наблюдаемая деградация при пересечении
   препятствий.
6. Сравнительный отчёт `wifi_good` (manual `RateErrorModel`) vs
   `wifi_good_sionna` (ray-traced): video frame loss и control PDR должны
   коррелировать с **позицией УАВ относительно препятствий**, не быть
   статичными по времени.
7. Секция «Sionna radio map» в `report.md` с heatmap-картинкой
   `radio_map_<run_id>.png` (matplotlib) — path_loss как функция (x, y) для
   фиксированного z.

## Текущая инфраструктура (что готово)

Подтверждено по survey репозитория, переиспользуется как есть:

| Артефакт | Где |
|---|---|
| 3D-сцена runway + iris model | `gazebo` контейнер: `gazebo/worlds/iris_runway_ardupilot.sdf` + `model://bas_iris_with_pov_camera` |
| ns-3 base scenario | `ns3/scenarios/two_channel.cc` (control + payload TAP) |
| Orchestrator с position events | `events.jsonl`: `event_type="flight"` с `position.{lat,lon,alt_rel_m}` |
| Comparison analyzer | `bas-analyzer-compare` (v1.0) — сравнит wifi vs wifi_sionna |
| WSL Ubuntu-Restore + Python 3.12 | `.venv/` |

Чего нет и придётся создать:
- Sionna + TensorFlow в Python venv (или отдельный venv `sionna_env/`)
- Mitsuba 3 renderer (опционально — для визуализации сцены)
- Конвертер SDF → Mitsuba XML / glTF (`scripts/export_scene_to_sionna.py`)
- Sionna RT compute-скрипт (`scripts/compute_radio_map.py`)
- Кастомный `SionnaErrorModel` в ns-3 (`ns3/scenarios/sionna_error_model.{h,cc}`)
- IPC между orchestrator и ns-3 для текущей UAV-позиции
  (вариант: файл `/tmp/uav_position.txt`, ns-3 опрашивает 10 Hz)
- Новый профиль `configs/network_profiles/sionna_urban.yaml`
- Новый run-скрипт `scripts/run_stage_2_1_sionna.sh`

## Архитектура

```
                                      OFFLINE (один раз перед прогоном):
                                      ┌────────────────────────────────┐
                                      │ iris_runway.sdf                │
                                      │   ↓ export_scene_to_sionna.py  │
                                      │ scene/iris_runway.xml          │ (Mitsuba 3 format)
                                      │   ↓ compute_radio_map.py       │
                                      │ radio_maps/iris_runway.npz     │ (3D-grid table)
                                      └────────────────────────────────┘
                                                       │
                                                       ▼
ONLINE (при mission run):
┌──────────────┐                              ┌─────────────────────────┐
│ Gazebo       │  flight events               │ ns-3 two_channel_sionna │
│ + SITL       │ ───────────────────────────► │ + SionnaErrorModel      │
│ (UAV pose)   │       /tmp/uav_pos           │ (lookup в radio_map.npz │
└──────────────┘                              │  по текущей UAV pose,   │
        │                                     │  применяет dynamic loss │
        │ MAVLink                             │  и delay к каналу)      │
        ▼                                     └─────────────────────────┘
   bas-ctrl-far ─────► tap-ctrl-near ─────► ns-3 control TAP
                       (dynamic loss/delay из Sionna lookup)
```

`/tmp/uav_pos` — простой файл с тремя float'ами (x, y, z) в метрах,
обновляется orchestrator'ом каждые ~100 мс из flight events. ns-3 читает
его в callback'е `SionnaErrorModel::ShouldDrop`. Без сокета чтобы не
ломать существующий ns-3 build.

Альтернатива (если IPC через файл слишком грубо): добавить UDP-listener
внутри ns-3 на отдельном порту, orchestrator шлёт обновления как
mini-JSON. Сложнее, оставлю как вариант B если файл-IPC окажется
неточным.

## Под-этапы

### 2.1.a — Sionna setup в WSL (~2-3 дня)

| Артефакт | Что |
|---|---|
| `sionna_env/` (новый Python venv) | Отдельный venv для Sionna (TensorFlow + Mitsuba), не смешивать с orchestrator/.venv |
| `requirements_sionna.txt` (новый) | `sionna==0.18`, `tensorflow==2.15`, `mitsuba==3.5`, `matplotlib`, `numpy<2.0` |
| `scripts/setup_sionna.sh` (новый) | bootstrap venv + pip install + smoke import test |
| smoke run | `python -c "import sionna.rt; sionna.rt.PreviewWidget" → SUCCESS` |

Acceptance: импорт `sionna.rt` работает, reference example
(`sionna.rt.examples.demo_scene`) генерирует пустую radio map без ошибок.
GPU не обязателен — Sionna работает на CPU (медленнее на ~5x).

### 2.1.b — Geometry export (~3-4 дня)

| Артефакт | Что |
|---|---|
| `scripts/export_scene_to_sionna.py` | Скрипт чтения SDF (через библиотеку `parse-sdf` или ручной XML-парсинг) + сборка Mitsuba XML с правильными материалами |
| `scene/iris_runway.xml` (генерируемый) | Mitsuba 3 scene file: runway plane, опциональные boxes-препятствия с radio-material'ами (`itu_concrete`, `itu_glass`) |
| `scene/materials_radio.xml` | Радио-материалы Sionna: бетон (10 GHz, ε=4.5), стекло, металл |
| Verification: визуализация в Mitsuba Preview | scene.preview() → отображает 3D-сцену с iris и runway |

Acceptance: scene открывается в Sionna без ошибок,
`scene.add_tx(position=[0,0,1])` и `scene.add_rx(position=[100,0,30])`
работают, ray-tracing запускается.

**Open question:** какие препятствия добавить в сцену? Варианты:
- (a) только runway (минимально, line-of-sight канал, нет потерь от препятствий)
- (b) 2-3 здания вдоль маршрута (демонстрирует occlusion-loss)
- (c) лес/деревья (диффузное рассеяние)

Решение: начать с (a) для smoke, добавить (b) перед verification 2.1.g.

### 2.1.c — Radio map compute (~2-3 дня)

| Артефакт | Что |
|---|---|
| `scripts/compute_radio_map.py` | Sionna RT compute: TX в фиксированной точке (GCS у runway center, alt=1m), RX на сетке (x: -100..100m step 10m, y: -50..50m step 5m, z: 0..50m step 10m) = ~50×20×6 = 6000 точек. Для каждой точки: path_loss, mean_delay, K-factor (Rice) |
| `radio_maps/iris_runway.npz` | numpy archive: `path_loss_db[X,Y,Z]`, `delay_ns[X,Y,Z]`, `kfactor[X,Y,Z]`, `grid_x[]`, `grid_y[]`, `grid_z[]` |
| matplotlib plot: heatmap path_loss vs (x,y) для z=10m | `radio_maps/iris_runway_heatmap.png` |

Sionna parameters:
- Carrier frequency 2.4 GHz (WiFi) или 868 MHz (LoRa EU868)
- TX/RX antennas: ITU-R `iso` (isotropic) или `dipole`
- `max_depth=3` ray bounces (баланс accuracy vs compute time)
- `synthetic_array=True` для скорости

Acceptance: heatmap имеет физически разумную форму — high loss
далеко от GCS, низкие потери близко; если препятствия есть — видна
тень за ними.

### 2.1.d — SionnaErrorModel в ns-3 (~5-7 дней)

| Артефакт | Что |
|---|---|
| `ns3/scenarios/sionna_error_model.h` | Класс `SionnaErrorModel : public ns3::ErrorModel` с методами `LoadFromNpz`, `SetUavPosition`, `DoIsCorrupt(Ptr<Packet>)` |
| `ns3/scenarios/sionna_error_model.cc` | Реализация: NPZ-loader через cnpy lib (header-only), 3D-interpolation (trilinear), drop probability = `1 - exp(-path_loss_dB / 30)` (примерная связь loss→PER) |
| `ns3/scenarios/two_channel_sionna.cc` | Копия `two_channel.cc` с подменой `RateErrorModel` на `SionnaErrorModel`, плюс PollTimer чтения `/tmp/uav_pos` каждые 100ms |
| `ns3/CMakeLists.txt` или скрипт сборки | Сборка нового scenario вместе с two_channel |

Acceptance: ns-3 запускается с `two_channel_sionna` без сегфолтов;
при подмене `/tmp/uav_pos` ns-3 видит разницу в применяемом loss
(можно проверить через debug-print или метрики).

### 2.1.e — Real-time UAV position integration (~3-4 дня)

| Артефакт | Что |
|---|---|
| `orchestrator/src/orchestrator/sionna_position_publisher.py` | Поток в orchestrator: подписывается на `flight` events, конвертирует lat/lon/alt → метры (через `_haversine_m` уже есть в analyzer), пишет в `/tmp/uav_pos` (text file: `x y z timestamp`) |
| `run.py` integration | при `--sionna-channel` поднимать publisher параллельно с MissionRunner |
| ns-3 PollTimer в `two_channel_sionna.cc` | Читает `/tmp/uav_pos` каждые 100 ms, обновляет `SionnaErrorModel::current_uav_pos` |

Acceptance: при mission AUTO UAV пролетает grid, ns-3 видит обновляющуюся
позицию (debug-log показывает изменения каждые 100ms), drop probability
меняется в зависимости от позиции.

### 2.1.f — Profile sionna_urban.yaml (~1 день)

```yaml
profile_id: sionna_urban
transport: ns3_sionna
link_type: wifi_24ghz
description: "Physically-justified WiFi channel from Sionna RT raytracing of iris_runway scene"
parameters:
  radio_map_path: "radio_maps/iris_runway.npz"
  carrier_ghz: 2.4
  tx_position_m: [0.0, 0.0, 1.0]  # GCS у точки старта
  poll_interval_ms: 100
  # fallback fixed params для пакетов вне radio-map grid
  default_path_loss_db: 80.0
  default_delay_ms: 10.0
log_fields:
  - radio_map_path
  - carrier_ghz
  - tx_position_m
```

### 2.1.g — Verification: wifi_good vs wifi_good_sionna (~2-3 дня)

| Артефакт | Что |
|---|---|
| `scripts/run_stage_2_1_sionna.sh` | Полный прогон mission с `--network.payload_channel.profile=sionna_urban` |
| `scripts/run_stage_2_1_compare.sh` | Двойной прогон: wifi_good (manual) и wifi_good_sionna (ray-traced) на одинаковой trajectory + автоматическое сравнение через `bas-analyzer-compare` |
| Acceptance check | Mission landed=True в обоих, но в sionna-варианте видны time-correlated всплески потерь когда UAV пересекает «тень» от препятствий (если они в сцене) |

### 2.1.h — Секция «Sionna radio map» в report.md (~1-2 дня)

| Артефакт | Что |
|---|---|
| `analyzer/src/analyzer/sionna_section.py` | (опционально) Парсер `radio_map_<run_id>.png` и добавление в markdown |
| Расширение `report.py` | Если в run-dir есть `radio_map.png`, embed её в report.md под секцией «Sionna radio map» с краткой статистикой (min/max path_loss, % сцены покрытой LoS) |

## Open questions

1. **GPU vs CPU**: Sionna RT работает на CPU, но медленно. На WSL2 без GPU
   радио-карта 6000 точек может строиться 20-60 минут. Если есть NVIDIA
   GPU и WSL CUDA — ускорится 5-10x. Какие у нас опции?
2. **Carrier frequency**: WiFi 2.4 GHz или LoRa 868 MHz? Для двух
   каналов (control + payload) разные несущие — нужно две radio maps
   или одна универсальная?
3. **Препятствия**: ТЗ упоминает «3D-препятствия», но не уточняет
   какие. Стартовый набор — 2-3 здания вдоль runway, потом можно
   масштабировать до city-scene если будет grant-deadline.
4. **Sionna version**: 0.18 или 0.19? Последний может требовать TF 2.16+
   и Python 3.11+. Зафиксировать в `requirements_sionna.txt`.
5. **IPC файл vs UDP socket**: для real-time UAV pos. Файл проще, но
   может терять updates под нагрузкой. UDP socket точнее но добавляет
   сложность в ns-3. Решение: начать с файла, мигрировать в UDP если
   будут видны race-conditions.

## Acceptance criteria (полный 2.1)

1. `sudo bash scripts/run_stage_2_1_sionna.sh wifi_good` — mission landed=True
   с использованием Sionna radio map; report.md содержит секцию
   «Sionna radio map».
2. `sudo bash scripts/run_stage_2_1_compare.sh` — comparison.md показывает
   wifi_good (manual) vs wifi_good_sionna (ray-traced); в sionna-варианте
   loss-spikes коррелируют с UAV-position относительно препятствий.
3. Документация:
   - `docs/architecture.md`: 2.1 помечен как готов.
   - `docs/tz_compliance.md`: пункты «Sionna RT» и «карта 3D препятствий» — закрыто.
   - README.md: новая секция «Этап 2.1: ray-traced radio channel».
4. Tag `v2.0-sionna` на main.

## Порядок работы по сессиям

| Сессия | Что делаем | Результат |
|---|---|---|
| 1 | 2.1.a setup + smoke Sionna в WSL | Sionna импортируется, reference example работает |
| 2 | 2.1.b geometry export для iris_runway | scene.xml в Mitsuba формате |
| 3 | 2.1.c radio map compute + heatmap | iris_runway.npz + heatmap.png |
| 4 | 2.1.d SionnaErrorModel + ns-3 build | two_channel_sionna.cc собирается, smoke без UAV-pos |
| 5 | 2.1.e real-time UAV-pos publisher + integration | mission прогон работает с обновляемой позицией |
| 6 | 2.1.f-h profile + verification + report section | comparison wifi vs wifi_sionna в одном отчёте |
| 7 | docs + tag v2.0-sionna | этап закрыт |

Один заход = одна сессия. Между сессиями коммитим, чтобы не терять контекст
в случае compaction.

## Связь с другими этапами

- **1.7 LoRa Serial Bridge**: можно делать параллельно (другой канал, не
  конфликтует).
- **1.8 ROS2/MAVROS**: ортогонально, Sionna влияет только на ns-3-слой.
- **2.3 Multi-UAV**: Sionna при N>1 потребует расчёта radio map для каждой
  пары TX-RX, либо использовать ту же map для всех (упрощение).
- **2.4 Ручное управление**: ортогонально.
