# Roadmap проекта (по состоянию на v1.0, май 2026)

Полная картина: что готово, что намечено в исходном архитектурном документе,
и что я предлагаю как логичное продолжение. Каждый блок — с обоснованием и
разбивкой на под-этапы (как делалось внутри 1.5).

## Этап 1 — закрыт ✅ (v0.1 → v1.0, ~6 месяцев)

| Под-этап | Состояние | Tag |
|---|---|---|
| 1.0 Skeleton (orchestrator + journal + analyzer + stub) | готов | — |
| 1.1 Docker + 4 базовых образа (SITL / Gazebo / ns-3 / orch) | готов | — |
| 1.2 ArduPilot SITL ↔ Gazebo через ardupilot_gazebo plugin | готов | — |
| 1.3 ns-3 `two_channel.cc` с TapBridge (control + payload) | готов | — |
| 1.4 MAVLink через host network (без ns-3) | готов | v0.1 |
| 1.5.0 Shadow GCS в bas-ctrl-far netns через ns-3 | готов | v0.2 |
| 1.5.1 Полная mission через ns-3 control, оба профиля | готов | v0.7 |
| 1.5.2.a RTP H.264 видео через ns-3 payload | готов | v0.8 |
| 1.5.2.a-metrics VideoMetrics в анализаторе | готов | v0.8.1 |
| 1.5.2.b Real Gazebo камера через GstCameraPlugin | готов | v0.9 |
| 1.5.2.c Корреляция payload outage ↔ video gaps | готов | v0.9 |
| 1.5.2.d Точная e2e latency через GstPadProbe | готов | v0.9 |
| 1.6 Сравнительный отчёт WiFi vs LoRa (markdown+CSV) | готов | **v1.0** |

**Итог этапа 1**: воспроизводимая среда моделирования БАС с двумя радио-каналами
(control + payload) через ns-3 в реальном времени, реальная Gazebo-камера,
полноценная mission AUTO в ArduPilot, all wrapped в один скрипт сравнения
WiFi vs LoRa с side-by-side отчётом для диплома/гранта.

---

## Этап 2 — намечено в исходном ТЗ Физулина А.В.

Из карты файлов в архитектурном документе (`docs/architecture.md`):

> **ns-3 / Sionna RT** (error rate, распределение ошибок, пропускная способность):
> Sionna RT не входит в этап 1; в этапе 2 — офлайн-расчёт радиокарт, результат
> подаётся как параметр профиля в ns-3.

> **Карта тестового сценария в ns-3/Sionna RT** для 3D-препятствий:
> Sionna — этап 2.

> Этап 2 включает также: AirSim/Cosys-AirSim как визуально-сенсорную ветку,
> несколько БАС / рой, ручное управление одним БАС через GCS.

### 2.1 — Sionna RT (приоритет 1 по ТЗ)

Цель: заменить искусственный `RateErrorModel` в `ns-3` (текущая модель: «N% потерь
случайно, M мс delay, outage windows вручную») на физически обоснованную модель
из ray-tracing'а. Это и есть «карта тестового сценария в ns-3/Sionna RT»
из ТЗ Физулина.

| Под-этап | Содержание |
|---|---|
| 2.1.a — установка | Sionna 0.18 в WSL (Python + TensorFlow + Mitsuba renderer); smoke на готовом примере с двумя antennas |
| 2.1.b — geometry export | Экспорт `iris_runway` SDF → glTF/PLY для Sionna scene. Iris + runway + опционально здания/деревья как препятствия |
| 2.1.c — radio map | Sionna RT для пары точек (tx у GCS, rx у БАС): получить path-loss / delay-spread / Doppler как функцию позиции UAV |
| 2.1.d — ns-3 integration | Sionna выдаёт CSV/JSONL «при позиции (x,y,z) → loss prob = P, delay = D». Новый `SionnaErrorModel` в ns-3 читает таблицу по координатам UAV |
| 2.1.e — real-time lookup | Gazebo шлёт текущую позицию UAV → orchestrator → ns-3 пересчитывает loss/delay из Sionna map за каждый sim-tick |
| 2.1.f — verification | сравнить wifi_good (manual) и wifi_good_sionna (физически обоснованный) на тех же траекториях; должна появиться зависимость канала от позиции |

Под-этап **2.1.d** и **2.1.e** требуют расширения `two_channel.cc` — нужен новый
`ErrorModel` с table-lookup'ом. Это middle-complexity работа, ~1-2 недели.

**Польза для диплома**: «не просто RateErrorModel с потолка, а physically-justified
radio propagation» — сильный аргумент для защиты.

### 2.2 — AirSim / Cosys-AirSim (приоритет 2)

Цель: визуально-сенсорная ветка (LiDAR, depth-camera, фотореалистичные текстуры).
В ТЗ упоминается как параллельная Gazebo'у визуальная среда.

**Cosys-AirSim** — это активно поддерживаемый Linux-friendly fork оригинального
Microsoft AirSim (который заброшен с 2022). Использует Unreal Engine 5 или
headless rendering.

| Под-этап | Содержание |
|---|---|
| 2.2.a — установка | Cosys-AirSim в WSL2 + Unreal headless или Colosseum/Stage env |
| 2.2.b — sensors | подключить depth-camera + LiDAR как новые payload-flows; протолкнуть через ns-3 payload TAP |
| 2.2.c — sync с ArduPilot | AirSim как FDM-bridge для SITL (вместо Gazebo) — параллельная ветка которую можно сравнивать |
| 2.2.d — comparison | report Gazebo vs AirSim на тех же сценариях: разница в визуальном realism vs скорости моделирования |

