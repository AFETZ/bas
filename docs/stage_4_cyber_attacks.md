# Stage 4 — Cyber attack simulator + defense monitor

Закрывает пункт ТЗ "Опционально — кибератаки" (зона Маргаряна А.Г.)
как **defensive research** simulator: воспроизводит 3 типа атак внутри
изолированной BAS simulation environment + companion monitor который
detects их в real time.

## Scope

**ВАЖНО:** Модуль предназначен **только для defensive research**:
testing detection и mitigation strategies против известных attack
vectors. Networking ограничен loopback / RFC1918 private ranges
(safety guard в `run_gps_spoof` / `run_cmd_inject`). Sionna jamming
touches только локальный `/tmp` file.

## Attack vectors

| Attack | Vector | Effect | Detection signature |
|---|---|---|---|
| **GPS spoofing** | Inject fake `GLOBAL_POSITION_INT` MAVLink frames в target UDP endpoint, impersonating UAV sysid | UAV EKF может accept чужой GPS source если no sender authentication | Sudden lat/lon delta > threshold (default 100m) за < window (default 1s) per sysid |
| **MAVLink command injection** | Inject `COMMAND_LONG` frames с unauthorized source sysid в target endpoint | UAV может execute commands (SET_MODE / DO_REPOSITION / NAV_TAKEOFF) от attacker | Source sysid не в whitelist `authorized_gcs_sysids` (default {255, 252}) |
| **RF jamming** | Overwrite `/tmp/sionna_channel.json` с sustained high-loss (0.95) + high-delay (500ms) + low RSSI (-110 dBm) | Эмулирует broadband jammer; ns-3 / orchestrator видят degraded channel | Sustained `rssi_dbm < threshold` (default -90 dBm) за N consecutive polls; `extra_delay_ms > 200` |

## Файлы

| Файл | Что |
|---|---|
| `scripts/cyber_attack_simulator.py` | 3 attack runners + CLI (`gps_spoof`/`cmd_inject`/`rf_jam`) с safety guards |
| `scripts/cyber_defense_monitor.py` | `DefenseMonitor` class + CLI; subscribes на MAVLink endpoint + polls channel file; emits NDJSON alerts |
| `scripts/_cyber_smoke.py` | Round-trip smoke: монитор + последовательно все 3 атаки + verify alerts |
| `docs/stage_4_cyber_attacks.md` | Этот файл |

## Запуск

### Defense monitor (постоянно работает)

```bash
./scripts/cyber_defense_monitor.py \
    --mavlink-host 127.0.0.1 --mavlink-port 14550 \
    --channel-file /tmp/sionna_channel.json \
    --log-file logs/defense_alerts.jsonl \
    --auth-gcs 255 --auth-gcs 252
```

NDJSON output:

```
{"ts": 1779638732.5, "kind": "gps_spoof", "severity": "critical",
 "detail": "sysid=1 jump 248m в 200ms (prev=(-35.36326,149.16523) → ...)",
 "extra": {"sysid": 1, "dist_m": 248.7, "dt_ms": 199.9, ...}}
```

### Запуск атак (по отдельности)

```bash
# GPS spoofing — instant teleport на 200м N/E:
./scripts/cyber_attack_simulator.py gps_spoof \
    --target-port 14550 --sysid 1 \
    --offset-lat-m 200 --offset-lon-m 200 \
    --duration-s 5

# Command injection — fake sysid=254 → SET_MODE(LAND):
./scripts/cyber_attack_simulator.py cmd_inject \
    --target-port 14550 --fake-sysid 254 \
    --command 176 --p1 1 --p2 9 --n 3

# RF jamming — high-loss channel for 5s:
./scripts/cyber_attack_simulator.py rf_jam \
    --channel-file /tmp/sionna_channel.json \
    --duration-s 5 --loss-ratio 0.95 --rssi-dbm -110
```

### CI smoke

```bash
./scripts/_cyber_smoke.py
```

Verified output:

