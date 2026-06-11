# Видеодемо: проход по ТЗ ↔ что реально работает в проекте

Сценарий для видео по пунктам ТЗ группы ПВАТС УЛ САПР. Для каждого пункта:
кто делал · честный статус · что показать на экране · **проверенная команда** ·
что зритель видит и почему это важно.

Главный принцип ролика: **показываем только то, что устойчиво работает и имеет
практический смысл.** Тяжёлые полные прогоны — заранее записанным фрагментом или
как готовый артефакт; быстрые проверки — живьём, они срабатывают за секунды.

> Репозиторий: `git@github.com:AFETZ/bas.git` · ветка `main`
> Ограничения честно: [docs/LIMITATIONS.md](LIMITATIONS.md)
> Реплики и тайминги: [docs/DEMO_VIDEO_SCENARIO.md](DEMO_VIDEO_SCENARIO.md)

## Легенда статуса (проговаривать в кадре)

- 🟢 **РЕАЛЬНО** — живая связка, проверяемый артефакт прогона.
- 🟡 **ФУНКЦ. ЭКВИВАЛЕНТ** — работает по-настоящему, но реализация проще буквы ТЗ.
- 🔵 **RESEARCH/SYNTH** — исследовательский стенд на синтетике, не железо.

---

## Режиссура: что показывать живьём, а что из записи

Перед съёмкой проверено вживую 2026-06-09 — раздел «зелёной зоны» отрабатывает
полностью.

### 🟢 Зелёная зона — запускать ЖИВЬЁМ (секунды, всегда срабатывает)

Эти команды без `sudo` и без GPU, дают результат за 3–20 секунд и печатают
`ALL CHECKS PASSED`. Это самый выигрышный материал — настоящий результат сразу.

```bash
.venv/bin/python scripts/_real_sitl_e2e_smoke.py        # 2.1  реальный ArduCopter: ARM + взлёт (~18 c)
bash          scripts/run_stage_4_sim_bridges_demo.sh smoke   # 2.3  мосты ArduPilot↔AirSim/Gazebo (~5 c)
.venv/bin/python scripts/_cyber_smoke.py                # 2.5  кибератаки/защита (~7 c)
.venv/bin/python scripts/_large_map_smoke.py            # 3.4  карты >20×20 км (~3 c)
.venv/bin/python scripts/_parallel_smoke.py             # 6    параллельные вычисления, 1.94× (~3 c)
.venv/bin/python scripts/_admin_web_integration_smoke.py # 5   веб-дэшборд + ИССГР (~30 c)
bash          scripts/run_stage_5_ground_vehicle_demo.sh      # 2.0  наземная машина едет в двойнике (~1 мин)
```

Плюс **готовая доказательная база по радиоканалу** (пункт 2.4) — папка
`reports/` и фигуры `figures/stage24_*_ieee.*`: это самый сильный, проверяемый
результат проекта, показывать как готовые графики и числа (см. пункт 2.4).

### 🟡 Жёлтая зона — лучше ЗАРАНЕЕ ЗАПИСАТЬ (тяжёлый полный стек)

Эти прогоны поднимают много компонентов (SITL + Gazebo + ns-3 + видео + сетевые
namespace + `sudo`/GPU). Когда отрабатывают — выглядят эффектно, но на «живом»
дубле могут подвиснуть. Снимите их заранее чистым прогоном и вставьте клипом.

```bash
sudo bash scripts/run_stage_2_4_rt_online_demo.sh   # флагман: пульт + полёт + Sionna→ns-3 + FPV
sudo bash scripts/run_stage_1_7_lora_serial.sh      # LoRa через Serial / LoRaWAN
sudo bash scripts/run_stage_3_urban_demo.sh         # городская сцена в Gazebo (GUI)
bash      scripts/run_master_demo.sh                # интеграция всего стенда, 14 модулей
```

## Порты и управление (шпаргалка для кадра)

