# Stage 4 — ArduPilot ↔ AirSim interface

Контрактный bridge между ArduPilot SITL и Cosys-AirSim. Закрывает пункт ТЗ
"ArduPilot ↔ AirSim interface" (исполнительная зона **Федотенкова А.А.**)
как архитектурный/референсный артефакт.

> Существующий `scripts/airsim_bridge.py` (Stage 2.2) форвардит pose из
> orchestrator `events.jsonl` → AirSim. Этот Stage 4 модуль работает
> **напрямую с ArduPilot SITL** (через MAVLink или JSON-FDM) и не зависит
> от orchestrator — то есть закрывает interface contract сам по себе.

## Два режима работы

| Режим | Транспорт | Когда применять |
|---|---|---|
| **mavlink_mirror** (default) | MAVLink (`udpin:127.0.0.1:14550`) | ArduPilot SITL уже запущен и эмиттит MAVLink, AirSim используется как **visual/sensor overlay**. Физику считает ArduPilot, AirSim — рендерит ту же pose. |
| **json_fdm** | UDP 9002/9003 (ArduPilot SIM_JSON pattern) | ArduPilot SITL запущен с `--model json:127.0.0.1`. Bridge закрывает physics loop: binary PWM in → internal X-config 6DOF dynamics → IMU/GPS/quaternion JSON out обратно SITL. AirSim подключается опционально как visual overlay. |

### Wire format — JSON-FDM (canonical ArduPilot pattern)

Это формат описанный в `libraries/SITL/SIM_AirSim.cpp` (ArduPilot
master). Bridge поднимает UDP listener на port 9003 и отправляет ответы
на 9002.

**SITL → bridge** для real ArduPilot (`servo_packet_16`, 40 bytes,
little-endian):

| Поле | Тип | Что |
|---|---|---|
| `magic` | `uint16` | `18458` |
| `frame_rate` | `uint16` | synthetic SITL rate |
| `frame_count` | `uint32` | synthetic clock tick |
| `pwm[16]` | `uint16[16]` | servo/motor outputs |

Synthetic smoke также поддерживает JSON-вариант того же payload:

```json
{
  "magic": 18458,
  "frame_rate": 1200,
  "frame_count": 12345,
  "pwm": [1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500,
          1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500]
}
```

`pwm` — 16 channels servo output (1000…2000μs PPM). На multicopter
первые 4 канала = motor outputs.

**bridge → SITL** (null-terminated compact JSON):

```json
{
  "timestamp": 12.345,
  "latitude": -35.363262,
  "longitude": 149.165237,
  "altitude": 584.0,
  "imu": {
    "gyro": [roll_rate, pitch_rate, yaw_rate],
    "accel_body": [ax, ay, az]
  },
  "position": [x_north, y_east, z_down],
  "quaternion": [qw, qx, qy, qz],
  "velocity": [vx, vy, vz]
}
```

Все units — SI (rad, rad/s, m, m/s²). Coordinate frame — NED (North-East-Down).

### Wire format — MAVLink mirror

Bridge подписывается на любой MAVLink endpoint (`udpin:HOST:PORT`),
читает только два message types:

| Message | Поле | Использование |
|---|---|---|
| `GLOBAL_POSITION_INT` | `lat`, `lon` (int×1e7), `alt` (mm) | → `Vector3r(x_north, y_east, z_down)` через haversine от `ORIGIN_LAT/LON` (CMAC default: -35.363262, 149.165237) |
| `ATTITUDE` | `yaw`, `pitch`, `roll` (rad) | → `Quaternionr` (Z-only yaw quaternion для simSetVehiclePose) |

Каждые ~100мс bridge вызывает `simSetVehiclePose(pose, true, vehicle_name)`
через msgpack-rpc к AirSim endpoint (default `127.0.0.1:41451`).

## Архитектура

```
┌──────────────────────────────────────────────────────────────────────┐
│ mavlink_mirror mode                                                  │
│                                                                      │
│   ArduPilot SITL ──── MAVLink udp:14550 ──── bridge                  │
│                                                  │                   │
│                                                  │ simSetVehiclePose │
│                                                  ▼                   │
│                                          AirSim :41451               │
│                                          (visual+sensor overlay)     │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ json_fdm mode (canonical closed-loop)                                │
│                                                                      │
│   ArduPilot SITL (--model json)        ┐                             │
│      writes servo_packet_16 binary     │ UDP :9003                   │
│      reads sensor JSON                 │                             │
│                                         ▼                            │
│                                       bridge                         │
│                                         │ ▲                          │
│                                         │ │ internal 6DOF dynamics   │
│                                         ▼ │                          │
│                                       AirSim :41451                  │
│                                       (optional visual overlay)      │
│                                         ▲                            │
│                                         │ sensor JSON (IMU/GPS/baro) │
│                                         │ UDP :9002 → SITL           │
│                                         └─────────────                │
└──────────────────────────────────────────────────────────────────────┘
```

## Файлы