```
==> defense monitor up on :17550, polling /tmp/_smoke_chan.json
[1] Send baseline GLOBAL_POSITION_INT (sysid=1, normal position)
    alerts after baseline: 0 (expected 0)
[2] Run gps_spoof (sysid=1, offset +200m N/E for 2s)
    gps_spoof alerts: 1
[3] Run cmd_inject (fake sysid=254, COMMAND_LONG → SET_MODE)
    cmd_injection alerts: 3
[4] Run rf_jam (loss=0.95, rssi=-110 dBm for 1s)
    rf_jamming alerts: 16   (1 CRITICAL + 10 WARN delay)

=== Total alerts: 20 ===
ALL CHECKS PASSED
```

## Wire format details

### GPS spoof — MAVLink GLOBAL_POSITION_INT (msgid=33)

Payload (28 bytes):

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 4 | time_boot_ms | uint32 |
| 4 | 4 | lat | int32, deg × 1e7 |
| 8 | 4 | lon | int32, deg × 1e7 |
| 12 | 4 | alt | int32, mm above MSL |
| 16 | 4 | rel_alt | int32, mm relative |
| 20 | 2 | vx | int16, cm/s |
| 22 | 2 | vy | int16, cm/s |
| 24 | 2 | vz | int16, cm/s |
| 26 | 2 | hdg | uint16, cdeg |

### Command injection — MAVLink COMMAND_LONG (msgid=76)

Payload (33 bytes):

| Offset | Size | Field |
|---|---|---|
| 0..27 | 7×4 | param1..param7 (float) |
| 28 | 2 | command (uint16) |
| 30 | 1 | target_system |
| 31 | 1 | target_component |
| 32 | 1 | confirmation |

Common commands для spoofing:
- `176 (DO_SET_MODE)` + p1=1, p2=9 → switch to LAND
- `22 (NAV_TAKEOFF)` + p7=alt
- `400 (DO_FLIGHTTERMINATION)` → emergency kill

### RF jam — `/tmp/sionna_channel.json` overwrite

```json
{
  "loss_ratio": 0.95,
  "extra_delay_ms": 500.0,
  "rssi_dbm": -110.0,
  "ts": 1779638732.123,
  "source": "cyber_attack_simulator/rf_jam"
}
```

Ns-3 polling процесс читает этот файл и применяет `loss_ratio` к
`RateErrorModel`, что эмулирует jammer-induced packet loss на
control + payload каналах (если `--sionnaTargetFlow=both` per Stage 2.1.e).

## Defense detection algorithms

### GPS spoof — position jump

Хранит per-sysid `(lat, lon, alt, ts)` last reading. На каждый new
GLOBAL_POSITION_INT:

```python
prev = self._last_position.get(sysid)
if prev and (now - prev_ts) * 1000 <= jump_window_ms:
    dist = haversine_m(prev_lat, prev_lon, new_lat, new_lon)
    if dist > position_jump_threshold_m:
        emit_alert("gps_spoof", critical, ...)
```

### Cmd injection — sysid whitelist

```python
if msgid == COMMAND_LONG and sysid not in authorized_gcs_sysids:
    emit_alert("cmd_injection", critical, ...)
```

### RF jam — sliding window RSSI

```python
if rssi_dbm < jam_rssi_threshold_dbm:
    consecutive_low_rssi += 1
    if consecutive_low_rssi >= jam_consecutive_polls:
        emit_alert("rf_jamming", critical, ...)
else:
    consecutive_low_rssi = 0
```

## Production mitigation hints

Detection — первый шаг; реальные mitigations (вне scope текущей задачи):

| Attack | Mitigation |
|---|---|
| GPS spoof | Cross-check GPS с visual odometry / SLAM; INS dead-reckoning при jump |
| Cmd injection | MAVLink message signing (MAVLink 2 signature feature); auth-token preshared key |
| RF jam | Frequency hopping; multiple radios; LoS detection через RSSI gradient |

## Pattern source

- [MAVLink v2 packet format](https://mavlink.io/en/guide/serialization.html)
- [MAVLink common dialect](https://mavlink.io/en/messages/common.html) — message IDs
- [MAVLink signing](https://mavlink.io/en/guide/message_signing.html) — mitigation reference
- [GPS spoofing literature](https://www.gps.gov/governance/advisory/meetings/2017-12/humphreys.pdf) — UT Austin / Humphreys 2017