| Порт | Что |
|---|---|
| `http://127.0.0.1:8765/` | Web GCS — пульт оператора БАС (полёт) |
| `http://127.0.0.1:8770/docs` | ИССГР REST/OGC API (Swagger для АСУ-клиента) |
| `http://127.0.0.1:8810/` | Admin dashboard (6 вкладок) |
| `http://127.0.0.1:8811/stats` | Счётчики синхронизации БД (multicast) |

**Управление дроном в Web GCS** (`:8765`): `GUIDED` → `ARM` (или `FORCE`) →
`TAKEOFF` → полёт `W/A/S/D` (вперёд/влево/назад/вправо, кириллица ЦФЫВ тоже),
`Space`/`Ctrl` = вверх/вниз; либо ввести N/E и нажать `GO TO` → `LAND`.

---

# ЗАДАЧА 1 — Аналитический обзор инструментов

**Кто делал:** Степанянц В.Г., Карпов А.К., Маргарян А.Г.

- **Статус:** 🟢 **РЕАЛЬНО** (документ-deliverable).
- **На экране:** открыть [docs/analytical_review_tools.md](analytical_review_tools.md) —
  структурированный обзор 25+ инструментов. По каждому: выбор + версия + лицензия
  + альтернативы + обоснование + ограничения + применение в проекте. Есть сводная
  таблица лицензионных рисков и раздел сравнения **AirSim vs Gazebo**.
- **Проверка (живьём, мгновенно):**

```bash
sed -n '1,60p' docs/analytical_review_tools.md     # или открыть в редакторе/браузере
```

- **Что сказать:** «Это аналитическая основа выбора стека: почему ArduPilot,
  почему Gazebo для физики и AirSim для визуала, какие лицензионные риски учтены».

---

# ЗАДАЧА 2 — Среда моделирования: БАС + наземный транспорт (дорожный и вне дорог)

**Базовые движки по ТЗ:** Unreal Engine, CARLA, AirSim, Gazebo. Среда покрывает
**три класса техники**: воздушный (БАС) и два наземных (на дорогах / вне дорог).

## 2.0 Наземный транспорт: дорожный и вне дорог — 🟢 РЕАЛЬНО, показывать ЖИВЬЁМ

**Кто делал:** Физулин/Андрончев/Карпов (совместная среда)

- **Что сделано:** движок на выбор — ArduPilot **ArduRover** или **CARLA**.
  `rover` = дорожный (ackermann-руль), `rover-skid` = вне дорог (skid-steer).
  Файлы: [scripts/run_stage_5_ground_vehicle_demo.sh](../scripts/run_stage_5_ground_vehicle_demo.sh),
  [scripts/carla_ground_vehicle.py](../scripts/carla_ground_vehicle.py),
  [docs/stage_5_ground_vehicle.md](stage_5_ground_vehicle.md).
- **Проверка (живьём, без sudo):**

```bash
bash scripts/run_stage_5_ground_vehicle_demo.sh                       # дорожный (ArduRover)
BAS_ROVER_FRAME=rover-skid bash scripts/run_stage_5_ground_vehicle_demo.sh   # вне дорог
bash scripts/run_stage_5_ground_vehicle_demo.sh --engine carla --carla-mode kinematic  # движок CARLA
```

- **На экране:** откроется витрина `:8810`, и на карте рядом с БАС поедет маркер
  `ground_vehicle.wheeled` (или `.offroad`) — машина проходит ~120 м по треку в
  едином цифровом двойнике. Это и есть «наземная техника в той же среде, что и
  БАС».
- **Честно:** вождение в демо — **скриптованный** манёвр (не интерактивные
  клавиши). CARLA-движок дополнительно проверен на реальном сервере (Windows RTX,
  карта Town10HD, Tesla Model 3, до 11 м/с) — это можно показать отдельным клипом.

## 2.1 Базовый симулятор ПО БАС — ArduPilot ArduCopter SITL — 🟢 РЕАЛЬНО, ЖИВЬЁМ

