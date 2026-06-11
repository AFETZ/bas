# Сценарий записи демо-видео по гранту БАС

Рабочий сценарий для записи отчётных роликов перед внешними коллегами.
Построен по **списку задач группы ПВАТС УЛ САПР**. По каждой задаче:
**что запускать → где это в коде (GitHub `AFETZ/bas`) → что вводить в
терминал → что показать на экране → что проговорить → честная пометка
реально/эквивалент/синтетика**.

> Репозиторий: `git@github.com:AFETZ/bas.git` · ветка `main`
> Источник истины по «реально/заглушка»: [docs/LIMITATIONS.md](LIMITATIONS.md)
> Матрица ТЗ: [docs/tz_compliance.md](tz_compliance.md)

## Легенда честности (проговаривать в кадре)

| Маркер | Что говорить |
|---|---|
| 🟢 **РЕАЛЬНО** | Настоящая связка, живой код, проверяемый артефакт прогона |
| 🟡 **ФУНКЦ. ЭКВИВАЛЕНТ** | Работает по-настоящему, но упрощённая реализация вместо буквальной из ТЗ (проговорить, что и почему) |
| 🔵 **RESEARCH/SYNTH** | Исследовательский стенд на синтетических данных, не production / не hardware (проговорить честно) |

## Проверено вживую перед записью (2026-06-09)

Эти offline-смоки прошли на этой машине **сегодня**, без sudo и GPU — можно
смело вставлять как «пруф, что это живой код, а не видеомонтаж»:

| Смок | Результат | Подтверждает |
|---|---|---|
| `scripts/_cyber_smoke.py` | ALL CHECKS PASSED, 20 алертов | кибер-защита |
| `scripts/_multirotor_dynamics_smoke.py` | ALL CHECKS PASSED, 13 проверок | 6DOF-физика квадрокоптера |
| `scripts/_mavlink_sim_router_smoke.py` | ALL CHECKS PASSED, 25→3 синка | MAVLink fanout |
| `scripts/_arducopter_airsim_smoke.py` | ALL CHECKS PASSED, 340 PWM | ArduPilot↔AirSim интерфейс |

---

## Общая подготовка стенда (один раз перед записью)

```bash
cd ~/bas-prototype            # или ~/bas после install.sh
git status                    # показать в кадре: это рабочий репозиторий
ls scripts/run_stage_*.sh     # показать каталог сценариев
```

- Большинство «полётных» демо требуют `sudo` (создают network namespaces под ns-3).
- Smoke-скрипты (`scripts/_*.py`) — **без sudo**, быстрые, детерминированные.
- Sionna RT live требует GPU/OptiX; на WSL ~20 с/тайл — для видео лучше cached.
- AirSim Windows GPU требует запущенного `Blocks.exe` на Windows-хосте.
- Не уверены, что запускать — `bash scripts/demo.sh` (интерактивное меню).

---

# ДУБЛЬ 1 — Аналитический обзор инструментов

**Пункт ТЗ:** «Аналитический обзор инструментов для моделирования БАС и
наземного транспорта…» · **Исполнители:** Степанянц, Карпов, Маргарян

- **Где в коде:** [docs/analytical_review_tools.md](analytical_review_tools.md)
- **Это не запуск, а документ.** В кадре открыть файл, прокрутить.
- **Что показать:** структурированный обзор 25+ инструментов (ArduPilot,
  Gazebo, AirSim/Cosys-AirSim, CARLA, ns-3, Sionna RT, MAVLink, ИССГР, CV),
  по каждому — версия, лицензия, альтернативы, обоснование, ограничения;
  сводная таблица лицензионных рисков.
- **Проговорить:** 🟢 **РЕАЛЬНО** как deliverable-документ. Сравнение
  AirSim vs Gazebo (ссылка из ТЗ на discuss.px4) отражено в разделе выбора
  движка физики/рендера.

```bash
sed -n '1,40p' docs/analytical_review_tools.md   # или открыть в редакторе
```

