# UI Guide — что означают сайты BAS demo

Этот документ нужен для человека, который впервые открыл стенд и видит несколько
адресов в браузере. Главное: в репозитории есть **два разных класса UI**.

1. **Operator UI / Web GCS** — пульт управления полётом.
2. **Admin Dashboard / API docs / sync stats** — витрина состояния, проверки и
   интеграции ИССГР.

## Быстрый выбор

| Что хотите сделать | Куда идти | Команда запуска |
|---|---|---|
| Показать все интеграционные модули грантового стенда | `http://127.0.0.1:8810/` | `bash scripts/run_master_demo.sh` |
| Управлять одним БАС вручную | `http://127.0.0.1:8765/` | `sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh` |
| Записать красивый flight demo автоматически | `logs/<run>/demo_report.md` | `sudo bash scripts/run_stage_2_4_auto_demo.sh` |
| Запустить 2 SITL + 2 iris | Web GCS/QGC stack | `sudo bash scripts/run_stage_2_4_multi_uav_demo.sh` |
| Проверить API цифрового двойника | `http://127.0.0.1:8770/docs` | часть `run_master_demo.sh` или `run_stage_3_issgr_demo.sh` |
| Проверить raw multicast counters | `http://127.0.0.1:8811/stats` | часть `run_master_demo.sh` |

## Admin Dashboard `:8810`

Это не пульт. Он отвечает на вопрос: **какие подсистемы стенда живы и что они
публикуют прямо сейчас?**

### Обзор

Показывает:
- сколько collection типов есть в ИССГР;
- сколько БАС зарегистрировано в цифровом двойнике;
- сколько препятствий загружено из urban/RF scene;
- сколько строк накопила бортовая SQLite БД;
- какие endpoints доступны.

Закрывает: демонстрационную витрину Stage 4 admin dashboard и связку
ИССГР + on-board DB + sync.

### ИССГР

Показывает GeoJSON objects из collections: `uavs`, `obstacles`, `gcs`,
`missions`, `sensor_readings`.

Что можно делать:
- выбрать collection;
- увидеть id, имя, geometry, coordinates и свойства;
- открыть Swagger API для ручного `GET/POST`.

Закрывает: наземную ИССГР, OGC API Features, цифровой двойник и API для
имитаторов АСУ.

### БАС / Multi-UAV

Показывает только те БАС, которые сейчас есть в ИССГР node-A.

Почему в `run_master_demo.sh` виден один БАС:
- master demo seed'ит **один synthetic UAV** как объект цифрового двойника;
- цель master demo — показать интеграцию ИССГР/sync/on-board/admin, а не
  реальный swarm flight control;
- это нормальное состояние для master demo.

Где два БАС:

```bash
sudo bash scripts/run_stage_2_4_multi_uav_demo.sh
```

Где управление:
- Web GCS / MAVProxy;
- QGroundControl через mavp2p;
- auto demo recorder.

Admin Dashboard intentionally не отправляет flight commands, чтобы не смешивать
административный мониторинг и операторское управление.

### Бортовая БД

Показывает SQLite WAL хранилище на борту:
- `uav_state`;
- `sensor_readings`;
- `mission_log`;
- `composite_state`.

Закрывает: бортовую часть ИССГР, сохранение sensor/time-series данных и
производные метрики для управления.

### Карта 20x20 км

Показывает tile grid algorithm. Это не flight map, а proof, что большую
территорию можно нарезать на tiles для:
- пространственного индекса ИССГР;
- Sionna cache keys;
- AirSim asset streaming.

### Multicast Sync

Показывает human-readable слой над raw endpoint `:8811/stats`.

Закрывает: автоматическую синхронизацию БД, heartbeat, L1 position packets,
L2 sensor packets, compact UDP multicast 40/80 bytes.

## Swagger `:8770/docs`

Это developer/API explorer, не демонстрационная страница для комиссии.

Используйте его когда нужно:
- посмотреть JSON schema моделей;
- вручную сделать `GET /digital_twin`;
- POST'нуть новый `UAV`, `Obstacle`, `Mission` или `SensorReading`;
- проверить, что внешний имитатор АСУ сможет работать с ИССГР API.

Если нужен понятный обзор, сначала смотрите Admin Dashboard `:8810`.

## Sync stats `:8811/stats`

Это raw JSON endpoint для машинного мониторинга. Он специально простой:

```json
{
  "ok": true,
  "totals": {"L1": 109, "L2": 0, "HEARTBEAT": 109}
}
```

Для человека удобнее вкладка **Multicast Sync** в Admin Dashboard.

## Operator Web GCS `:8765`

Это настоящий операторский интерфейс:
- GUIDED / ARM / FORCE / TAKEOFF / LAND;
- WASD / arrows velocity control;
- GO TO по карте;
- FPV overlay;
- RF LOS/NLOS panel.

Запуск:

```bash
sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh
```

Именно этот UI закрывает ручное управление одним БАС через GCS.

## Как объяснять demo одной фразой

`run_master_demo.sh` показывает, что все backend-модули грантового стенда
интегрированы и наблюдаемы. `run_stage_2_4_fpv_rf_demo.sh` показывает ручное
управление полётом. `run_stage_2_4_multi_uav_demo.sh` показывает multi-SITL
конфигурацию. Это разные сценарии, а не один экран, который обязан делать всё.