**Кто делал:** Андрончев А.Д., Физулин А.В., Карпов А.К.

- **Что сделано:** настоящий бинарь ArduPilot (не эмулятор), замкнут на
  собственную 6DOF-физику через JSON-FDM. Файлы:
  [scripts/install_ardupilot.sh](../scripts/install_ardupilot.sh),
  [scripts/_real_sitl_e2e_smoke.py](../scripts/_real_sitl_e2e_smoke.py).
- **Проверка (живьём, ~18 c):**

```bash
.venv/bin/python scripts/_real_sitl_e2e_smoke.py     # бинарь собирается scripts/install_ardupilot.sh
```

- **На экране (в логе):** `arducopter --model json`, `HEARTBEAT`, валидный
  `GLOBAL_POSITION_INT`, `STABILIZE → ARM`, `ARMED: True`, взлёт `+0.6 м`,
  `max PWM 1858`, `ALL CHECKS PASSED`. Это настоящий автопилот, тот же код, что
  на Pixhawk.
- **Честно:** нет модели ветра/turbulence/ground-effect (LIMITATIONS §1).

## 2.2 Управление с НПУ через MAVLink + два канала связи — ЛИЧНАЯ ЗОНА ФИЗУЛИНА

**Кто делал:** Физулин А.В.

### 2.2.a — MAVLink-управление + видеоканал + WiFi (TCP/IP) — 🟢 РЕАЛЬНО

- **Что сделано:** два независимых канала в ns-3
  [ns3/scenarios/two_channel.cc](../ns3/scenarios/two_channel.cc): control = MAVLink,
  payload = RTP/H.264 видео; пульт [scripts/gcs_web_ui_server.py](../scripts/gcs_web_ui_server.py);
  профиль WiFi [configs/network_profiles/](../configs/network_profiles/).
- **Проверка (🟡 тяжёлый стек — заранее записать):**

```bash
sudo bash scripts/run_stage_2_4_rt_online_demo.sh    # → открыть http://127.0.0.1:8765/
```

- **На экране:** `GUIDED → ARM → TAKEOFF`, полёт `W/A/S/D`, `GO TO`; справа FPV с
  борта и RF-панель (RSSI / loss / delay). Видно: команды и видео идут по двум
  раздельным каналам.
- **Честно:** цепочка `Браузер → MAVProxy → ns-3 control → mavbridge → SITL`,
  команды через MAVProxy (не прямой pymavlink).

### 2.2.b — WiFi vs LoRa: метрики бок о бок — 🟢 РЕАЛЬНО

```bash
sudo bash scripts/run_stage_1_6_compare.sh          # → logs/<run>/comparison.md, comparison.csv
```

- **На экране:** таблица WiFi-good vs degraded: loss, goodput, jitter, разрывы видео.

### 2.2.c — LoRa через Serial Port / LoRaWAN — 🟢 РЕАЛЬНО (модель радио, не железо)

- **Что сделано:** [ns3/scenarios/lora_serial.cc](../ns3/scenarios/lora_serial.cc) —
  калибровка под Semtech SX1276 (SF7/BW125, 5470 бит/с, airtime ~50 мс, PER=0.01);
  LoRaWAN-вариант [ns3/scenarios/lora_serial_lorawan.cc](../ns3/scenarios/lora_serial_lorawan.cc).
- **Проверка (🟡 тяжёлый стек — заранее записать):**

```bash
sudo bash scripts/run_stage_1_7_lora_serial.sh       # → logs/<run>/events.jsonl
```

- **На экране:** миссия AUTO выполнена (landed=True, 7/7 точек, 252 м) **по LoRa
  без WiFi-fallback**, телеметрия PDR=0.991 (потеря байт 1.63 % — заложена
  калибровкой). То есть управление проходит даже по узкому LoRa-каналу.
- **Честно:** виртуальный PTY + ns-3-модель SX1276, IP-стека в радиопетле нет.
  Это **модель**, не физический LoRa-модуль (LIMITATIONS §10).

