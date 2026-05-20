# Roadmap и состояние работ

Актуально на 20.05.2026. Файл больше не является списком ожидающих решений:
личная зона Физулина А.В. по текущей матрице закрыта. Ниже зафиксировано, что
уже реализовано, и что остаётся как внешний backlog полного проекта.

## Закрытые этапы

| Этап | Статус | Ключевой результат |
|---|---|---|
| 1.0-1.4 | Закрыто | Skeleton, Docker, Gazebo/SITL, ns-3 TapBridge, базовый MAVLink |
| 1.5.0 | Закрыто | Shadow GCS через ns-3 control channel |
| 1.5.1 | Закрыто | AUTO mission через ns-3 на `wifi_good` и `degraded_lora` |
| 1.5.2 | Закрыто | RTP/H.264 payload, Gazebo camera, video metrics, outage correlation |
| 1.6 | Закрыто | WiFi vs LoRa comparison report + CSV |
| 1.7 | Закрыто | LoRa через Serial Port без IP-stack в радиопетле |
| 1.8 | Закрыто | ROS2/MAVROS backend как альтернативный путь управления |
| 2.1 | Закрыто | Sionna RT scene/radio map + dynamic channel hook |
| 2.4 | Закрыто | Web GCS + MAVProxy ручное управление одним БАС |
| 2.4 RF demo | Закрыто | Gazebo obstacles + live LOS/NLOS/RSSI/loss/delay graph |

## Текущая демонстрационная вершина

Основной видео-сценарий:

```bash
sudo bash scripts/run_stage_2_4_rf_demo.sh
```

Что показывает:

- Gazebo GUI с БАС, runway, полем и препятствиями;
- Browser Web GCS на `http://127.0.0.1:8765/`;
- TAKEOFF, WASD/manual velocity и `GO TO` через MAVProxy stdin;
- `GO TO` как `SET_POSITION_TARGET_LOCAL_NED`, а не body-frame velocity;
- live RF panel: LOS/NLOS, RSSI, loss, delay, график;
- события в `events.jsonl` и `ns3_events.jsonl`.

## Что осталось за рамками этой закрытой части

| Блок | Почему не закрыт здесь | Возможный следующий этап |
|---|---|---|
| AirSim / Cosys-AirSim overlay | Это отдельная связка Gazebo physics -> AirSim high-realism sensors, зона других исполнителей и отдельной интеграции | `2.2-airsim-overlay` |
| Multi-UAV / swarm | В текущем репозитории все acceptance-сценарии рассчитаны на один БАС | `2.3-multi-uav` |
| QGroundControl как внешний GUI | Stage 2.4 закрыт через Web GCS + MAVProxy; QGC не был acceptance-клиентом | `2.4-qgc-client` |
| Полный real-time Sionna ray tracing | Сейчас есть offline radio map + dynamic JSON hook; live RF demo использует geometry model | `2.1-online-rt-extension` |
| ИССГР объектная БД/API/CV | Это полный грантовый контур, шире моделирующего стенда | Отдельный репозиторий/модуль |

## Backlog без обещаний

Эти пункты полезны, но не нужны для заявления “личная зона закрыта”:

1. `--sionnaTargetFlow=control|payload|both` в `two_channel.cc`, чтобы live RF
   model мог деградировать не только payload hook, но и manual control channel.
2. QGroundControl bridge как дополнительный внешний GUI поверх уже работающей
   MAVProxy/Web GCS цепочки.
3. Multi-UAV topology в ns-3: несколько SITL/Gazebo instances и общий анализатор.
4. AirSim overlay: перенос pose из Gazebo в AirSim и возврат сенсорных потоков в
   payload channel.
5. Автоматический demo recorder: запуск сценария, браузер, Gazebo GUI и сбор
   видео/скриншотов в один отчёт.

## Где смотреть детали

- [architecture.md](architecture.md) — итоговая архитектура.
- [tz_compliance.md](tz_compliance.md) — матрица закрытия ТЗ.
- [stage_2_4_manual_gcs.md](stage_2_4_manual_gcs.md) — ручное управление и RF demo.
- [stage_1_7_lora_serial_plan.md](stage_1_7_lora_serial_plan.md) — LoRa Serial.
- [stage_1_8_mavros_plan.md](stage_1_8_mavros_plan.md) — MAVROS.
- [stage_2_1_sionna_plan.md](stage_2_1_sionna_plan.md) — Sionna RT.
