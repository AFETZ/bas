# Roadmap проекта (после сверки с ТЗ от 17.05.2026, v1.0)

Сводный план оставшихся работ с учётом полного ТЗ группы.

См. также:
- `docs/architecture.md` — общая архитектура и таблица этапов
- `docs/tz_compliance.md` — матрица «пункт ТЗ → этап → состояние» (табель для гранта)

## Этап 1 — закрыт ✅ (v0.1 → v1.0)

| Этап | Tag | Содержание |
|---|---|---|
| 1.0–1.4 | — / v0.1 | skeleton, Docker, SITL+Gazebo, ns-3 TapBridge, MAVLink через host |
| 1.5.0–1.5.1 | v0.7 | shadow GCS + полная mission через ns-3, оба профиля |
| 1.5.2.a–d | v0.8 → v0.9 | RTP video, VideoMetrics, Gazebo камера, outage correlation, precise e2e |
| 1.6 | **v1.0** | сравнительный отчёт WiFi vs LoRa |

---

## Этап 1.7 — LoRa через Serial Port (буквальная реализация ТЗ)

**Основание:** ТЗ требует «LoRa (через Serial Port) / LoRaWAN, WiFi (TCP/IP)».
Подтверждено руководителем: нужна **буквальная** реализация, не функциональный
эквивалент (текущий `degraded_lora` через IP-канал).

### Архитектура

```
SITL → /dev/ptySITL  ──┐
                       │  PTY ↔ PTY pipe через ns-3 SerialChannel
                       │  (баудрейт-pacing, frame ≤256B, FSK BER)
GCS  → /dev/ptyGCS   ──┘
```

В отличие от текущей TapBridge-схемы (L2 ethernet frames):
- никакого IP-stack'а — байтстрим MAVLink
- baud-rate ограничивает throughput (типичный LoRa 9600-115200 baud)
- frame size ≤256 байт (LoRa physical layer limit)
- FSK BER (bit error rate) вместо packet loss ratio — потери на уровне отдельных бит

### Под-этапы

| Под-этап | Содержание |
|---|---|
| 1.7.a | Smoke: pty-pair через socat, MAVLink между orchestrator и SITL без ns-3 (proof of concept serial) |
| 1.7.b | `ns3/scenarios/lora_serial.cc` — новый сценарий с `SerialDevice` и `LoRaErrorModel` (BER + baud rate timing) |
| 1.7.c | Bridge pty → ns-3 SerialChannel (через netdev TapBridge в режиме UseLocal для байтстрима, либо отдельный pty-to-ns3 helper) |
| 1.7.d | `--mavlink-endpoint serial:/dev/ptySITL,baud=115200` в orchestrator (pymavlink уже поддерживает serial source) |
| 1.7.e | Новый профиль `configs/network_profiles/lora_serial.yaml` (SF=7-12, BW=125kHz, baud=9600-115200) |
| 1.7.f | Метрики serial: byte_loss_rate, frame_drop_rate, throughput_bps в FlowMetrics |
| 1.7.g | Run-скрипт `scripts/run_stage_1_7_lora_serial.sh` (профиль control = LoRa serial; payload остаётся WiFi или отключается) |
| 1.7.h | Acceptance: mission AUTO landed=True через LoRa serial канал с реалистичным baud=57600 + BER 1e-4 |

Размер: ~1.5-2 недели. Tag: `v1.1-stage17` или `v1.1-lora-serial`.

---

## Этап 1.8 — ROS2/MAVROS bridge (обязательный с runtime-переключением)

**Основание:** ТЗ упоминает MAVROS несколько раз. Подтверждено руководителем:
обязательная интеграция, при этом текущая pymavlink-реализация должна
остаться доступной как альтернативный режим (быстрое переключение перед
прогоном).

### Архитектура

Новый CLI-флаг:
```
bas-orchestrator <scenario> --real --mavlink-backend pymavlink   # текущий
bas-orchestrator <scenario> --real --mavlink-backend mavros      # новый
```

`mavros`-backend:
- MAVROS-нода (ROS2 Humble) в отдельном контейнере, network_mode `bas-ctrl-far-net`
- Принимает MAVLink через mavbridge, экспортирует ROS2 топики (`/mavros/state`, `/mavros/setpoint_position/local`, ...)
- Orchestrator подписывается на ROS2 топики вместо прямой `pymavlink.recv_match`
- rosbag2 пишет параллельно с JSONL для верификации