## 2.3 Интерфейсы ArduPilot↔Gazebo / ↔AirSim / MAVLink↔(Gazebo,AirSim) — 🟢 РЕАЛЬНО

**Кто делал:** Федотенков А.А.

- **Что сделано:** ArduPilot↔Gazebo — штатный `ardupilot_gazebo` (JSON FDM);
  ArduPilot↔AirSim — [scripts/arducopter_airsim_interface.py](../scripts/arducopter_airsim_interface.py)
  (JsonFdmBridge + MavlinkMirrorBridge), физика
  [scripts/multirotor_dynamics.py](../scripts/multirotor_dynamics.py);
  MAVLink-fanout — [scripts/mavlink_sim_router.py](../scripts/mavlink_sim_router.py);
  MAVROS/ROS2-путь — [scripts/run_stage_1_8_mavros.sh](../scripts/run_stage_1_8_mavros.sh).
- **Проверка (живьём, ~5 c):**

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh smoke   # мосты + роутер + JSON-FDM физика
```

- **На экране:** 340 PWM-кадров, набор высоты >2 м, поворот по рысканью, роутер
  раздаёт 1→N синков по 525 Б, `ALL CHECKS PASSED`.
- **Опц. (🟡):** `sudo bash scripts/run_stage_1_8_mavros.sh baseline_wifi` —
  альтернативный путь управления через ROS2/MAVROS (миссия 7/7, 253 м).
- **Честно:** AirSim-визуал в этом смоке через stub (реальный GPU — пункт 3.2).

## 2.4 Gazebo-физика → AirSim-визуал + ns-3/Sionna RT радиофизика — ЛИЧНАЯ ЗОНА

**Кто делал:** Физулин А.В. · метрики по ТЗ: **error rate, распределение ошибок,
пропускная способность.**

> **Это самый сильный и проверяемый результат проекта.** В кадре показывать
> готовые графики и числа из аудита — они доказуемы и не зависят от «живого»
> прогона.

- **Что сделано:** живой Sionna RT даёт RSSI по реальной геометрии маршрута, этот
  RSSI напрямую попадает в попакетное решение ns-3 о потере, видеопоток идёт через
  ns-3, метрики считаются после ns-3. Файлы:
  [scripts/sionna_channel_publisher.py](../scripts/sionna_channel_publisher.py),
  ns-3 [ns3/scenarios/two_channel.cc](../ns3/scenarios/two_channel.cc),
  аудит [reports/stage24_c4_final_summary.md](../reports/stage24_c4_final_summary.md).
- **Что показать на экране (готовые артефакты — самый надёжный вариант):**
  - `figures/stage24_route_rssi_loss_bins_ieee.png` — потери vs RSSI по двум
    каналам (control + payload) с доверительными интервалами;
  - `figures/stage24_route_rssi_packet_trace_ieee.png` — RSSI во времени и
    оконные потери: когда дрон уходит в тень — потери растут;
  - числа из [reports/stage24_c4_article_package.md](../reports/stage24_c4_article_package.md).
- **Проверка чисел (живьём, читает сырые данные):**

```bash
.venv/bin/python scripts/audit_stage24_c4_acceptance.py   # печатает 24/24 PASS
```

- **Ключевые числа (проговорить):** RSSI из Sionna == RSSI, по которому ns-3
  принимает решение, расхождение **0.0 дБ**; через ns-3 прошло 21433 видеопакета,
  5622 отброшено; потери растут с 0.034 при −72 дБм до 0.536 при −80 дБм. Три
  метрики ТЗ: error rate = `loss_ratio`, распределение = разрывы видео
  `video_gap_*`, пропускная = `goodput_bps`.
- **Опц. живой Sionna RT на GPU (🟡):**

```bash
bash scripts/run_sionna_live.sh real_tile --tile-i 0 --freq-mhz 2400   # ~20 c/тайл, нужен GPU
```

- **Честно:** аудит — это **симуляция попакетных потерь на маршруте**, не железо и
  не полевой замер PER; коридор RSSI −78.8…−71.5 дБм (не весь диапазон). Живой
  Sionna на WSL медленный (~20 c/тайл) — для видео надёжнее готовые графики
  (LIMITATIONS §2).

## 2.5 Опционально — модели кибератак — 🔵 RESEARCH/SYNTH, ЖИВЬЁМ (смок)

**Кто делал:** Маргарян А.Г.

- **Что сделано:** [scripts/cyber_attack_simulator.py](../scripts/cyber_attack_simulator.py)
  (GPS-спуфинг / инъекция MAVLink-команд / РЧ-глушение) +
  [scripts/cyber_defense_monitor.py](../scripts/cyber_defense_monitor.py).
- **Проверка (живьём, ~7 c):**

```bash
.venv/bin/python scripts/_cyber_smoke.py
```

- **На экране:** 3 атаки → 20 алертов (1 spoof + 3 cmd + 16 jam), `ALL PASSED`.
  Монитор ловит все три сигнатуры.
- **Честно:** синтетический MAVLink-endpoint, safety-guards (loopback/RFC1918),
  митигации описаны, но не внедрены в боевой стек (LIMITATIONS §6).

---

# ЗАДАЧА 3 — Карта тестового сценария на реальных/реалистичных данных

## 3.1 Карта в Gazebo — 🟢 РЕАЛЬНО (🟡 тяжёлый GUI — заранее записать)

**Кто делал:** Карпов А.К., Маргарян А.Г.

- **Что сделано:** [gazebo/worlds/](../gazebo/worlds/) — `iris_runway_urban.sdf`
  (6 многоэтажек, 3 дороги, деревья, фонари, машины).
- **Проверка:**

```bash
sudo bash scripts/run_stage_3_urban_demo.sh
```

- **На экране:** окно Gazebo с городской застройкой; ИССГР (`--seed-profile urban`)
  поднимает 6 зданий как препятствия, они видны на карте Web GCS.

## 3.2 Карта в Microsoft AirSim (+ реальный GPU-рендер) — 🟢 Windows / 🔵 stub

**Кто делал:** Андрончев А.Д., Федотенков А.А.

- **Что сделано:** [scripts/airsim_scene_builder.py](../scripts/airsim_scene_builder.py)
  (urban-каталог + сцена из OSM `--from-osm` + segmentation),
  [docs/stage_2_2_airsim_overlay.md](stage_2_2_airsim_overlay.md).
- **Проверка:**

```bash
sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh  # нужен Blocks.exe на Windows
sudo bash scripts/run_stage_2_2_airsim_overlay.sh                              # stub (CI, без GPU)
```

- **На экране (windows-режим):** ping=True, ~209 объектов, 7 камер, реальный PNG
  256×144 в `logs/<run>/airsim_camera/`.
- **Честно:** на Linux/WSL2 только `-nullrhi` (пустой кадр); реальный рендер этого
  билда — только на Windows-хосте; stub — синтетика для CI (LIMITATIONS §3).

## 3.3 ns-3/Sionna RT — детализированный учёт 3D-препятствий — ЛИЧНАЯ ЗОНА, ЖИВЬЁМ

**Кто делал:** Физулин А.В.

- **Что сделано:** [scripts/export_scene_to_sionna.py](../scripts/export_scene_to_sionna.py)
  — программный сборщик сцены Mitsuba: runway + 4 препятствия (ангар-металл,
  две башни-бетон, здание-кирпич) с правильными ITU-материалами; Sionna RT видит
  препятствия в трассировке лучей.
- **Проверка (живьём, ~2 c):**

```bash
.venv/bin/python scripts/export_scene_to_sionna.py     # → scene/iris_runway.xml
```

- **На экране:** скрипт печатает 4 препятствия с координатами, размерами и
  материалами и пишет сцену `scene/iris_runway.xml`. Радиотень от этих
  препятствий считается в Sionna RT (см. пункт 2.4: за зданиями RSSI падает,
  потери растут) — это видно на графиках аудита.
- **Честно:** эта команда строит **сцену**; визуализация радиотени — отдельный
  шаг Sionna RT (пункт 2.4), не вывод данной команды.

## 3.4 Адаптация под карты >20×20 км — 🟢 РЕАЛЬНО (алгоритмы), ЖИВЬЁМ

**Кто делал:** Андрончев А.Д., Карпов А.К.

- **Что сделано:** [orchestrator/src/orchestrator/issgr/large_map.py](../orchestrator/src/orchestrator/issgr/large_map.py)
  — `TileGrid`, `SpatialIndex`, геодезия WGS84/UTM через pyproj.
- **Проверка (живьём, ~3 c):**

```bash
.venv/bin/python scripts/_large_map_smoke.py
```

- **На экране:** 20×20 км = 100 тайлов по 2 км, 5000 препятствий за 13 мс,
  bbox-запрос 4×4 км за 0.06 мс, round-trip геодезии 0.00 см, `ALL CHECKS PASSED`.
- **Честно:** это координатная алгебра + индекс; **потоковую загрузку OSM-тайлов**
  он не делает — для реального города отдельно `import_osm_scenario.py`
  (LIMITATIONS §4).
- **Бонус (🟡, нужна сеть):** `./scripts/import_osm_scenario.py --place "Тверская,
  Москва" --radius-m 300 --with-terrain` — реальные здания из OpenStreetMap в
  ИССГР (приближение box: footprint+высота, не mesh).