---

# ДУБЛЬ 2 — Среда моделирования: базовый автопилот ArduCopter SITL

**Пункт ТЗ:** «Разработка среды моделирования… базовый симулятор ПО БАС —
ArduPilot ArduCopter SITL» · **Исполнители:** Андрончев, Физулин, Карпов

- **Где в коде:** [scripts/_real_sitl_e2e_smoke.py](../scripts/_real_sitl_e2e_smoke.py),
  [docker/ardupilot-sitl/Dockerfile](../docker/ardupilot-sitl/), сборка —
  [scripts/install_ardupilot.sh](../scripts/install_ardupilot.sh)
- **Команда (если бинарь ArduPilot уже собран):**

```bash
bash scripts/install_ardupilot.sh           # один раз, если arducopter ещё не собран
.venv/bin/python scripts/_real_sitl_e2e_smoke.py
```

- **Что показать:** в логе — реальный `arducopter --model json`, поток
  `HEARTBEAT`, валидный `GLOBAL_POSITION_INT`, переход `STABILIZE → ARM`,
  RC-throttle, набор высоты `takeoff delta > 0.5 м`, `max PWM 1858`.
- **Проговорить:** 🟢 **РЕАЛЬНО** — это настоящий бинарь ArduPilot (5.1 МБ),
  не эмулятор. Замыкаем его на нашу собственную 6DOF-физику через JSON-FDM
  мост (см. Дубль 7). Ограничение честно: нет модели ветра/turbulence
  (см. LIMITATIONS §1).

---

# ДУБЛЬ 3 — Ручное управление с НПУ через MAVLink + Web GCS (ЛИЧНАЯ ЗОНА)

**Пункт ТЗ:** «Для ручного управления с НПУ используется MavLink… реализовать
моделирование и ручное управление как минимум одного БАС» · **Исполнители:**
Физулин (+ Андрончев, Карпов)

- **Где в коде:** [scripts/run_stage_2_4_fpv_rf_demo.sh](../scripts/run_stage_2_4_fpv_rf_demo.sh),
  [scripts/gcs_web_ui_server.py](../scripts/gcs_web_ui_server.py),
  [scripts/mavproxy_stage_2_4_driver.py](../scripts/mavproxy_stage_2_4_driver.py),
  UI — [web/gcs/](../web/gcs/)
- **Команда:**

```bash
sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh
# открыть в браузере http://127.0.0.1:8765/
```

- **Что показать:** Web GCS в браузере — кнопки `GUIDED / ARM / TAKEOFF / LAND`,
  удержание `W/A/S/D` для velocity-команд, `GO TO` по карте; FPV-окно с борта
  (живой MJPEG из Gazebo), RF-панель с RSSI/loss/delay. Командуем дроном
  руками, он летит.
- **Проговорить:** 🟢 **РЕАЛЬНО** — цепочка `Browser UI → MAVProxy → ns-3
  control → mavbridge → SITL`. Команды идут через MAVProxy stdin (настоящий
  MAVLink-роутер), прямой pymavlink не используется. `GO TO` шлёт
  `SET_POSITION_TARGET_LOCAL_NED`. Это закрывает «ручное управление одним БАС».

---

# ДУБЛЬ 4 — Два канала связи: WiFi (TCP/IP) vs LoRa, сравнение (ЛИЧНАЯ ЗОНА)

**Пункт ТЗ:** «2 канала связи по стандартным протоколам (управление,
видеопоток)… WiFi (TCP/IP)» · **Исполнители:** Физулин

- **Где в коде:** [ns3/scenarios/two_channel.cc](../ns3/scenarios/two_channel.cc),
  [scripts/run_stage_1_6_compare.sh](../scripts/run_stage_1_6_compare.sh),
  профили [configs/network_profiles/](../configs/network_profiles/)
- **Команда:**

```bash
sudo bash scripts/run_stage_1_6_compare.sh
# смотреть logs/<run>/comparison.md и comparison.csv
```