**Сложность**: AirSim тяжелее Gazebo (UE5 ≈ 8-16 GB RAM). На WSL2 без GPU
будет slow. Возможно вариант — отдельная Linux VM с GPU passthrough.

**Польза для диплома**: показать что архитектура prototype'а не привязана к одному
симулятору — взаимозаменяемые FDM-бэкенды.

### 2.3 — Multi-UAV (рой)

Цель: расширить с n=1 до n=2-4 БАС в одной симуляции. ТЗ упоминает «несколько БАС».

| Под-этап | Содержание |
|---|---|
| 2.3.a — multi-instance SITL | 2-4 SITL'а в разных bas-uav-N netns'ах, каждый со своим ArduPilot instance |
| 2.3.b — ns-3 multi-node | расширение `two_channel.cc` до N MAVLink-каналов; все БАС shared один control-bridge |
| 2.3.c — MANET routing | если БАС-к-БАС связь нужна — добавить AODV/OLSR в ns-3 |
| 2.3.d — orchestrator multi | оркестратор управляет несколькими mission_runner'ами, агрегирует логи |
| 2.3.e — analyzer multi | report сравнивает PDR / e2e для каждого БАС, видно интерференцию |

**Сложность**: ns-3 multi-node не сложно (там это родная модель), но Gazebo
с несколькими iris моделями в одном world нагружает CPU. Можно начать с n=2.

**Польза для диплома**: «масштабируемость архитектуры» — следующий уровень
после single-UAV доказательства.

### 2.4 — Ручное управление (GCS)

Цель: подключить QGroundControl или MAVProxy на host'е, оператор реально летает
через ns-3 канал. Из ТЗ: «совместно с Андрончевым и Карповым — ручное
управление одним БАС».

| Под-этап | Содержание |
|---|---|
| 2.4.a — port forward | UDP 14550 в `bas-ctrl-far` netns пробросить на host'овый интерфейс или вторую TCP-bridge |
| 2.4.b — QGC smoke | QGroundControl на Windows host'е через WSLg или socat-relay; передача команд через ns-3 |
| 2.4.c — joystick mode | manual mode (GUIDED stick input) — тестировать как degraded канал влияет на латентность управления |
| 2.4.d — UX-метрики | в логи: время от joystick input до фактического движения UAV |

**Сложность**: малая (топологически — тот же mavbridge socat плюс external port).
Полезно для демо «человек реально управляет дроном через эмулированный LoRa-канал».

---

## Этап 3 — мои предложения (не в ТЗ, но логично)

Это уже не Физулинская часть, а либо общая инфра, либо новые исследовательские
ветки. Можно делать выборочно.

### 3.1 — Hardware-in-the-loop (HIL)

Подключить **реальный radio modem** (например SiK Radio или LoRa SX1276) между
host и ns-3, чтобы ns-3 заменялся на физический канал. Это позволяет
валидировать «насколько похоже ns-3 на реальный radio».

### 3.2 — Web dashboard

Live-веб-морда на FastAPI/React: real-time графики PDR, video FPS, БАС
position на карте. Не имеет научной ценности, но кардинально улучшает demo.

### 3.3 — Optimization sweep

Скрипт `run_sweep.sh` варьирует параметры (delay 10/50/100/250/500 мс,
loss 0/1/2/5%, bitrate 500/1000/2000 kbps) и строит surface-plot
«где граница successful mission». Готовый ответ на вопрос «при каких
условиях канал ещё допустим».

### 3.4 — Mavlink router / реальная maNET

Заменить mavbridge socat на **mavlink-router** (полноценный mavlink demuxer).
Это позволит подключать одновременно автопилот + GCS + companion computer +
analytics — как реальная авионика дрона.

### 3.5 — Real-flight validation

С реальным дроном (CubeOrange + ArduPilot) сделать тот же mission, записать
лог, и проверить что симуляционный отчёт совпадает в пределах ±5%. Это
«золотая стандартная» валидация любого симулятора.

---

## Рекомендация по последовательности

С точки зрения «дать максимум для диплома/гранта за минимум усилий»,
я бы шёл так:

1. **2.1 Sionna RT** — главный пункт ТЗ Физулина, физически обоснованный
   канал. ~3-4 недели работы. Сильно усиливает defensibility.
2. **2.4 Ручное управление через GCS** — короткий шаг (~неделя), но даёт
   яркое demo: «человек реально летает через эмулированный LoRa».
3. **2.3 Multi-UAV** — масштабируемость, важная для swarm-grant'ов.
   ~2 недели для n=2.
4. **2.2 AirSim** — большой по объёму (~3 недели + GPU доступ), но
   менее научно значим если Sionna уже сделан.
5. **3.x** — по запросу руководителя.

С точки зрения «изучить что-то новое и интересное» — Sionna RT очевидный
выбор: пересечение симуляции, ray-tracing'а и сетевого моделирования,
горячая тема в академии 2024-2026.

---

## Какие архитектурные документы уже есть

- `docs/architecture.md` — общая архитектура + таблица этапов
- `docs/stage_1_5_2_plan.md` — детальный план 1.5.2 (полезен как шаблон
  для будущих 2.x-планов)
- `docs/stage_1_5_1_known_issues.md` — журнал debug'а 1.5.1
- `README.md` — пользовательская точка входа

Для следующего выбранного этапа (например 2.1) сделаю аналогичный
`docs/stage_2_1_sionna_plan.md` с DoD, под-этапами, файл-by-файл изменениями
и acceptance critera — как был для 1.5.2.