---

# ЗАДАЧА 4 — Моделирование и ручное управление ≥1 БАС в сценарии — 🟢 РЕАЛЬНО

**Кто делал:** Андрончев А.Д., Физулин А.В., Карпов А.К.

- **Что сделано:** тот же стек, что 2.2.a — Web GCS + SITL + сцена Gazebo.
- **Проверка:**

```bash
sudo bash scripts/run_stage_2_4_rt_online_demo.sh    # интерактив → http://127.0.0.1:8765/
sudo bash scripts/run_stage_2_4_auto_demo.sh         # авто-фильм: TAKEOFF→GOTO→LAND + видео/скриншоты/отчёт
```

- **На экране:** дрон арм-ится, взлетает, идёт по точкам, садится. Авто-демо
  само пишет `logs/<run>/demo_report.md` + `video/web_gcs.webm` +
  `video/fpv.mjpeg.mp4` + 14 скриншотов — **готовый клип для ролика**.
- **Совет:** для «томушнего» видео используйте авто-фильм заранее, а живьём
  достаточно показать пульт и одну команду `GO TO`.

---

# ЗАДАЧА 5 — Опционально: веб-интерфейс — 🟡 ФУНКЦ. ЭКВИВАЛЕНТ, ЖИВЬЁМ (смок)