- **Что показать:** два канала ns-3 — **control** (MAVLink) + **payload**
  (RTP/H.264 видео); side-by-side отчёт WiFi-good vs degraded. Метрики:
  loss_ratio, goodput, jitter, video gap.
- **Проговорить:** 🟢 **РЕАЛЬНО** — ns-3 как настоящий сетевой симулятор,
  два независимых TAP-канала. WiFi = MAVLink через UDP/IP + RTP/UDP видео.

---

# ДУБЛЬ 5 — LoRa через Serial Port / LoRaWAN, калибровка под SX1276 (ЛИЧНАЯ ЗОНА)

**Пункт ТЗ:** «Интерфейс каналов связи… использует стандарты LoRa (через
Serial Port)/LoRaWan» · **Исполнители:** Физулин

- **Где в коде:** [ns3/scenarios/lora_serial.cc](../ns3/scenarios/lora_serial.cc),
  [ns3/scenarios/lora_serial_lorawan.cc](../ns3/scenarios/lora_serial_lorawan.cc),
  [scripts/run_stage_1_7_lora_serial.sh](../scripts/run_stage_1_7_lora_serial.sh),
  [scripts/setup_lora_bridge.sh](../scripts/setup_lora_bridge.sh)
- **Команда:**

```bash
sudo bash scripts/run_stage_1_7_lora_serial.sh
# смотреть logs/<run>/events.jsonl: flow_id=lora_gcs_tx, lora_uav_tx
```

- **Что показать:** виртуальный PTY (`/tmp/ptyGCS_lora`) → dual-socat bridge →
  ns-3 NetDevice калиброванный под Semtech SX1276 (SF7/BW125: 5470 bps,
  airtime ~50 мс, PER=0.01). Прогон: **mission AUTO landed=True, 7/7
  waypoints, 252 м через LoRa без WiFi-fallback**, telemetry PDR=0.991
  (1.63% byte_loss как заложено калибровкой).
- **Проговорить:** 🟢 **РЕАЛЬНО** — никакого IP-стека в радио-петле, чистый
  serial byte-stream. PHY откалиброван по Augustin et al. 2016. Честно:
  это **модель** радио SX1276 в ns-3, не физическое железо LoRa (для
  hardware нужен реальный модуль — см. LIMITATIONS §10).

---

# ДУБЛЬ 6 — MAVROS / ROS2 как альтернативный путь управления (ЛИЧНАЯ ЗОНА)

**Пункт ТЗ:** «…с использованием MAVROS для работы на основе ROS» ·
**Исполнители:** Физулин (интерфейс), Федотенков (зона)

- **Где в коде:** [docker/mavros/](../docker/mavros/),
  [scripts/run_stage_1_8_mavros.sh](../scripts/run_stage_1_8_mavros.sh),
  orchestrator MAVROS bridge — [orchestrator/src/orchestrator/](../orchestrator/src/orchestrator/)
- **Команда:**

```bash
sudo bash scripts/run_stage_1_8_mavros.sh baseline_wifi
```

- **Что показать:** docker `bas/mavros:dev` (ROS2 Humble + MAVROS 2.14),
  rclpy bridge node, mission через `/mavros/mission/push`, force-arm через
  `CommandLong`+21196. Прогон: status=success, 7/7 waypoints, 253 м, 30 м alt.
- **Проговорить:** 🟢 **РЕАЛЬНО** — настоящий ROS2/MAVROS, не имитация.
  Переключается рантайм-флагом `--mavlink-backend mavros`. Pymavlink
  остаётся по умолчанию.

---

# ДУБЛЬ 7 — Интерфейсы ArduPilot↔Gazebo и ArduPilot↔AirSim (физика → визуал)

**Пункт ТЗ:** «ArduPilot должен иметь интерфейс взаимодействия с Gazebo и
AirSim; MavLink ↔ Gazebo/AirSim; Gazebo физика → AirSim визуал» ·
**Исполнители:** Федотенков (зона), интерфейсы — Физулин

