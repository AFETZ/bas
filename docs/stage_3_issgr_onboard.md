# Stage 3 — ИССГР Бортовая часть (on-board persistent БД + composite)

Persistent on-board storage для бортовой ИССГР. Закрывает требование ТЗ:

> "Бортовая часть ИССГР: бортовая объектно-ориентированная БД, сохранение
> данных сенсоров, комплексированные данные для управления БАС"
> (`docs/Краткая_выдержка_актуального_из_гранта_БАС.docx`)

## Что делает

`orchestrator/issgr/onboard.OnBoardDB` — SQLite-backed thread-safe модуль:

- **persists across reboot UAV** (файл на companion-computer);
- хранит **time-series** UAV state + sensor readings + missions;
- считает **derived/composite metrics** в фоновом потоке для feedback
  к автопилоту (NLOS detect → переключить канал, low battery → RTL,
  CV target detected → trigger mission action);
- автоматически выкидывает старые записи (retention rolling window).

## Schema

| Таблица | Назначение | Индексы |
|---|---|---|
| `uav_state` | append-only снапшоты позы | `ts_ms`, `(sysid, ts_ms)` |
| `sensor_readings` | RSSI / CV / LiDAR с JSON-value | `ts_ms`, `(sensor_type, ts_ms)`, `(source_uav_id, ts_ms)` |
| `mission_log` | загруженные миссии + execution state (upsert by `object_id`) | unique по `object_id` |
| `composite_state` | derived metrics: avg_rssi, nlos, targets, battery_pct | `ts_ms`, `(sysid, metric_name, ts_ms)` |
| `metadata` | schema_version, created_at_ms, key/value | PRIMARY KEY |

Все timestamps — epoch milliseconds (UTC). UAV identifier — string
`domain:system:uuid` для globally-unique cross-node merge.

## Composite metrics

`compute_composite(sysid, window_s=5, rssi_nlos_threshold_dbm=-75.0)`
возвращает `CompositeMetrics`:

| Поле | Источник | Алгоритм |
|---|---|---|
| `avg_rssi_5s` | `sensor_readings` где `sensor_type LIKE 'rssi%'` | `AVG(confidence)` за окно `window_s` секунд |
| `nlos_detected` | `avg_rssi_5s` | bool: `avg_rssi_5s < -75 dBm` (configurable threshold) |
| `target_count_5s` | `sensor_readings` где `sensor_type='camera_object_detection'` | count + per-class breakdown в `target_classes: dict[str,int]` |
| `battery_pct_smoothed` | последние 10 `battery_v` samples | exp filter `α=0.3` (`s' = 0.7·s + 0.3·v`), → 3S LiPo conversion `(V-9.6)/(12.6-9.6)·100` |
| `last_position_age_ms` | `MAX(ts_ms)` из `uav_state` | now_ms - last |

Каждый `compute_composite` persistит все non-None metrics в
`composite_state` для time-series аналитики.

## Composite engine (background)

```python
db.start_composite_engine(sysids=[1, 2], interval_s=1.0)
# ... спит interval_s между тиками ...
db.stop_composite_engine()
```

Daemon thread; собирает все sysid'ы каждые `interval_s` секунд.
Exception inside loop — swallowed (graceful degradation).

## Retention

Каждый INSERT в `uav_state` / `sensor_readings` / `composite_state` за
которым следует `_purge_old(table)` — удаляет всё со `ts_ms < now -
retention_seconds`. Mission log не purges (mission history short-lived).

Default `retention_seconds=3600` (1 час); on-board диск UAV redundant —
не имеет смысла хранить часы данных, всё пушится через multicast sync в
наземную ИССГР.

## Файлы

| Файл | Что |
|---|---|
| `orchestrator/src/orchestrator/issgr/onboard.py` | `OnBoardDB`, `CompositeMetrics`, schema |
| `scripts/issgr_onboard_demo.py` | Standalone demo: tail events.jsonl или synthetic данные |
| `scripts/_onboard_db_smoke.py` | CI smoke — append/query/composite/engine |
| `docs/stage_3_issgr_onboard.md` | Этот файл |

## Запуск

### Demo с synthetic данными (без events)

