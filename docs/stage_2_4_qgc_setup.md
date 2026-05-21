# Stage 2.4 — QGroundControl bridge

Опциональный режим Stage 2.4 где QGroundControl на Windows подключается к
тому же SITL что и наш Web GCS / MAVProxy, через мост `bluenviron/mavp2p`
вместо стандартного `mavbridge` (socat 1↔1).

## Архитектура

```
┌─────────────────┐                  ┌──────────────────────────────┐
│   QGC (Win)     │                  │  Web GCS UI (browser)        │
└────────┬────────┘                  └──────────────┬───────────────┘
         │ UDP 14560                                 │ HTTP 8765
         ▼                                            ▼
┌────────────────────────────────────────────────────────────────────┐
│ host netns                                                         │
│   socat UDP4-LISTEN:14560 → UDP4:10.10.0.2:14560                  │
│   gcs_web_ui_server.py (Python http.server)                        │
└────────┬────────────────────────────┬──────────────────────────────┘
         │ UDP 10.10.0.2:14560        │ ns-3 control channel
         ▼                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│ bas-uav netns                                                       │
│   mavp2p                                                            │
│     tcpc:127.0.0.1:5760   ← один TCP client к SITL (single-client)  │
│     udps:0.0.0.0:14550    ← UDP server для MAVProxy через ns-3      │
│     udps:0.0.0.0:14560    ← UDP server для QGC host relay           │
│                                                                     │
│   SITL ArduCopter (TCP 5760)                                        │
└─────────────────────────────────────────────────────────────────────┘
```

QGC и MAVProxy получают одновременный MAVLink-стрим от SITL. mavp2p
дедуплицирует heartbeat'ы по sysid и forward'ит команды обратно к SITL.

## Запуск

```bash
sudo bash scripts/run_stage_2_4_qgc_demo.sh
```

В консоли появится блок:

```
==========================================================================
 QGroundControl bridge готов
--------------------------------------------------------------------------
 В QGC: Application Settings → Comm Links → Add → UDP
   * Name:        BAS-WSL
   * Port:        14560
   * Server addr: 172.28.x.y   (WSL eth0)
 Затем Connect — heartbeat появится сразу после старта SITL.
==========================================================================
```

## Подключение QGC на Windows

1. Установить QGroundControl: <https://qgroundcontrol.com/downloads/>
2. Запустить QGC. По умолчанию он слушает UDP 14550 на хосте и сам ловит
   broadcast — нам это не подходит, делаем явную конфигурацию.
3. **Application Settings → Comm Links → Add**:
   - Type: `UDP`
   - Name: `BAS-WSL`
   - Port: `14560`
   - Server Addresses: добавить IP из консоли (WSL eth0)
   - Snimi флаг "automatically connect on start" если нужно
4. **Connect** на добавленный link. Heartbeat появится в течение 1–2 с.
5. На главном экране QGC увидите:
   - Артoгоризонт + GPS координаты SITL (Canberra-default)
   - Mode: `STABILIZE` (или текущий)
   - Arm/Disarm кнопки работают

## Совместная работа с Web GCS

Когда оператор работает в **Web GCS** (TAKEOFF, WASD, GO TO, FPV) — все
команды идут через MAVProxy в bas-ctrl-far netns → ns-3 control channel →
mavp2p → SITL. **QGC одновременно** видит те же события и может слать
свои команды (например ARM/DISARM из QGC меню).

Конфликта команд нет — MAVLink использует sysid/compid различение.
Acceptance-цепочка (MAVProxy stdin) остаётся неизменной, QGC лишь
наблюдатель + дополнительный input.

## WSL2 networking notes

| Windows / WSL2 версия | Как QGC видит SITL |
|---|---|
| **WSL2 mirrored networking** (Windows 11 22H2+ build 22621, опция `[wsl2] networkingMode=mirrored`) | QGC подключается на `localhost:14560` — Windows и WSL share network namespace |
| **WSL2 NAT (default)** | Нужен **WSL eth0 IP**: смотри его в выводе скрипта или `wsl hostname -I`. QGC: `Server addr = <WSL IP>`, `Port = 14560` |
| **Старые сборки Windows 10** | Аналогично NAT-режиму |

Включить mirrored networking (опционально, проще для QGC):

```ini
# %USERPROFILE%\.wslconfig
[wsl2]
networkingMode=mirrored
```

Затем `wsl --shutdown` и перезапуск WSL.

## Troubleshooting

### QGC не видит heartbeat

1. Проверь что mavp2p в bas-uav netns слушает:
   ```bash
   sudo ip netns exec bas-uav ss -uln | grep 14560
   ```
2. Проверь host-side socat:
   ```bash
   ss -uln | grep 14560
   ```
3. Telnet/nc тест из host:
   ```bash
   nc -u 10.10.0.2 14560 < /dev/null
   ```
4. SITL TCP 5760:
   ```bash
   sudo ip netns exec bas-uav ss -tln | grep 5760
   ```

### "Address already in use" при перезапуске

Старый socat остался жив. Cleanup:

```bash
sudo pkill -f "socat.*14560"
rm -f /tmp/bas_qgc_socat.pid
```

При следующем запуске `start_qgc_host_relay` сам выпиливает stale processes.

### SITL не отвечает на QGC команды

Возможно SITL TCP 5760 занят старым mavbridge. Wrapper должен был не
запускать mavbridge при `BAS_GCS_QGC=1`. Проверь:

```bash
sudo sg docker -c "docker ps | grep -E 'mavbridge|mavrouter'"
```

Должен быть только `bas-mavrouter`, без `bas-mavbridge`.

## Acceptance vs. демо

QGC bridge — это **демо-режим**, не acceptance клиент. Stage 2.4 acceptance
закрыт через MAVProxy stdin (см. `docs/stage_2_4_manual_gcs.md`); QGC
добавлен как удобный визуальный GUI для просмотра телеметрии и тестов
ручного управления параллельно с Web GCS.

## Источники паттерна

- [ardupilot-sitl-docker (uxduck)](https://github.com/uxduck/ardupilot-sitl-docker) — `--mavproxy-args="--out udp:host.docker.internal:14550"`
- [mavlink-router (Intel)](https://github.com/mavlink-router/mavlink-router) — multi-endpoint MAVLink router
- [mavp2p (bluenviron)](https://github.com/bluenviron/mavp2p) — Go-based router, использован у нас
- [QGroundControl + MAVProxy UDP discussion (ArduPilot)](https://discuss.ardupilot.org/t/connect-qgroundcontrol-to-mavproxy-udp-port/120035)