- **Где в коде:** [scripts/arducopter_airsim_interface.py](../scripts/arducopter_airsim_interface.py)
  (`JsonFdmBridge` + `MavlinkMirrorBridge`),
  [scripts/multirotor_dynamics.py](../scripts/multirotor_dynamics.py) (X-config 6DOF),
  [scripts/mavlink_sim_router.py](../scripts/mavlink_sim_router.py) (1→N fanout),
  [scripts/run_stage_4_sim_bridges_demo.sh](../scripts/run_stage_4_sim_bridges_demo.sh)
- **Команда (быстрый offline-пруф):**

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh smoke
# или прямые смоки (прошли сегодня):
.venv/bin/python scripts/_arducopter_airsim_smoke.py
.venv/bin/python scripts/_mavlink_sim_router_smoke.py
.venv/bin/python scripts/_multirotor_dynamics_smoke.py
```

- **Что показать:** MAVLink fanout (один источник → GCS/Gazebo/AirSim/file),
  JSON-FDM: бинарный `servo_packet_16` PWM → 6DOF → IMU/GPS/quaternion обратно;
  340 PWM-кадров, climb >2 м, yaw +13°.
- **Проговорить:** 🟢 **РЕАЛЬНО** — ArduPilot↔Gazebo через штатный
  `ardupilot_gazebo` plugin (JSON FDM). ArduPilot↔AirSim — канонический
  `SIM_JSON` паттерн на UDP 9002/9003 + MAVLink-mirror через msgpack-rpc.
  Честно: нет модели ветра/ground-effect (LIMITATIONS §1).

---

# ДУБЛЬ 8 — AirSim: фотореалистичный рендер и сенсоры на реальном GPU

**Пункт ТЗ:** «AirSim для высокореалистичного моделирования окружающей
обстановки и сенсоров БАС» · **Исполнители:** Андрончев, Федотенков

- **Где в коде:** [scripts/run_stage_2_2_airsim_overlay.sh](../scripts/run_stage_2_2_airsim_overlay.sh),
  [scripts/airsim_client.py](../scripts/airsim_client.py),
  [scripts/airsim_bridge.py](../scripts/airsim_bridge.py),
  [docs/stage_2_2_airsim_overlay.md](stage_2_2_airsim_overlay.md)
- **Команда:**

```bash
# Real GPU (требует запущенный Blocks.exe на Windows-хосте):
sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh
# CI/без GPU (stub) — для записи если GPU недоступен:
sudo bash scripts/run_stage_2_2_airsim_overlay.sh
```

- **Что показать:** Cosys-AirSim UE5.5 на RTX 5070 Ti, 209 объектов сцены,
  7 камер, `simGetImages` отдаёт реальный PNG 256×144, pose forwarding из WSL.
- **Проговорить:** в режиме `windows` 🟢 **РЕАЛЬНО** GPU-рендер (см.
  `docs/assets/airsim_windows_gpu_frame.png`). Это исполнительная зона
  Андрончева/Федотенкова, но архитектурно интерфейс закрыт.
  Честно: 🔵 на Linux/WSL2 — только `-nullrhi` (пустой кадр), реальный
  рендер этого билда возможен только на Windows-хосте (LIMITATIONS §3).
  Stub-режим — синтетика для CI, проговорить явно если показываете его.

---

# ДУБЛЬ 9 — ns-3 + Sionna RT: радиофизика и метрики (ЛИЧНАЯ ЗОНА)

**Пункт ТЗ:** «Интеграция моделирования затухания и отражения радиосигналов
(error rate, распределение ошибок, пропускная способность) с помощью
ns-3/Sionna RT» · **Исполнители:** Физулин

- **Где в коде:** [scripts/sionna_channel_publisher.py](../scripts/sionna_channel_publisher.py),
  [scripts/sionna_real_tile.py](../scripts/sionna_real_tile.py),
  сцена [scene/iris_runway.xml](../scene/), карта [radio_maps/](../radio_maps/),
  [scripts/run_stage_2_4_rt_online_demo.sh](../scripts/run_stage_2_4_rt_online_demo.sh),
  ns-3 [ns3/scenarios/two_channel.cc](../ns3/scenarios/two_channel.cc)
- **Команда:**

```bash
# A. Live RT-демо с RF-панелью и ручным полётом (заход за здание рвёт связь):
sudo bash scripts/run_stage_2_4_rt_online_demo.sh

