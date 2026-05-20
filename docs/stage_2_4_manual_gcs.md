# Stage 2.4 Manual GCS

## Цель

Stage 2.4 закрывает пункт ТЗ «ручное управление одним БАС через GCS».
Основной live demo режим теперь показывает операторский Web GCS UI, а
командным backend остаётся MAVProxy command-line GCS:

```text
Browser operator UI -> MAVProxy GCS -> ns-3 control channel -> mavbridge -> SITL
```

Acceptance-прогон должен показать не только heartbeat, а живую командную
цепочку в GUIDED режиме: `mode GUIDED`, `arm throttle`, `takeoff 10`,
live `velocity` через кнопки/клавиатуру и `SET_POSITION_TARGET_LOCAL_NED`
для `GO TO`, затем `mode LAND` и посадку или disarm.

## Почему строго MAVProxy

Прямой `pymavlink` mini-GCS доказывает только MAVLink command path от
скрипта к SITL. Он не доказывает, что управление выполнялось через GCS/НПУ.
Поэтому для Stage 2.4 acceptance path команды управления отправляет только
MAVProxy CLI. `pymavlink` допустим отдельно как диагностический probe, но
не как основной acceptance path и не как источник flight-команд.

Mission upload для этого теста не используется: Stage 2.4 является live
manual/GCS command demo, а не автономным полётным заданием.

## Запуск

Live operator UI с Gazebo GUI:

```bash
sudo env BAS_GAZEBO_GUI=1 BAS_STAGE24_FORCE_ARM=1 \
  bash scripts/run_stage_2_4_operator_ui.sh
```

Live RF/LOS demo с видимыми препятствиями в Gazebo и графиком RSSI/loss:

```bash
sudo bash scripts/run_stage_2_4_rf_demo.sh
```

Этот режим поднимает `iris_runway_rf_demo.sdf`: runway + поле + дрон +
видимые препятствия `Hangar`/`Tower`/GCS mast. Web GCS поверх карты рисует
те же препятствия, линию GCS→UAV, статус `LOS`/`NLOS`, RSSI, packet loss
и delay. При уходе дрона за hangar статус переключается в `NLOS`, RSSI
падает, а график показывает деградацию канала. Текущая RF-модель также
пишется в `/tmp/bas_stage24_rf.json`; ns-3 получает этот JSON через
`--sionnaChannelPath` и фиксирует `ns3:sionna_poll channel_updated` в
`ns3_events.jsonl`.

Открыть в браузере:

```text
http://127.0.0.1:8765/
```

В UI доступны `GUIDED`, `ARM`, `TAKEOFF`, `LAND`, удержание `W/A/S/D` для
скорости и клик по карте / `GO TO` для локальной точки. `GO TO` отправляет
через MAVProxy stdin сообщение `SET_POSITION_TARGET_LOCAL_NED`, поэтому
точка на карте трактуется как абсолютные локальные координаты `N/E`, а не
как body-frame скорость. Прямой `pymavlink` в UI не используется.

UI-only preview без SITL/Gazebo/ns-3:

```bash
.venv/bin/python scripts/gcs_web_ui_server.py --demo --port 8765
```

Smoke acceptance:

```bash
sudo bash scripts/run_stage_2_4_mavproxy_gcs.sh
```

Интерактивная проверка оператором:

```bash
sudo bash scripts/run_stage_2_4_mavproxy_gcs.sh interactive
```

Dry-run без запуска стенда:

```bash
bash scripts/run_stage_2_4_mavproxy_gcs.sh dry-run
```

Основной endpoint по умолчанию:

```text
udpout:10.10.0.2:14550
```

Это endpoint из `bas-ctrl-far` netns в сторону BAS-side namespace:
`MAVProxy -> ns-3 control -> mavbridge UDP 14550 -> SITL TCP 5760`.
Не подставлять `127.0.0.1` вслепую: в Docker/netns это будет другой
network namespace.

Переопределение endpoint:

```bash
sudo env BAS_STAGE24_MAVPROXY_MASTER=udpout:10.10.0.2:14550 \
  bash scripts/run_stage_2_4_mavproxy_gcs.sh
```

Если SITL acceptance реально упирается в pre-arm checks, force arm включается
только явно:

