# Stage 4 — MAVLink ↔ Gazebo / AirSim sim router

Прозрачный UDP fanout router для MAVLink. Закрывает пункт ТЗ
"MAVLink ↔ Gazebo / AirSim" (исполнительная зона **Федотенкова А.А.**)
как референсный артефакт без зависимостей на C++ mavlink-router.

> Существующий `mavbridge` (socat-based UDP↔TCP) Stage 1.7 решает
> point-to-point bridging для одного PTY. Этот Stage 4 router —
> **1-to-N fanout** для подключения нескольких consumer'ов одного
> MAVLink stream'а одновременно: GCS (MAVProxy/QGC), Gazebo plugin,
> AirSim adapter, capture file.

## Архитектура

```
ArduPilot SITL  ┐
(udpin:14550)   │
                ▼
        ┌─────────────────────────────┐
        │  mavlink_sim_router         │
        │  - bind на source           │
        │  - parse v1/v2 frame length │
        │  - re-sync на invalid STX   │
        │  - fanout 1→N transparently │
        │  - msgid stats (top-5)      │
        └─────────────────────────────┘
              │      │      │      │
              ▼      ▼      ▼      ▼
            14551  14552  14553  file
            GCS    Gazebo AirSim capture
            MAVP   plugin adapter (binary)
```

## Wire transparency

Router **не парсит** MAVLink payload. Он определяет границы frame'ов
по first byte:

| Byte | Магия | Frame length |
|---|---|---|
| `0xFE` | MAVLink v1 STX | `8 + LEN[1]` |
| `0xFD` | MAVLink v2 STX | `12 + LEN[1] + (13 если INCOMPAT[2] & 0x01)` |
| другое | invalid | skip 1 byte, increment `n_resync` |

Это даёт correct parsing v1 и v2 одновременно, signed/unsigned v2
frames, любой dialect (common, ardupilot, ualberta, etc.) без
кодогенерации.

## Sink типы

| Spec | Класс | Описание |
|---|---|---|
| `udpout:HOST:PORT` | `UdpOutSink` | Один UDP socket на каждый sink; sendto без блокировки |
| `file:PATH` | `FileSink` | Append raw frames в binary file (для post-mortem mavlogdump) |

Каждый sink независимый — failure одного (socket error, disk full) не
влияет на остальные. Router считает per-sink `n_sent` / `n_written`
для observability в stats.

## Запуск

### Standalone fanout

```bash
./scripts/mavlink_sim_router.py \
    --source udpin:127.0.0.1:14550 \
    --sink udpout:127.0.0.1:14551 \
    --sink udpout:127.0.0.1:14552 \
    --sink udpout:127.0.0.1:14553 \
    --sink file:/tmp/capture.mav \
    --stats-every-s 5
```

Stats каждые 5с:

```
[router] frames_in=243  bytes_in=8412  resync=0  top: id=0:42 id=33:38 id=30:32 ...
  → udpout:127.0.0.1:14551  (sent=243)
  → udpout:127.0.0.1:14552  (sent=243)
  → udpout:127.0.0.1:14553  (sent=243)
  → file:/tmp/capture.mav  (written=243)
```

`top` показывает топ-5 msgid с counts — позволяет быстро увидеть нет
ли flood'а одного типа сообщений или пропадания heartbeat (id=0).

### Через wrapper

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh router
```

Запускает router на 30с с canonical 4-sink config.

### Совместная работа с SITL + AirSim

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh full
```

Поднимает всю цепочку:

| Компонент | Endpoint / роль |
|---|---|
| `airsim_stub_server` | :41451 (msgpack-rpc, no-op AirSim) |
| `mavlink_sim_router` | :14550 → :14551/14552/14553/file |
| `arducopter_airsim_interface --mode=mavlink_mirror` | :14553 → AirSim simSetVehiclePose |
| ArduPilot SITL (опц.) | -v ArduCopter → :14550 (если `SIM_VEHICLE` валиден) |

## Файлы

| Файл | Что |
|---|---|
| `scripts/mavlink_sim_router.py` | Router class + sink абстракции + CLI |
| `scripts/_mavlink_sim_router_smoke.py` | CI smoke: 25 v2 HEARTBEAT через router, verify N=N×count + bytes |
| `scripts/run_stage_4_sim_bridges_demo.sh` | Demo wrapper |
| `docs/stage_4_mavlink_sim_router.md` | Этот файл |

## Verified

CI smoke pass:

```
Sent: 25 frames × 21B = 525B
  Sink A (udp:17551): count=25  bytes=525
  Sink B (udp:17552): count=25  bytes=525
  Sink F (file): bytes=525
  Router state:  frames_in=25  bytes_in=525
ALL CHECKS PASSED
```

Все 3 sink (2 UDP + 1 file) получили identical 25 × 21B v2 HEARTBEAT
frames без data loss и без resync events.

## Сравнение с альтернативами

| Решение | Языки | Pros | Cons |
|---|---|---|---|
| **mavlink-router** (intel/mavlink-router C++) | C++17 | High-perf, autoroute by sysid, TCP/UDP/UART | Build dependency, requires CMake, бинарь нужен на каждый target |
| **MAVProxy --out** | Python | Уже установлен, parser ready | Single-process, parses all payloads (CPU overhead на ~5Hz HEARTBEAT — fine; на 50Hz ATTITUDE — заметно), interactive shell может зависнуть |
| **socat UDP forking** | C | Однострочник | Не парсит frames — может разорвать MAVLink message на 2 packets если MTU маленький |
| **наш `mavlink_sim_router.py`** | Python (stdlib only) | Лёгкий, transparent, без depend, parses frames boundaries | Меньше пиковая производительность (Python sock loop ~5k frames/s vs 50k+ в C++) |

Для prototype среды pure-Python вариант покрывает все use case, для
production deployment рекомендуется mavlink-router.

## Pattern source

- ArduPilot [MAVLink dialect XML](https://github.com/ArduPilot/mavlink) — message IDs
- MAVLink [v2 frame format](https://mavlink.io/en/guide/serialization.html#packet_format)
- intel/mavlink-router design — fanout pattern reference