# B. Один live-тайл Sionna RT (нужен GPU/OptiX, ~20 с):
bash scripts/run_sionna_live.sh real_tile --tile-i 0 --freq-mhz 2400

# C. Cached режим из готовой карты (быстро, для видео надёжнее):
.venv/bin/python scripts/sionna_real_tile.py --mode cached --tile-i 0
```

- **Что показать:** при заходе дрона за здание RSSI падает, ns-3 повышает
  loss+delay **на обоих каналах** (control+payload одновременно), видео рвётся,
  команды задерживаются — в RF-панели Web GCS в реальном времени. Метрики в
  `report.md`/`comparison.csv`: error rate (loss_ratio/PDR), распределение
  ошибок (video_gap), пропускная способность (goodput_bps).
- **Проговорить:** 🟢 **РЕАЛЬНО** — Sionna RT даёт физически обоснованную
  RSSI-карту через ray-tracing с ITU-материалами; ns-3 поллит JSON каждые
  100 мс и деформирует `RateErrorModel`. Все три метрики ТЗ присутствуют.
  Честно: live-solve на WSL медленный (~20 с/тайл), в production —
  Linux native GPU; для надёжного видео используйте cached-карту
  (это **реальный** Sionna-вывод, просто посчитанный заранее) — LIMITATIONS §2.

---

# ДУБЛЬ 10 — Карта тестового сценария: 3D-препятствия для Sionna + город из OSM

**Пункт ТЗ:** «Создание карты сценария… ns-3/Sionna RT для детализированного
учёта 3D препятствий… адаптация под карты >20×20 км» · **Исполнители:**
Физулин (3D для RF), Карпов, Маргарян, Андрончев

- **Где в коде:** [scripts/export_scene_to_sionna.py](../scripts/export_scene_to_sionna.py),
  [scripts/import_osm_scenario.py](../scripts/import_osm_scenario.py),
  [gazebo/worlds/](../gazebo/worlds/) (`iris_runway_urban.sdf`),
  large-map — `orchestrator/issgr/large_map.py`,
  смок [scripts/_large_map_smoke.py](../scripts/_large_map_smoke.py)
- **Команда:**

```bash
# 3D-сцена для Sionna (Mitsuba XML: runway+hangar+towers+building, ITU-материалы):
.venv/bin/python scripts/export_scene_to_sionna.py

# Реальный город из OpenStreetMap (любая точка Земли):
./scripts/import_osm_scenario.py --place "Тверская, Москва" --radius-m 300 --with-terrain