```bash
sudo env BAS_STAGE24_FORCE_ARM=1 bash scripts/run_stage_2_4_mavproxy_gcs.sh
```

В отчёте это фиксируется как `Force arm used: true`.

## Честные границы RF/LOS demo

- RF/LOS panel — live geometry model в Web GCS: луч `GCS -> UAV` пересекается
  с препятствиями `Hangar`/`Tower`, после чего UI считает `LOS/NLOS`, RSSI,
  loss и delay.
- Этот live-график нужен для операторского видео и быстрой демонстрации связи
  “препятствие -> NLOS -> падение RSSI”.
- Полный Sionna RT pipeline в репозитории есть отдельно: offline scene/radio
  map + dynamic JSON hook. Stage 2.4 RF demo не делает настоящий Sionna
  ray tracing на каждом кадре Gazebo.
- Channel JSON передаётся в ns-3 polling как RF hook; базовое ручное управление
  в этом demo остаётся на baseline control channel, чтобы операторский полёт не
  срывался во время показа NLOS-графика.

## MAVProxy нюансы

- В MAVProxy 1.8.74 нет `--script`; runner проверяет `--help` и не использует
  эту опцию.
- `mavinit.scr` запускается на старте и не ждёт vehicle, поэтому в
  `configs/gcs/mavproxy_stage_2_4/mavinit.scr` лежит только безопасная
  настройка: aliases, `module load cmdlong`, `set shownoise false`,
  запрет forwarding для внешних sub-clients.
- `--cmd` допустим только для безопасной настройки. Flight sequence нельзя
  класть в `--cmd`, потому что команды могут выполниться до heartbeat.
- `--daemon` не используется в основном тесте: он отключает interactive shell.
- `--streamrate=10` включён, чтобы MAVProxy видел GPS/position/mode/arm
  telemetry.
- Если нужен внешний QGC, стандартный UDP port обычно `14550`, но Stage 2.4
  acceptance не должен подменяться внешним direct client.

## Ручные команды

В interactive режиме после появления MAVProxy CLI:

```text
status
mode GUIDED
arm throttle
takeoff 10
velocity 1 0 0
velocity 0 0 0
mode LAND
disarm
```

Для точного `GO TO` в локальных координатах Web UI использует модуль
`message`, потому что встроенные `velocity`/`position` команды `cmdlong`
работают в `MAV_FRAME_BODY_NED`. Ручной эквивалент:

```text
message SET_POSITION_TARGET_LOCAL_NED 0 1 0 1 3576 80 45 -10 0 0 0 0 0 0 0 0
```

## Acceptance events

Driver пишет `logs/<run_id>/events.jsonl` и `logs/<run_id>/report.md`.
В UI-режиме дополнительно пишется `operator_ui_manifest.json`.
Ключевые события headless smoke:

- `mavproxy_started`
- `gcs_connected`
- `heartbeat_visible`
- `gps_visible`
- `mode_visible`
- `arm_state_visible`
- `manual_command_sent`
- `manual_command_accepted`
- `guided_mode_ok`
- `arm_ok`
- `takeoff_detected`
- `position_or_velocity_command_ok`
- `landing_command_sent`
- `landed_or_disarmed`
- `scenario_success` / `scenario_failed`

Scenario получает `success` только если verified command path прошёл через
MAVProxy, `Mission upload used: false`, `Direct pymavlink command path used:
false`, и live команды изменили состояние/телеметрию vehicle.

Для видео порядок лучше такой: сначала Gazebo окно и Web GCS live управление,
затем `report.md`, `events.jsonl` и `ns3_events.jsonl` как доказательная
машинная фиксация увиденного поведения.

Для RF-видео лучше показать отдельный маршрут:

1. Запустить `scripts/run_stage_2_4_rf_demo.sh`.
2. Взлететь через `TAKEOFF`.
3. Нажать `NLOS PASS` или задать `GO TO N=80 E=45`.
4. На экране одновременно показать Gazebo с obstacle-сценой и Web GCS:
   препятствие на карте, красную LOS/NLOS-линию, падение RSSI и рост loss.
5. После прогона открыть `events.jsonl` и `ns3_events.jsonl`, где будут
   `rf_link_state` и `ns3:sionna_poll` события.
