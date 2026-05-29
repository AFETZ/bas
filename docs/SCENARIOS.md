# Сценарные рецепты — связки модулей

Сила стенда не в отдельных модулях, а в их **цепочках**. Здесь — готовые
end-to-end сценарии: что с чем соединяется, какими командами, что вы
увидите и какой пункт ТЗ это демонстрирует.

Карта модулей — `docs/MODULE_MAP.md`. Глоссарий терминов — `docs/GLOSSARY.md`.

---

## Рецепт 0 — «Весь стенд за раз» (для первого знакомства)

**Цель:** одной командой поднять все модули и увидеть их связь в браузере.

**Цепочка:** ИССГР API ×2 → multicast sync → бортовая БД → AirSim stub +
scene → JsonFdmBridge + SITL emulator → cyber monitor → Sionna RT → admin.

```bash
bash scripts/run_master_demo.sh          # откроет http://127.0.0.1:8810/
```

**Что увидеть (маршрут осмотра):**
1. `http://127.0.0.1:8810/` вкладка **Обзор** — сколько модулей живо.
2. вкладка **ИССГР** — объекты цифрового двойника.
3. `http://127.0.0.1:8770/docs` — Swagger API для АСУ-клиента.
4. `http://127.0.0.1:8811/stats` — счётчики multicast sync.
5. `logs/master_demo_*/sionna_tiles/` — реальный RT-расчёт Мюнхена.

**Закрывает:** интеграционную демонстрацию Stage 3/4.

---

## Рецепт 1 — «Разведка над реальным городом»

**Цель:** взять реальный город, поднять его как сцену, пролететь, распознать
объекты, синхронизировать обстановку на второй узел.

**Цепочка модулей:**
```
OSM importer ─► ИССГР obstacles ─► Gazebo сцена ─► полёт (SITL+Web GCS)
                     │                                     │
                     │                              FPV camera ─► CV детектор
                     ▼                                     ▼
              admin dashboard                      ИССГР sensor_readings
                     ▲                                     │
              multicast sync ◄───────────────────────────┘
```

**Шаги:**
```bash
# 1. Импорт реального города (любая точка Земли) с рельефом
./scripts/import_osm_scenario.py --place "Тверская, Москва" --radius-m 300 \
    --with-terrain --issgr-url http://127.0.0.1:8770

# 2. Поднять ИССГР + полётный стек
sudo bash scripts/run_stage_2_4_fpv_rf_demo.sh

# 3. CV-детектор подписывается на FPV и кладёт цели в ИССГР
./scripts/cv_detector.py --issgr-url http://127.0.0.1:8770
```

**Что увидеть:** реальные здания Москвы в ИССГР `/collections/obstacles`,
дрон летает между ними в Gazebo, CV кладёт распознанные объекты в
`/collections/sensor_readings`, admin показывает всё на спутниковой подложке.

**Закрывает:** «карта сценария», «обработка видовых данных», «наземная ИССГР».

---

## Рецепт 2 — «Деградация канала в городском каньоне»

**Цель:** показать физически обоснованное падение связи, когда БАС уходит
в радиотень за здание.

**Цепочка модулей:**
```
Sionna RT (Munich/город) ─► RSSI heatmap ─► /tmp/sionna_channel.json
                                                     │
                                                     ▼
                              ns-3 two_channel (control + payload)
                                                     │
                                                     ▼
                                   Web GCS RF-панель (RSSI/loss/delay)
```

**Шаги:**
```bash
# Вариант A — live Sionna RT в реальном городе (нужен GPU/OptiX)
bash scripts/run_sionna_live.sh real_tile --scene-name munich --freq-mhz 2400

# Вариант B — полный demo с RF-панелью и ручным полётом
sudo bash scripts/run_stage_2_4_rt_online_demo.sh
```

**Что увидеть:** при заходе дрона за здание RSSI падает, ns-3 повышает loss
и delay **на обоих каналах** (control + payload), видео-поток рвётся,
команды задерживаются — в RF-панели Web GCS видно в реальном времени.

