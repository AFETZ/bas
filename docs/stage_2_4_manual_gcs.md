# Stage 2.4 Manual GCS

## Цель

Stage 2.4 закрывает пункт ТЗ «ручное управление одним БАС через GCS».
Для headless-friendly стенда GCS считается MAVProxy command-line GCS:

```text
MAVProxy GCS -> ns-3 control channel -> mavbridge -> SITL
```

Acceptance-прогон должен показать не только heartbeat, а живую командную
цепочку в GUIDED режиме: `mode GUIDED`, `arm throttle`, `takeoff 10`,
live `position`/`velocity`/`guided` command, `mode LAND`, затем посадку
или disarm.

## Почему строго MAVProxy

Прямой `pymavlink` mini-GCS доказывает только MAVLink command path от
скрипта к SITL. Он не доказывает, что управление выполнялось через GCS/НПУ.
Поэтому для Stage 2.4 acceptance path команды управления отправляет только
MAVProxy CLI. `pymavlink` допустим отдельно как диагностический probe, но
не как основной acceptance path и не как источник flight-команд.

Mission upload для этого теста не используется: Stage 2.4 является live
manual/GCS command demo, а не автономным полётным заданием.

## Запуск

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

Альтернативно для movement-команды можно использовать `position N E D` из
модуля `cmdlong`, например:

```text
position 5 0 -10
```

## Acceptance events

Driver пишет `logs/<run_id>/events.jsonl` и `logs/<run_id>/report.md`.
Ключевые события:

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