### Под-этапы

| Под-этап | Содержание |
|---|---|
| 1.8.a | ROS2 Humble base image (`bas/ros2-mavros:dev`), Dockerfile с `ros-humble-mavros` пакетом |
| 1.8.b | MAVROS-нода в `bas-ctrl-far` netns подключается к `udp://10.10.0.2:14550` через ns-3 control |
| 1.8.c | Новый `orchestrator/src/orchestrator/mavros_backend.py` — реализует тот же контракт что pymavlink-backend (interface `MavlinkBackend` с методами `connect`, `recv_match`, `send_*`) |
| 1.8.d | CLI-флаг `--mavlink-backend` + factory выбора реализации |
| 1.8.e | rosbag2 → JSONL bridge или параллельное логирование (опционально) |
| 1.8.f | Smoke оба режима: `pymavlink` и `mavros` на wifi_good, mission landed=True в обоих |
| 1.8.g | Документация runtime-переключения в README |

Размер: ~1.5-2 недели. Tag: `v1.2-stage18` или `v1.2-mavros`.

**Важно:** текущий pymavlink-код **НЕ удаляется**. Получаем два альтернативных
backend'а, переключаемых одним флагом.

---

## Этап 2.1 — Sionna RT (закрыт, v2.0)

**Status:** готов. Реализация ниже сохранена для истории и как чертёж
для повторных запусков (Sionna setup, radio-map regeneration, новые сцены).



**Основание:** ТЗ Физулина прямо требует «интеграция возможностей моделирования
затухания и отражения передаваемых между БАС радиосигналов … с помощью ns-3/Sionna RT».
Подтверждено: обязательно.

### Архитектура

```
1. iris_runway.sdf  →  scripts/export_scene_to_sionna.py  →  scene.ply (3D mesh)
2. Sionna RT  +  scene.ply  +  TX/RX positions  →  ray_traced_radio_map.npz
   (table: (x,y,z) → path_loss, delay_spread, doppler)
3. ns3/scenarios/two_channel_sionna.cc:
   - читает radio_map.npz при старте
   - на каждом MAVLink-пакете запрашивает текущую позицию UAV из Gazebo
   - lookup в таблице → текущие loss/delay/jitter
   - применяет к каналу через custom SionnaErrorModel
```

### Под-этапы

| Под-этап | Содержание |
|---|---|
| 2.1.a | Sionna 0.18 в WSL: Python venv, TensorFlow CPU/GPU, Mitsuba renderer, smoke на готовом примере |
| 2.1.b | Экспорт `iris_runway` SDF → glTF/PLY (`scripts/export_scene_to_sionna.py`); включить geometry runway + iris model + опциональные препятствия (здания/деревья) |
| 2.1.c | Sionna RT расчёт radio map для пары TX/RX (GCS позиция фиксирована, UAV пробегает grid 10x10x5 точек над runway): path_loss, delay, doppler |
| 2.1.d | `ns3/scenarios/sionna_error_model.cc` — `SionnaErrorModel : public ErrorModel` с table-lookup |
| 2.1.e | Real-time integration: Gazebo шлёт текущую UAV позицию → orchestrator → ns-3 пересчитывает params каждые N мс |
| 2.1.f | Новый профиль `configs/network_profiles/sionna_urban.yaml` ссылается на конкретную radio map |
| 2.1.g | Verification: comparison `wifi_good` (manual RateErrorModel) vs `wifi_sionna` (physically-justified) на той же траектории — channel deg должен коррелировать с препятствиями |
| 2.1.h | Доп. секция в report.md / comparison.md: «Sionna radio map» с heatmap-graphic'ом |

Размер: ~3-4 недели. Tag: `v2.0-sionna`.

---

## Этап 2.2 — AirSim как overlay над Gazebo физикой

**Уточнение из ТЗ:** «Gazebo должен использоваться в качестве симулятора
физики полёта, результат моделирования которой должен быть передан в AirSim,
который используется для высокореалистичного моделирования окружающей
обстановки и сенсоров БАС». То есть **связка**, не замена.

**Исполнитель:** Федотенков А.А. (interface с MAVLink), Андрончев+Карпов
(карта в AirSim). Моя зона — подготовить bridge-интерфейс Gazebo → AirSim.

### Под-этапы