# Алгоритмы карт >20×20 км (tile grid, spatial index, геодезия):
.venv/bin/python scripts/_large_map_smoke.py
```

- **Что показать:** Sionna видит препятствия в ray-tracing (35% сцены в
  радиотени, `logs/sionna_demo/trajectory_loss.png`); OSM → реальные здания
  в ИССГР; 20×20 км = 100 тайлов, 5000 препятствий за 13 мс.
- **Проговорить:** 🟢 **РЕАЛЬНО** для 3D-геометрии RF и алгоритмов карт.
  Честно: 🟡 OSM-здания — box-приближение (footprint+высота), не mesh;
  large-map даёт coordinate-алгебру и индекс, но **не** скачивает OSM-тайлы
  потоком (LIMITATIONS §4).

---

# ДУБЛЬ 11 — Опционально: модели кибератак (защита)

**Пункт ТЗ:** «Опционально — рассмотреть реализацию моделей кибератак» ·
**Исполнители:** Маргарян

- **Где в коде:** [scripts/cyber_attack_simulator.py](../scripts/cyber_attack_simulator.py),
  [scripts/cyber_defense_monitor.py](../scripts/cyber_defense_monitor.py),
  смок [scripts/_cyber_smoke.py](../scripts/_cyber_smoke.py)
- **Команда:**

```bash
.venv/bin/python scripts/_cyber_smoke.py
# или вручную в двух терминалах:
./scripts/cyber_defense_monitor.py --mavlink-port 14559 --channel-file /tmp/ch.json
./scripts/cyber_attack_simulator.py gps_spoof --target-port 14559 --offset-lat-m 250
```

- **Что показать:** 3 вектора (GPS spoof / MAVLink cmd injection / RF jam) →
  детектор поднимает 20 алертов (1 spoof + 3 cmd + 16 jam). Прошло сегодня.
- **Проговорить:** 🔵 **RESEARCH/SYNTH** — это **defensive research
  simulator** на синтетическом MAVLink-endpoint с safety-guards (только
  loopback/RFC1918), **не** реальный pentest и не проверено против
  production-автопилота. Митигации (MAVLink signing, freq hopping) описаны
  как рекомендации, но не реализованы (LIMITATIONS §6).

---

# ДУБЛЬ 12 — Опционально: веб-интерфейс (admin dashboard)

**Пункт ТЗ:** «Опционально — веб-интерфейс для загрузки ПО на БАС и
конфигурации сценариев» · **Исполнители:** Федотенков

- **Где в коде:** [web/admin/](../web/admin/),
  [scripts/admin_web_server.py](../scripts/admin_web_server.py),
  смок [scripts/_admin_web_smoke.py](../scripts/_admin_web_smoke.py)
- **Команда:**

```bash
.venv/bin/python scripts/_admin_web_integration_smoke.py
# или поднять dashboard в составе master demo (Дубль 14): http://127.0.0.1:8810/
```

- **Что показать:** 6 вкладок (Обзор / ИССГР Collections / Multi-UAV /
  Бортовая БД / Tile Map / Multicast Sync), Leaflet-карта, REST-прокси.
- **Проговорить:** 🟡 **ФУНКЦ. ЭКВИВАЛЕНТ** — dashboard живой и читает
  реальный ИССГР/OnBoardDB, но **read-only**: нет write-команд (нельзя
  заармить дрон или загрузить ПО через него), нет auth, poll-обновления ~5 с
  (LIMITATIONS §8). ТЗ помечает пункт как опциональный.

---

# ДУБЛЬ 13 — Параллельные вычисления для крупномасштабных сценариев

**Пункт ТЗ:** «Изучение необходимости параллельных вычислений… создание
модуля управления параллельным моделированием» · **Исполнители:**
Андрончев, Карпов

- **Где в коде:** [orchestrator/src/orchestrator/parallel.py](../orchestrator/src/orchestrator/parallel.py),
  смок [scripts/_parallel_smoke.py](../scripts/_parallel_smoke.py)
- **Команда:**

```bash
.venv/bin/python scripts/_parallel_smoke.py
```

- **Что показать:** `TaskScheduler` (priority queue + retry), `launch_sitl_fleet`
  (N SITL параллельно с уникальными sysid), `precompute_sionna_tiles`
  (16 тайлов, **1.94× speedup** на 4 воркерах vs sequential).
- **Проговорить:** 🟢 **РЕАЛЬНО** на `ProcessPoolExecutor` (stdlib, без внешних
  зависимостей). Честно: 🔵 CPU-only (нет GPU-пула), cross-host (Ray/Dask)
  вне scope; `sionna_compute_tile` внутри parallel.py — FSPL-stub, реальный
  Sionna в отдельном `sionna_real_tile.py` (LIMITATIONS §7).

---

# ДУБЛЬ 14 — Финал: весь стенд одной командой (интеграция)

**Назначение:** показать живую интеграцию всех модулей разом — сильный
финал ролика.

- **Где в коде:** [scripts/run_master_demo.sh](../scripts/run_master_demo.sh)
  (или [scripts/run_stage_2_4_auto_demo.sh](../scripts/run_stage_2_4_auto_demo.sh)
  для красивого авто-фильма с Playwright+ffmpeg)
- **Команда:**

```bash
bash scripts/run_master_demo.sh          # откроет http://127.0.0.1:8810/
# красивый автоматический flight-фильм (видео+скриншоты+отчёт):
sudo bash scripts/run_stage_2_4_auto_demo.sh
```

- **Что показать:** 14 модулей стартуют параллельно, health-check, работают
  60+ с: ИССГР ×2 + multicast sync + бортовая БД + AirSim stub+scene +
  JsonFdmBridge + cyber monitor + Sionna cached tile + admin. На 60-й с:
  `node-A uavs=1 obs=8`, `sync HB=52 L1=52`, `onboard_rows=800`,
  `cyber_alerts=85`. Auto-demo выдаёт `demo_report.md` + `web_gcs.webm` +
  `fpv.mjpeg.mp4` + 14 скриншотов.
- **Проговорить:** 🟢 **РЕАЛЬНО** как интеграционная демонстрация Stage 3/4.

---

## Сводная таблица: задача ТЗ → дубль → честность

| # | Задача ТЗ | Исполнители | Дубль | Честность |
|---|---|---|---|---|
| 1 | Аналитический обзор инструментов | Степанянц/Карпов/Маргарян | 1 | 🟢 документ |
| 2 | Базовый ArduCopter SITL | Андрончев/Физулин/Карпов | 2 | 🟢 |
| 3 | Ручное управление через MAVLink/НПУ | **Физулин** | 3 | 🟢 |
| 4 | Канал управления + видео, WiFi (TCP/IP) | **Физулин** | 4 | 🟢 |
| 5 | LoRa через Serial / LoRaWAN | **Физулин** | 5 | 🟢 (модель SX1276, не железо) |
| 6 | MAVROS / ROS2 | Федотенков/Физулин | 6 | 🟢 |
| 7 | ArduPilot↔Gazebo/AirSim, MAVLink↔sim | Федотенков/Физулин | 7 | 🟢 |
| 8 | AirSim рендер+сенсоры | Андрончев/Федотенков | 8 | 🟢 Windows GPU / 🔵 stub |
| 9 | ns-3/Sionna RT, 3 метрики | **Физулин** | 9 | 🟢 (live медленный→cached) |
| 10 | Карта: 3D для RF, OSM, >20×20 км | Физулин/Карпов/Андрончев | 10 | 🟢 / 🟡 box-OSM |
| 11 | Опц.: кибератаки | Маргарян | 11 | 🔵 research-synth |
| 12 | Опц.: веб-интерфейс | Федотенков | 12 | 🟡 read-only |
| 13 | Параллельные вычисления | Андрончев/Карпов | 13 | 🟢 CPU-only |
| — | Моделирование ≥1 БАС в сценарии | Андрончев/Физулин/Карпов | 3, 9, 14 | 🟢 |
| — | Интеграция всего стенда | — | 14 | 🟢 |

## Рекомендованный порядок записи

1. Короткое вступление + `git status` + каталог скриптов (Общая подготовка).
2. **Быстрые offline-пруфы** (Дубли 11, 7, 13, 2-смок) — 2-3 минуты,
   показывают «живой код» без долгого подъёма стека.
3. **Главный полётный блок** (Дубли 3 → 9) — ручное управление + падение
   связи за зданием. Самое зрелищное, личная зона Физулина.
4. Каналы связи (Дубли 4, 5, 6).
5. Интерфейсы и визуал (Дубли 7, 8, 10).
6. Опциональное (Дубли 11, 12, 13) с честными пометками.
7. **Финал** — Дубль 14 (весь стенд / авто-фильм).