**Закрывает:** «ns-3 затухание/отражение», «Sionna RT радиокарты»,
«ручное управление при деградации».

---

## Рецепт 3 — «Бортовая аналитика и доступ АСУ»

**Цель:** показать, что борт хранит данные, считает комплексированные
метрики, а ведомственная АСУ читает обстановку через стандартный REST.

**Цепочка модулей:**
```
полёт (SITL) ─► events.jsonl ─► ИССГР API ─► бортовая SQLite БД
                                    │              │
                                    │       composite engine
                                    │       (avg RSSI, NLOS, battery)
                                    ▼              ▼
                          АСУ-клиент REST     admin (Бортовая БД)
```

**Шаги:**
```bash
# 1. ИССГР + бортовая БД + полётный стек
bash scripts/run_stage_3_issgr_demo.sh

# 2. Пример АСУ-клиента: тянет обстановку через OGC API
./scripts/issgr_asu_client_demo.py --issgr-url http://127.0.0.1:8770
```

**Что увидеть:** `/digital_twin` отдаёт единый GeoJSON всей обстановки;
бортовая SQLite копит time-series; composite engine считает avg RSSI за
5с, NLOS-флаг, сглаженный заряд батареи; admin вкладка «Бортовая БД»
показывает метрики.

**Закрывает:** «бортовая ИССГР», «комплексированные данные», «имитаторы АСУ».

---

## Рецепт 4 — «Кибербезопасность канала управления»

**Цель:** показать, что детектор распознаёт типовые атаки на БАС-канал.

**Цепочка модулей:**
```
cyber_attack_simulator ─► MAVLink endpoint / sionna channel
   (GPS spoof / cmd inject / RF jam)        │
                                            ▼
                              cyber_defense_monitor ─► алерты (NDJSON)
```

**Шаги:**
```bash
# Round-trip smoke: монитор + 3 атаки + детект
./scripts/_cyber_smoke.py

# Или вручную: монитор в одном терминале, атаки в другом
./scripts/cyber_defense_monitor.py --mavlink-port 14559 --channel-file /tmp/ch.json
./scripts/cyber_attack_simulator.py gps_spoof --target-port 14559 --offset-lat-m 250
```

**Что увидеть:** монитор поднимает алерты — GPS spoof (скачок позиции >100м),
cmd injection (команда от неавторизованного sysid), RF jam (RSSI < -90 dBm).

**Закрывает:** «опционально — кибератаки» (defensive research).

---

## Рецепт 5 — «Интерфейс автопилота через свою физику»

**Цель:** реальный ArduPilot SITL летает по нашей 6DOF-физике через JSON-FDM
мост (а не по встроенной физике SITL).

**Цепочка модулей:**
```
ArduPilot SITL (--model json) ─► servo PWM ─► JsonFdmBridge
                                                  │ multirotor 6DOF + IMU noise
                                                  ▼
                                        sensor JSON (IMU/GPS/pose) ─► EKF
                                                  │
                                                  ▼ (опц.) simSetVehiclePose
                                            Cosys-AirSim визуал
```

**Шаги:**
```bash
# End-to-end smoke: SITL ↔ bridge ↔ ARM ↔ takeoff
.venv/bin/python scripts/_real_sitl_e2e_smoke.py

# Или demo-обёртка bridges
bash scripts/run_stage_4_sim_bridges_demo.sh smoke
```

**Что увидеть:** SITL принимает наши IMU/GPS, EKF сходится, дрон arm'ится и
взлетает; bridge гоняет 4500+ PWM-кадров в секунду.

**Закрывает:** «ArduPilot ↔ AirSim interface», «MAVLink ↔ Gazebo/AirSim».

---

## Какой рецепт для чего

| Хочу показать… | Рецепт |
|---|---|
| Всё сразу, интеграцию | 0 |
| Реальный город + распознавание | 1 |
| Падение связи за зданием | 2 |
| Цифровой двойник + АСУ | 3 |
| Защиту от кибератак | 4 |
| Реальный автопилот + физику | 5 |