| Под-этап | Содержание | Кто |
|---|---|---|
| 2.2.a | Cosys-AirSim в WSL2 (UE5 headless или Stage env) | Андрончев |
| 2.2.b | Gazebo → AirSim position bridge: orchestrator извлекает UAV positions из Gazebo и шлёт в AirSim как actor pose | Физулин (bridge) |
| 2.2.c | AirSim depth-camera + LiDAR sensors → ns-3 payload TAP (новые flows) | Федотенков + Физулин |
| 2.2.d | Сравнение Gazebo-only vs Gazebo+AirSim overlay: real-time-factor, FPS, payload throughput | Все трое |

Размер: ~3-4 недели в общей сложности. Tag: `v2.1-airsim-overlay`.

---

## Этап 2.3 — Multi-UAV (рой)

**Основание:** ТЗ — «несколько БАС в одной сцене» (в общих задачах).

### Под-этапы

| Под-этап | Содержание |
|---|---|
| 2.3.a | N экземпляров `bas-uav-1..N` netns'ов, каждый со своим SITL + Gazebo model |
| 2.3.b | `two_channel.cc` расширен до N MAVLink-каналов (shared control bridge или star topology с GCS) |
| 2.3.c | MANET routing (AODV/OLSR) если нужна БАС↔БАС связь |
| 2.3.d | Multi-instance mission_runner + агрегация логов в `events.jsonl` |
| 2.3.e | analyzer multi-UAV report: PDR/e2e на каждый БАС, видна интерференция в одной радио-сети |

Размер: ~2 недели для n=2-4. Tag: `v2.2-swarm`.

---

## Этап 2.4 — Ручное управление через GCS

**Основание:** ТЗ — совместная с Андрончевым/Карповым, ручное управление
как минимум одним БАС.

### Под-этапы

| Под-этап | Содержание |
|---|---|
| 2.4.a | UDP 14550 в `bas-ctrl-far` netns пробросить на host'овый интерфейс (port-forward) |
| 2.4.b | QGroundControl на Windows host'е через WSLg или socat-relay; видим heartbeat от SITL через ns-3 |
| 2.4.c | Joystick mode (GUIDED stick input через MANUAL_CONTROL MAVLink-пакеты) |
| 2.4.d | UX-метрика: задержка от joystick → реальное движение UAV; деградация на degraded_lora |

Размер: ~1 неделя. Tag: `v2.3-manual-gcs`.

---

## Этап 3 — мои предложения (не в ТЗ, опционально)

Сохраняются как backlog для отдельных запросов:

- **3.1 HIL** — реальный SiK/LoRa modem вместо ns-3
- **3.2 Web dashboard** — FastAPI + React (хорошо стыкуется с веб-интерфейсом
  Федотенкова)
- **3.3 Optimization sweep** — surface-plot «граница успешной mission»
- **3.4 mavlink-router** — полноценный demuxer вместо socat
- **3.5 Real-flight validation** — CubeOrange + сверка лога с симулятором

---

## Рекомендация по последовательности (с учётом ТЗ)

После сверки с ТЗ и подтверждения от руководителя приоритет такой:

| # | Этап | Обоснование |
|---|---|---|
| 1 | **2.1 Sionna RT** | Самый трудоёмкий + научно сильный + обязательный пункт ТЗ. Начать первым, дать долгий tail |
| 2 | **1.7 LoRa Serial** | Параллельно с 2.1 (можно делегировать Codex'у). Закрывает буквальное расхождение по LoRa |
| 3 | **1.8 ROS2/MAVROS** | После 1.7. Обязательный пункт ТЗ, не ломает текущий код (runtime-переключение) |
| 4 | **2.4 Ручное управление** | Короткий и яркий шаг для demo |
| 5 | **2.3 Multi-UAV** | Масштабируемость, swarm |
| 6 | **2.2 AirSim** | Самое тяжёлое, делать после остальных или параллельно через Федотенкова |

После закрытия 1.7 + 1.8 + 2.1 + 2.4 — **100% моих пунктов по ТЗ**.
Запасные этапы 3.x — по запросу.

## Какой выбираем сейчас

Жду решения по первому этапу из списка. Если **2.1 Sionna RT** — сделаю
детальный `docs/stage_2_1_sionna_plan.md` как был для 1.5.2.
Если **1.7 LoRa Serial** или **1.8 MAVROS** — то соответствующий план.