| Файл | Что |
|---|---|
| `scripts/arducopter_airsim_interface.py` | `JsonFdmBridge` + `MavlinkMirrorBridge` + CLI |
| `scripts/multirotor_dynamics.py` | X-config 6DOF quadrotor dynamics + IMU noise/bias |
| `scripts/_arducopter_airsim_smoke.py` | CI smoke: JSON-FDM round-trip без real SITL |
| `scripts/_real_sitl_e2e_smoke.py` | Real ArduCopter `--model json` + ARM + RC takeoff verification |
| `scripts/run_stage_4_sim_bridges_demo.sh` | Demo wrapper (smoke / mirror / full modes) |
| `docs/stage_4_arducopter_airsim_interface.md` | Этот файл |

## Запуск

### Smoke (без SITL, без AirSim)

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh smoke
```

Проверяет:
- JSON-FDM round-trip: 340 PWM frames → 340 sensor responses
- Phase 1 motors-off удерживает ground contact (`z_down≈0`)
- Phase 2 PWM above hover даёт climb >2 м
- Phase 3 differential thrust даёт yaw rotation
- Все response packets содержат timestamp/imu/position/quaternion/velocity

### Real SITL e2e (wire + ARM + takeoff)

```bash
.venv/bin/python scripts/_real_sitl_e2e_smoke.py
```

Проверяет real `~/ardupilot/build/sitl/bin/arducopter --model json:127.0.0.1`:
binary `servo_packet_16` parsing, sensor JSON response, MAVLink `HEARTBEAT`,
valid `GLOBAL_POSITION_INT`, `STABILIZE → ARM`, RC throttle takeoff,
motor PWM > hover, relative altitude climb >0.5 м.

### Mirror mode (AirSim stub + bridge без SITL)

```bash
bash scripts/run_stage_4_sim_bridges_demo.sh mirror
```

Запускает airsim_stub_server на :41451 + mirror bridge подписан на
:14553. Без MAVLink traffic не будет pose updates; полезно для теста
RPC handshake к AirSim.

### Full demo (SITL + router + bridges + stub)

```bash
SIM_VEHICLE=~/ardupilot/Tools/autotest/sim_vehicle.py \
    bash scripts/run_stage_4_sim_bridges_demo.sh full
```

Поднимает:
- `airsim_stub_server` :41451
- `mavlink_sim_router` fanout 14550 → 14551/14552/14553/file
- `arducopter_airsim_interface --mode=mavlink_mirror` :14553 → AirSim
- ArduPilot SITL `-v ArduCopter` → :14550 (если sim_vehicle.py найден)

После 45с показывает stats router'а, bridge'а и pose log.

## Подключение к real Cosys-AirSim (Windows / отдельная Linux машина)

```bash
# SITL запущен на этом хосте, AirSim — на Windows side с UE5 editor:
./scripts/arducopter_airsim_interface.py \
    --mode=mavlink_mirror \
    --mavlink-endpoint=udpin:127.0.0.1:14550 \
    --airsim-host=192.168.1.50 \
    --airsim-port=41451 \
    --vehicle-name="" \
    --log-file=logs/airsim_mirror.jsonl
```

`vehicle_name` = `""` соответствует первому/default vehicle в
AirSim settings (`SimMode: Multirotor` + один Vehicle entry).

## Интеграция в Stage 2.2 / 2.4 demo

Этот bridge может быть запущен **дополнительно** к `airsim_bridge.py`
(который читает events.jsonl). Они не конфликтуют — оба вызывают
`simSetVehiclePose` на разных vehicle_name или последний wins. Для
production используйте один из них:

- `airsim_bridge.py` — если orchestrator уже эмиттит events;
- `arducopter_airsim_interface.py` — если хочется bridge без orchestrator
  зависимости (proof-of-concept для interface contract).

## Ограничения

1. **JSON-FDM mode**: реализована минимальная real-flight физика:
   X-frame motor mixer, forces/torques, quaternion integration,
   ground-contact IMU support force (`accel_body≈-g` on ground),
   IMU white noise + bias drift, and ArduPilot synthetic-clock timing
   from `servo_packet.frame_rate/frame_count`. Smoke
   `_real_sitl_e2e_smoke.py` verifies real SITL wire protocol,
   `STABILIZE → ARM`, RC throttle takeoff, motor PWM > hover, and
   relative altitude climb >0.5m. Для production-accurate flight всё
   ещё нужны richer aerodynamics (wind, ground effect, battery sag).

2. **MAVLink mirror**: pose forwarding throttled на ~10 Hz (хватает
   для visual overlay; для high-fidelity sensor simulation нужно >50 Hz
   и quaternion attitude, не только yaw).

3. **Origin lat/lon hardcoded** на CMAC default (-35.363262, 149.165237).
   Для другого scenario поменять в `arducopter_airsim_interface.py`.

## Pattern source

- ArduPilot [SIM_AirSim.cpp](https://github.com/ArduPilot/ardupilot/blob/master/libraries/SITL/SIM_AirSim.cpp) — JSON-FDM wire format
- Cosys-AirSim [API docs](https://cosys-lab.github.io/Cosys-AirSim/apis/) — msgpack-rpc methods
- MAVLink [GLOBAL_POSITION_INT](https://mavlink.io/en/messages/common.html#GLOBAL_POSITION_INT)
