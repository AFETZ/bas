# Архитектура первого прототипа

Краткая выжимка из `Первичный_анализ_переработанный.docx` с уточнениями по реализации.

## Контуры

1. **Сценарный оркестратор** (`orchestrator/`).
   Создаёт `run_id`, читает конфиг, поднимает компоненты, держит главный цикл, гасит компоненты по завершению, пишет события в общий журнал.

2. **Полётный контур.**
   - Реально: Gazebo Sim Harmonic ↔ ardupilot_gazebo plugin ↔ ArduPilot SITL.
   - На этапе 1 (stub): `orchestrator.components.StubGazeboArduPilot` эмулирует движение по waypoints без физики.

3. **Сетевой контур.**
   - Реально: ns-3 в режиме реального времени + TapBridge + два TAP-моста (control, payload). RateErrorModel + DelayJitterEstimator. Outage'и через `Simulator::Schedule`.
   - На этапе 1 (stub): `orchestrator.components.StubNs3Channel` эмулирует доставку пакетов согласно профилю (delay, jitter, loss, outage).

4. **Поток полезной нагрузки.**
   - Реально: ffmpeg/GStreamer стрим камеры Gazebo → отдельный TAP → ns-3 → приёмник в host.
   - На этапе 1: stub отправляет пакеты с частотой 30 Hz и размером 1200 байт.

5. **Логирование.**
   - Единый JSONL `logs/<run_id>/events.jsonl`. Схема событий описана в `orchestrator/src/orchestrator/logger.py` (EVENT_TYPES).
   - ns-3 пишет в тот же каталог `ns3_events.jsonl` (этап 2).

6. **Анализатор.**
   - `analyzer/` читает JSONL, считает PDR, задержку, jitter, goodput, проверяет успешность миссии. Сохраняет markdown-отчёт.

## Схема событий журнала

Соответствует таблице 5 архитектурного документа.

| event_type | основные поля |
|---|---|
| `run_start` | run_id, scenario_id, config_hash, seed, versions, control_profile, payload_profile, mission |
| `run_end` | run_id, scenario_id |
| `scenario` | sim_time, status (success / timeout / failure), reason |
| `flight` | sim_time, vehicle_id, position{x,y,z}, velocity_mps, flight_mode, mission_state, waypoint_index |
| `control_telemetry` | (зарезервировано на этап 2 - событие команды/телеметрии в host-приложении) |
| `network` | sim_time, flow_id, packet_id, tx_time, rx_time, drop_reason, delay, jitter, throughput_bps, outage_state |
| `payload` | sim_time, flow_id, payload_id, capture_time, send_time, receive_time, size_bytes, drop_reason |
| `sync` | sim_time, gazebo_time, ns3_time, real_time_factor, position_desync_m |
| `component` | component, phase (start / stop), произвольные поля |

## Карта файлов под зону ответственности Физулина А.В.

В рамках проекта группы Физулин А.В. отвечает за:
- **Два канала связи** (управление + видеопоток), эмуляция WiFi (TCP/IP) и LoRa/LoRaWAN (Serial):
  → `configs/network_profiles/wifi_good.yaml`, `configs/network_profiles/lora_narrowband.yaml`
  → `orchestrator/src/orchestrator/components.py:StubNs3Channel`
  → `ns3/scenarios/two_channel.cc` (реальная реализация)

- **ns-3 / Sionna RT** (error rate, распределение ошибок, пропускная способность):
  → метрики считаются в `analyzer/src/analyzer/metrics.py`
  → Sionna RT не входит в этап 1; в этапе 2 - офлайн-расчёт радиокарт, результат подаётся как параметр профиля в ns-3.

- **Карта тестового сценария в ns-3/Sionna RT** для 3D-препятствий:
  → `gazebo/worlds/basic.sdf` определяет геометрию, ns-3 читает позиции через TapBridge-моделирование. Sionna - этап 2.

- **Совместно с Андрончевым и Карповым** - ручное управление одним БАС:
  → этап 2, подключение GCS (QGroundControl / MAVProxy) на порт 5760.

## Этапы

| Этап | Содержание | Состояние |
|---|---|---|
| 1.0 | Skeleton: оркестратор, журнал, анализатор, stub-компоненты, конфиги | **готов** |
| 1.1 | Docker в WSL, базовые образы (ArduPilot SITL, Gazebo Harmonic, ns-3) | следующий шаг |
| 1.2 | ArduPilot SITL ↔ Gazebo Harmonic через ardupilot_gazebo plugin | после 1.1 |
| 1.3 | ns-3 two_channel.cc с TapBridge + два моста (control, payload) | после 1.2 |
| 1.4 | MAVLink команды и телеметрия через TAP control | после 1.3 |
| 1.5 | Видеопоток камеры Gazebo через TAP payload | после 1.4 |
| 1.6 | Сценарии нагрузочного теста + сравнительный отчёт WiFi vs LoRa | после 1.5 |
| 2.x | Sionna RT (офлайн радиокарты) | этап 2 |
| 2.x | AirSim / Cosys-AirSim как визуально-сенсорная ветка | этап 2 |
| 2.x | Несколько БАС, рой | этап 2 |

## Stub vs Real

`bas-orchestrator <scenario>` запускает в stub-режиме (без Docker). Это нужно для отладки оркестратора и анализатора. Stub детерминированно эмулирует прогон по тому же контракту событий.

`bas-orchestrator <scenario> --real` (заглушка, ещё не реализована) поднимет `docker compose up` и будет ждать события от реальных компонентов.

## Известные упрощения этапа 1

- Flight stub не реагирует на сетевые задержки управления (в реальном прототипе ArduPilot задержит команду и реакция БАС изменится).
- Network stub генерирует пакеты независимо от полётного контура (т.е. в LoRa-профиле БАС летит как обычно, хотя реальный ArduPilot тоже задержался бы).
- TapBridge ещё не подключён в `two_channel.cc` - сейчас это каркас с CommandLine-аргументами и пустым `Simulator::Run()`.
- Sionna RT и AirSim - вне scope этапа 1.
