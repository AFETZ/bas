# Среда моделирования БАС - первый прототип

Минимально реализуемый стенд по архитектуре из аналитического документа Физулина А.В.: воспроизводимый прогон сценария БАС с полётным контуром, сетевыми деградациями, потоком данных и единым журналом.

## Ядро (этап 1)

```
+--------------+   +------------------+   +-------------+   +--------+
|  scenario    |   |   Gazebo Sim     |   |  ArduPilot  |   |  ns-3  |
| orchestrator |-->|    Harmonic      |<->|    SITL     |<->|  RT    |
|  (Python)    |   | (физика, сцена)  |   |  (полёт)    |   |  (сеть)|
+------+-------+   +--------+---------+   +------+------+   +---+----+
       |                    |                    |              |
       v                    v                    v              v
       +-----------------> JSONL единый журнал <-----------------+
                                  |
                                  v
                            +-----------+
                            | analyzer  |
                            | (метрики, |
                            |  отчёт)   |
                            +-----------+
```

Подробности — в [docs/architecture.md](docs/architecture.md) (исходник в `База знаний / Первичный_анализ_переработанный.docx`).

## Состав

| Каталог | Назначение |
|---|---|
| `configs/scenarios/` | YAML-сценарии (миссия + сетевой профиль + seed) |
| `configs/network_profiles/` | Параметры ns-3 для WiFi и LoRa-подобного канала |
| `configs/missions/` | Маршруты БАС (waypoints) |
| `orchestrator/` | Python-оркестратор: подъём компонентов, run_id, единый журнал |
| `analyzer/` | Python-анализатор: PDR, throughput, delay, jitter, отчёт |
| `ns3/scenarios/` | C++ сценарий ns-3 (TapBridge, два канала) |
| `ardupilot/` | Параметры и миссии для ArduPilot SITL |
| `gazebo/worlds/` | SDF-миры для Gazebo Harmonic |
| `docker/` | Dockerfile'ы для каждого компонента |
| `logs/` | Журналы прогонов (gitignored) |
| `scripts/` | Bootstrap-скрипты (install_docker.sh и др.) |

## Быстрый старт (когда Docker уже стоит)

```bash
# собрать образы
docker compose build

# прогнать baseline-сценарий (WiFi-канал, без деградации)
./scripts/run_scenario.sh baseline_wifi

# прогнать деградированный сценарий (узкополосный канал)
./scripts/run_scenario.sh degraded_lora

# построить отчёт
python -m analyzer logs/<run_id>
```

## Состояние компонентов

| Компонент | Этап 1 | Статус |
|---|---|---|
| Оркестратор + единый JSONL-журнал | да | **работает** (stub + --real) |
| Анализатор метрик | да | **работает** (PDR/delay/jitter/outage/landed/waypoints) |
| Конфиги (YAML) | да | сценарии + сетевые профили + миссии |
| ArduPilot SITL контейнер | да | **собран** (bas/ardupilot-sitl:dev) |
| Gazebo Harmonic контейнер | да | **собран** (bas/gazebo-harmonic:dev) |
| ns-3 + TapBridge + 2 канала | да | **работает** (TapBridge UseLocal, RateErrorModel, outage, JSONL) |
| Host bridges + TAP/veth/netns | да | **готов** (`scripts/setup_radio_net.sh`) |
| Реальный полёт через MAVLink (host network) | да | **работает** (этап 1.4) |
| Shadow GCS в bas-ctrl-far netns через ns-3 | да | **работает** (этап 1.5.0) |
| Mission через ns-3, profile `wifi_good` | да | **работает** v0.7 (AUTO + UDP bridge) |
| Mission через ns-3, profile `degraded_lora` | да | **работает** v0.7 (250ms delay + 2% loss + outage) |
| Видеопоток камеры Gazebo через payload-канал | да | 1.5.2.a/b/c: камера, метрики, MP4, корреляция outage ↔ video gaps |
| Сравнительный отчёт WiFi vs LoRa | нет | этап 1.6 |
| Sionna RT (офлайн радиокарты) | нет | этап 2 |
| AirSim / Cosys-AirSim | нет | этап 2 |
| Несколько БАС / рой | нет | этап 2 |

## Этап 1.5: радио-петля SITL ↔ ns-3 (v0.7)

Реализован полный mission run из netns `bas-ctrl-far` через ns-3 канал в SITL+Gazebo,
размещённые в shared netns `bas-uav` (Kubernetes pause-container pattern).

Транспорт: MAVLink UDP через `mavbridge` (socat-sidecar в shared netns) обходит TCP
head-of-line blocking; mission upload протокол укреплён COUNT-burst'ом и adaptive
silence-limit'ом для устойчивости к 250ms+2% loss каналу.

```bash
# baseline wifi (~5ms delay)
sudo bash scripts/run_stage_1_5_1_mission.sh wifi_good

# degraded LoRa-подобный (250ms delay, 2% loss, 2× outage по 3s)
sudo bash scripts/run_stage_1_5_1_mission.sh degraded_lora
```

Отчёт по прогону — `logs/<run_id>/report.md`. Подробности про root causes и решения —
[docs/stage_1_5_1_known_issues.md](docs/stage_1_5_1_known_issues.md).

## Этап 1.5.2 (v0.8.1+): RTP-видео через payload-канал

Под-этап smoke реализован: H.264 RTP-поток (sender → ns-3 payload TAP → receiver)
работает на обоих профилях. Анализатор автоматически добавляет секцию
«Видеопоток» в `report.md`, если в прогоне есть `video_tx.jsonl` /
`video_rx.jsonl`: FPS, frame loss, RFC 3550 jitter, bitrate goodput,
приблизительная e2e latency и корреляция payload outage ↔ video gaps.

```bash
sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
sudo bash scripts/run_stage_1_5_2_mission.sh degraded_lora
```

По умолчанию источник видео — `videotestsrc` (smoke). Для проверки реальной
камеры Gazebo используется режим `BAS_VIDEO_SOURCE=camera`:

```bash
sudo service docker start
docker compose build gazebo video
sudo env BAS_VIDEO_SOURCE=camera bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

Этот режим включает штатный `GstCameraPlugin` через Gazebo topic
`.../enable_streaming`, затем retap'ит H.264 RTP с `127.0.0.1:5600` в payload
канал ns-3.

По умолчанию прогон headless: окно Gazebo не открывается, а принятый
видеопоток сохраняется в `logs/<run_id>/video_rx.mp4`. Для визуального демо
через WSLg можно открыть GUI:

```bash
sudo env BAS_VIDEO_SOURCE=camera BAS_GAZEBO_GUI=1 bash scripts/run_stage_1_5_2_mission.sh wifi_good
```

## Дальнейшие шаги

1. Этап 1.5.2.d — точная e2e latency через GstPad probe / RTCP sender reports
2. Этап 1.6 — сравнительный отчёт WiFi vs LoRa-подобный канал (markdown + CSV)
3. Стабилизация real-camera `degraded_lora` upload-флейка (`MISSION_ACK type=13`)
4. Этап 2 — Sionna RT (офлайн радиокарты), AirSim/Cosys-AirSim, рой