**Кто делал:** Федотенков А.А.

- **Что сделано:** [web/admin/](../web/admin/) — SPA с 6 вкладками,
  [scripts/admin_web_server.py](../scripts/admin_web_server.py) (9 API).
- **Проверка (живьём, ~30 c):**

```bash
.venv/bin/python scripts/_admin_web_integration_smoke.py    # ALL CHECKS PASSED
```

- **На экране:** вкладки Обзор / ИССГР Collections / Multi-UAV / Бортовая БД /
  Tile Map (400 тайлов = 400 км²) / Multicast Sync; Leaflet-карта объектов.
- **Честно:** dashboard **read-only** — нет write-команд и auth, опрос ~5 с
  (LIMITATIONS §8). ТЗ помечает пункт как опциональный.

---

# ЗАДАЧА 6 — Параллельные вычисления — 🟢 РЕАЛЬНО (CPU-pool), ЖИВЬЁМ

**Кто делал:** Андрончев А.Д., Карпов А.К.

- **Что сделано:** [orchestrator/src/orchestrator/parallel.py](../orchestrator/src/orchestrator/parallel.py)
  — `TaskScheduler` (очередь + retry), `launch_sitl_fleet` (N SITL параллельно),
  `precompute_sionna_tiles`.
- **Проверка (живьём, ~3 c):**

