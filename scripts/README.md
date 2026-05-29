# scripts/ — карта скриптов

В каталоге ~90 файлов. Чтобы не путаться: вот классификация по
назначению. Физически не разносим по подпапкам — на скрипты ссылаются
demo-обёртки и smoke'и по фиксированным путям.

## 🚀 Точки входа (с них начинают)

| Файл | Что |
|---|---|
| `demo.sh` | **Интерактивное меню** «что хотите увидеть?» — главная точка входа |
| `run_master_demo.sh` | Весь стенд за раз + Admin Dashboard в браузере |
| `run_all_smokes.sh` | **Регрессия**: все offline-smoke одной командой (`--live` для network/GPU/SITL) |
| `bootstrap.sh` | Установка стенда |

## 🎬 Demo-обёртки (`run_stage_*.sh`, `run_*.sh`)

Каждая поднимает свой стек с набором env. Полный каталог —
`docs/DEMOS.md`, рецепты связок — `docs/SCENARIOS.md`. Ключевые:

| Файл | Сценарий |
|---|---|
| `run_stage_2_4_auto_demo.sh` | Автоматический flight-фильм с отчётом |
| `run_stage_2_4_fpv_rf_demo.sh` | Ручной полёт + FPV + RF-панель |
| `run_stage_2_4_rt_online_demo.sh` | Live Sionna RT + деградация каналов |
| `run_stage_2_4_multi_uav_demo.sh` | 2 SITL + 2 iris |
| `run_stage_2_4_qgc_demo.sh` | Web GCS + QGroundControl |
| `run_stage_3_issgr_demo.sh` | Цифровой двойник ИССГР + АСУ |
| `run_stage_3_*_demo.sh` | CV / urban / sync демонстрации |
| `run_stage_1_*` | Acceptance smoke стадий 1.x (mission AUTO, LoRa, MAVROS) |
| `run_stage_4_sim_bridges_demo.sh` | MAVLink fanout + JSON-FDM bridge |
| `run_sionna_live.sh` | Sionna RT live wrapper (env для OptiX) |

## 🧩 Модули (импортируемые / standalone сервисы)

| Файл | Модуль (см. `docs/MODULE_MAP.md`) |
|---|---|
| `issgr_api_server.py` | ИССГР REST/OGC API |
| `issgr_sync_publisher.py` / `issgr_sync_subscriber.py` | Multicast sync |
| `issgr_asu_client_demo.py` | Пример АСУ-клиента |
| `admin_web_server.py` | Admin Dashboard backend |
| `gcs_web_ui_server.py`, `mavproxy_stage_2_4_driver.py` | Web GCS |
| `arducopter_airsim_interface.py`, `multirotor_dynamics.py` | JsonFdmBridge + 6DOF |
| `mavlink_sim_router.py` | MAVLink 1→N fanout |
| `airsim_{client,bridge,stub_server,scene_builder}.py` | Cosys-AirSim overlay |
| `cv_detector.py` | YOLOv8 CV → ИССГР |
| `cyber_attack_simulator.py`, `cyber_defense_monitor.py` | Кибер атака/защита |
| `sionna_real_tile.py` | Sionna RT tile (cached + live) |
| `import_osm_scenario.py`, `terrain_elevation.py` | OSM + рельеф → сценарий |
| `install_ardupilot.sh`, `install_mitsuba_optix_wsl.sh` | Установщики deps |

## 🧪 Smoke-тесты (`*_smoke.py`, `*_smoke.sh`)

Канонический тест-сьют. Запускаются через `run_all_smokes.sh` или
поодиночке. Offline-safe (без сети/GPU) vs live помечены в runner'е.

| Smoke | Проверяет |
|---|---|
| `_multirotor_dynamics_smoke.py` | X-config 6DOF физика + IMU noise |
| `_large_map_smoke.py` | TileGrid + SpatialIndex |
| `_mavlink_sim_router_smoke.py` | MAVLink fanout |
| `_onboard_db_smoke.py` | Бортовая SQLite + composite |
| `_parallel_smoke.py` | TaskScheduler + SITL fleet + Sionna pool |
| `_arducopter_airsim_smoke.py` | JSON-FDM round-trip |
| `_cyber_smoke.py` | 3 атаки + детектор |
| `_sync_loopback_smoke.py` | Multicast 40/80B пакеты |
| `_airsim_scene_smoke.py` | Spawn urban-каталога |
| `_admin_web_smoke.py` / `_admin_web_integration_smoke.py` | Admin endpoints |
| `_osm_import_smoke.py` | OSM importer (+`--live` сеть) |
| `_terrain_smoke.py` | Terrain elevation (+`--live` сеть) |
| `_sionna_scenes_smoke.py` | Built-in city scenes (нужен sionna_env) |
| `_real_sitl_e2e_smoke.py` | Real ArduPilot SITL ↔ bridge (нужен `~/ardupilot`) |
| `_sync_stats_smoke.py` | Sync publisher /stats |

## 🔧 Диагностика / helpers (`_*.py`, `_*.sh` не-smoke)

Вспомогательные, запускаются вручную при отладке:
`_check_flight.py`, `_show_arm_state.py`, `_show_phases.py`,
`_mjpeg_static_server.py`, `_sionna_cuda_minimal.py`,
`_sionna_polarized_test.py` (последний используется `run_sionna_live.sh`).

## Конвенция

- `run_*` — пользовательские обёртки (что-то поднимают и показывают).
- `*_smoke.*` — автотесты, печатают `ALL CHECKS PASSED`.
- `_*` (underscore) — внутреннее (smoke / helper / диагностика).
- остальное (`*.py` без underscore) — модули и сервисы стенда.
