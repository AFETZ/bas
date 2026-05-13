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
| Анализатор метрик | да | **работает** (поддерживает оба формата позиции) |
| Конфиги (YAML) | да | сценарии + сетевые профили + миссии |
| ArduPilot SITL контейнер | да | **собран** (bas/ardupilot-sitl:dev) |
| Gazebo Harmonic контейнер | да | **собран** (bas/gazebo-harmonic:dev) |
| ns-3 + TapBridge + 2 канала | да | **работает** (TapBridge UseLocal, RateErrorModel, outage, JSONL) |
| Host bridges + TAP/veth/netns | да | **готов** (scripts/setup_radio_net.sh) |
| Реальный полёт через MAVLink | да | **работает** (DockerComposeFlightStack + MissionRunner) |
| ns-3 в радио-петле с SITL | нет | проверено через ping в netns, интеграция с SITL — этап 1.5 |
| Sionna RT (офлайн радиокарты) | нет | этап 2 |
| AirSim / Cosys-AirSim | нет | этап 2 |
| Несколько БАС / рой | нет | этап 2 |

## Дальнейшие шаги

1. Установить Docker в WSL (`scripts/install_docker.sh`)
2. Собрать ArduPilot SITL образ (`docker compose build sitl`) - реальная сборка займёт ~30 минут
3. Собрать ns-3 образ с TapBridge - ~20 минут
4. Прогнать baseline сценарий
5. Сверить логи и отчёт со столбцами из таблицы 5 архитектурного документа