```bash
.venv/bin/python scripts/_parallel_smoke.py
```

- **На экране:** 20 задач → 17 ok / 3 retry-fail; 16 тайлов Sionna — **1.94×
  ускорение** на 4 воркерах vs последовательно, идентичный результат; 4 mock-SITL
  с уникальными sysid; `ALL CHECKS PASSED`.
- **Честно:** только CPU (GPU-пула нет), cross-host (Ray/Dask) вне scope
  (LIMITATIONS §7).

---

# ФИНАЛ: интеграция всего стенда (🟡 заранее записать)

```bash
bash scripts/run_master_demo.sh          # → http://127.0.0.1:8810/
```

- **На экране:** 14 модулей стартуют параллельно и работают 60+ с: ИССГР×2 +
  синхронизация БД + бортовая БД + AirSim + физика + кибер-монитор + кэш Sionna +
  дэшборд. На 60-й с: `uavs=1 obs=8`, `sync HB=52 L1=52`, `onboard_rows=800`,
  `cyber_alerts=85`.

## Сводная таблица (что показать и как)

| Пункт ТЗ | Кто | Статус | Как показать | Команда |
|---|---|---|---|---|
| 1. Аналитический обзор | Степанянц/Карпов/Маргарян | 🟢 | открыть документ | `docs/analytical_review_tools.md` |
| 2.0 Наземный транспорт | Физулин/Андрончев/Карпов | 🟢 | живьём | `run_stage_5_ground_vehicle_demo.sh` |
| 2.1 ArduCopter SITL | Андрончев/Физулин/Карпов | 🟢 | живьём 18 c | `_real_sitl_e2e_smoke.py` |
| 2.2.a MAVLink+видео+WiFi | **Физулин** | 🟢 | запись | `run_stage_2_4_rt_online_demo.sh` |
| 2.2.b WiFi vs LoRa | **Физулин** | 🟢 | запись | `run_stage_1_6_compare.sh` |
| 2.2.c LoRa Serial/LoRaWAN | **Физулин** | 🟢 | запись | `run_stage_1_7_lora_serial.sh` |
| 2.3 Мосты ArduPilot↔sim | Федотенков | 🟢 | живьём 5 c | `run_stage_4_sim_bridges_demo.sh smoke` |
| 2.4 Sionna→ns-3 + метрики | **Физулин** | 🟢 | **готовые графики + аудит** | `audit_stage24_c4_acceptance.py` + `figures/stage24_*_ieee` |
| 2.5 Кибератаки (опц.) | Маргарян | 🔵 | живьём 7 c | `_cyber_smoke.py` |
| 3.1 Карта Gazebo | Карпов/Маргарян | 🟢 | запись | `run_stage_3_urban_demo.sh` |
| 3.2 Карта AirSim | Андрончев/Федотенков | 🟢/🔵 | запись (Windows) | `BAS_AIRSIM_MODE=windows run_stage_2_2_airsim_overlay.sh` |
| 3.3 Sionna 3D-препятствия | **Физулин** | 🟢 | живьём 2 c | `export_scene_to_sionna.py` |
| 3.4 Карты >20×20 км | Андрончев/Карпов | 🟢/🟡 | живьём 3 c | `_large_map_smoke.py` |
| 4. Моделирование + ручное управление | Андрончев/Физулин/Карпов | 🟢 | запись (авто-фильм) | `run_stage_2_4_auto_demo.sh` |
| 5. Веб-интерфейс (опц.) | Федотенков | 🟡 | живьём 30 c | `_admin_web_integration_smoke.py` |
| 6. Параллельные вычисления | Андрончев/Карпов | 🟢/🔵 | живьём 3 c | `_parallel_smoke.py` |