```bash
.venv/bin/python scripts/issgr_onboard_demo.py \
    --db-path bas_onboard.db \
    --synth-hz 10 \
    --max-seconds 30
```

Выводит каждые 5с:

```
[t+ 10.0s]
  uav= 100  sensor=  49  composite=  64
  avg_rssi_5s=-74.55  nlos=False  targets=7 {'car': 3, 'person': 2, 'tree': 2}  battery=100%
```

### Demo с реальными orchestrator events

```bash
# В одном терминале — запустить orchestrator (например stage 2.4):
sudo bash scripts/run_stage_2_4_rf_demo.sh

# В другом — on-board persistence слушает events.jsonl:
.venv/bin/python scripts/issgr_onboard_demo.py \
    --events-file logs/stage_2_4_*/orchestrator/events.jsonl \
    --db-path bas_onboard.db \
    --sysid 1
```

### CI smoke

```bash
.venv/bin/python scripts/_onboard_db_smoke.py
```

Verified outputs:
- 5 UAV state snapshots inserted
- 4 RSSI + 3 CV detections
- `compute_composite` returns `avg_rssi_5s=-68.75`, `target_classes={'person':2,'car':1}`,
  `battery_pct_smoothed=86.0%`, all asserts pass
- Background engine 1s @ 0.3s interval emits `composite_state.count: 5 → 25`

## SQL inspection

После demo run:

```bash
sqlite3 bas_onboard.db <<SQL
.headers on
.mode column

-- Last UAV state per sysid:
SELECT sysid, MAX(ts_ms) AS latest_ms,
       MIN(alt_m) AS alt_min, MAX(alt_m) AS alt_max
FROM uav_state GROUP BY sysid;

-- Composite metrics last 60s:
SELECT sysid, metric_name, AVG(metric_value) AS avg, COUNT(*) AS n
FROM composite_state
WHERE ts_ms > (SELECT MAX(ts_ms) FROM composite_state) - 60000
GROUP BY sysid, metric_name;

-- Latest CV detections:
SELECT ts_ms, json_extract(value_json, '$.class_name') AS cls,
       json_extract(value_json, '$.confidence') AS conf
FROM sensor_readings
WHERE sensor_type='camera_object_detection'
ORDER BY ts_ms DESC LIMIT 10;
SQL
```

## Integration с другими модулями

| Источник events | Получает |
|---|---|
| MAVLink telemetry (orchestrator events.jsonl `uav_state`) | `append_uav_state(UAV)` |
| `scripts/cv_detector.py` POST в `/collections/sensor_readings/items` | `append_sensor_reading(SensorReading)` для `sensor_type='camera_object_detection'` |
| `scripts/rf_publisher.py` RSSI samples | `append_sensor_reading()` для `sensor_type='rssi_lora'` / `rssi_wifi` |
| Multicast sync subscriber (`issgr_sync_subscriber.py`) | Может POST'ить в local `IssgrRepository`; on-board DB подцепляется через events.jsonl |

## Production deployment

Реальный companion-computer (Raspberry Pi / Jetson Nano):

1. systemd service запускает `issgr_onboard_demo.py --events-file
   /var/lib/orchestrator/events.jsonl --db-path /var/lib/onboard/bas.db`;
2. SQLite WAL mode переживает power-cut с минимальным data loss
   (последние ≤1s транзакций);
3. retention 1ч — диск используется ~10MB max на типичный mission;
4. Composite metrics доступны локальному companion process и Stage 4 Admin
   Dashboard; для автопилотного REST-клиента можно добавить тонкий FastAPI
   wrapper над `OnBoardDB`.

## Pattern source

- [SQLite WAL mode](https://www.sqlite.org/wal.html) — concurrent
  reader + writer, crash-safe
- [Exponential moving average](https://en.wikipedia.org/wiki/Exponential_smoothing)
  для battery pct smoothing
- [3S LiPo voltage curve](https://www.rcgroups.com/forums/showthread.php?2528127) —
  9.6V (cutoff) → 12.6V (full charge) linear approximation
- MAVLink GLOBAL_POSITION_INT — int32 lat/lon × 1e7 (по аналогии со
  Stage 3 multicast sync wire format)
