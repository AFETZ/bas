# Stage 3 — ИССГР Multicast Sync (40/80-byte packets)

Compact UDP multicast протокол для синхронизации ИССГР БД между узлами.
Закрывает пункт ТЗ верхнего уровня:

> "Синхронизация БД: автоматический запуск синхронизации, уровни детализации,
> пакеты 40/80 байт, multicast при поддержке каналов связи"
> (`docs/Краткая_выдержка_актуального_из_гранта_БАС.docx`)

## Wire format

Все packets начинаются с 4-байтового header'а:

```
Offset Size  Field           Описание
0      2     sync_marker     0xBA5A (big-endian)
2      1     version         0x01
3      1     msg_type        0xFF=HEARTBEAT, 0x01=POSITION_L1, 0x02=SENSOR_L2
```

### L1 packet — 40 байт (HEARTBEAT + POSITION)

```
4      4     domain_hash     FNV-1a 32-bit от "domain:system"
8      4     object_hash     FNV-1a 32-bit от UUID
12     4     sequence        uint32 monotonic per object
16     4     timestamp_ms    low 32 bits эпохи ms
20     4     lat_e7          int32, latitude × 1e7 (стандарт MAVLink)
24     4     lon_e7          int32, longitude × 1e7
28     4     alt_cm          int32, altitude в см
32     2     heading_cdeg    int16, heading × 100
34     2     speed_cmps      int16, speed cm/s
36     1     state_flags     bit0=armed; bits4-7=battery_pct/16
37     1     issgr_class_top 0=geo, 1=ops, 2=func, 3=raster
38     2     crc16           CRC-16/CCITT-FALSE (poly 0x1021)
```

### L2 packet — 80 байт (SENSOR reading)

L1 header без CRC (38 байт) + 40-byte extension + CRC (2):

```
38     8     source_obj_hash64  FNV-1a 64-bit от UAV UUID-источника
46     4     sensor_type_hash   FNV-1a 32-bit от sensor_type string
50     8     sensor_value_f64   IEEE 754 double
58     4     ground_lat_e7      int32 (для CV detections — target ground)
62     4     ground_lon_e7      int32
66     4     confidence_e4      uint32, confidence × 10000
70     8     reserved
78     2     crc16
```

## Multicast параметры

| Параметр | Default | Изменение |
|---|---|---|
| Group | `239.10.10.10` | RFC 2365 admin-local scope |
| Port | `5500` | UDP |
| TTL | 1 | Local network only; site=8, regional=64 |
| Interface | `0.0.0.0` | Через `--interface=<ip>` |

## Уровни детализации (LoD)

| LoD | Packet | Когда |
|---|---|---|
| L0 | HEARTBEAT (40B) | Минимальное presence ping, lat/lon=0 |
| L1 | POSITION (40B) | UAV state — lat/lon/alt/heading/speed/armed |
| L2 | SENSOR (80B) | CV detection / RSSI с ground tag |

Publisher автоматически выбирает LoD по типу объекта.

## Архитектура two-node sync

```
┌────────────────────────────────────────────────────────────────────┐
│ Node A (source-of-truth)                Node B (АСУ replica)       │
│                                                                    │
│  ISSGR API :8770                         ISSGR API :8771            │
│    UAV pose updated  ──→                  empty initially          │
│         │                                       ▲                  │
│         ▼                                       │ REST POST        │
│  sync_publisher                          sync_subscriber           │
│  reads /digital_twin                     listens multicast         │
│  encodes → 40/80B                        decodes → upsert          │
│         │                                       ▲                  │
│         ▼ multicast group                      │                  │
│   239.10.10.10:5500 ───────────────────────────┘                  │
│   TTL=1, fire-and-forget                                          │
│                                                                    │
│   Auto-scheduling: 1 Hz (configurable)                            │
│   "При поддержке каналов связи": graceful — UDP без ACK,          │
│   subscriber делает sequence-gap detection + REST resync           │
│   из /digital_twin при потере >N packets                          │
└────────────────────────────────────────────────────────────────────┘
```

## Файлы

| Файл | Что |
|---|---|
| `orchestrator/src/orchestrator/issgr/sync.py` | Protocol + encoders/decoders + MulticastPublisher/Subscriber + AutoPublisher |
| `scripts/issgr_sync_publisher.py` | Standalone publisher — читает ИССГР REST, emit multicast |
| `scripts/issgr_sync_subscriber.py` | Standalone subscriber — receive, decode, optional POST в local ИССГР |
| `scripts/run_stage_3_issgr_sync_demo.sh` | Two-node demo (node-A → multicast → node-B) |
| `scripts/_sync_loopback_smoke.py` | Helper для CI smoke (one host) |
| `docs/stage_3_issgr_sync.md` | Этот файл |

## Запуск

### Two-node demo

```bash
sudo bash scripts/run_stage_3_issgr_sync_demo.sh
```

Поднимает:
- ISSGR node-A на :8770 с одним seed UAV (Iris-Sync-Demo, sysid=1)
- ISSGR node-B на :8771 (пустой)
- sync_publisher: node-A → multicast 239.10.10.10:5500 каждую секунду
- sync_subscriber: multicast → POST на node-B

После ~45с node-B имеет UAV state, синхронизированный через multicast.

### Standalone publisher/subscriber

```bash
# Terminal A (источник):
.venv/bin/python scripts/issgr_api_server.py --port 8770
.venv/bin/python scripts/issgr_sync_publisher.py --issgr-url http://127.0.0.1:8770

# Terminal B (replica на другой машине той же сети):
.venv/bin/python scripts/issgr_api_server.py --port 8771 --no-seed
.venv/bin/python scripts/issgr_sync_subscriber.py --issgr-url http://127.0.0.1:8771
```

### CI smoke (loopback)

```bash
.venv/bin/python scripts/_sync_loopback_smoke.py
```

Verified: 6/6 packets (1 HEARTBEAT + 3 L1 + 2 L2), все CRC OK, все fields
decoded правильно.

## Round-trip пример

```
Encode UAV(lat=-35.363, lon=149.165, alt=10м, hdg=90°, spd=2.5m/s, armed=True):
  40 bytes: ba5a 0101 ... CRC16

Decode → L1Packet(
  msg_type=0x01, object_hash=0xa66c23bd, sequence=42,
  lat_deg=-35.36300, lon_deg=149.16500, alt_m=10.0, heading_deg=90.0,
  speed_mps=2.50, armed=True, crc_ok=True
)

Encode SensorReading(class=CV:person, conf=0.87, ground=(-35.3636,149.1655)):
  80 bytes
Decode → L2Packet(
  base.msg_type=0x02, sensor_type_hash=0xcce2346c,
  sensor_value=0.870, ground_lat=-35.363600, ground_lon=149.165500,
  confidence=0.870
)
```

## Gap detection + resync

Subscriber отслеживает `last_seq` per `object_hash`. Если `new_seq >
prev_seq + 1`, logs WARNING и (опционально) тригерит REST GET
`/digital_twin` для full resync. Это даёт self-healing при packet loss
в multicast без overhead reliable transport (TCP).

## Безопасность

Multicast = no authentication, packets видны всем на сетевом сегменте.
Для production:

- Использовать **SSM** (Source-Specific Multicast) с фильтрацией по
  source IP
- Подписать packets HMAC-SHA256 в reserved field (~16 байт)
- Encrypt через AES-128-GCM (overhead ~16 байт IV+tag, packet станет
  56/96 байт)

Сейчас sync_publisher emit-ит без auth — этот pattern для trusted
network. Усиления безопасности — отдельный backlog.

## Pattern source

- [RFC 2365: Administratively Scoped IP Multicast](https://datatracker.ietf.org/doc/html/rfc2365)
- [RFC 1112: Host Extensions for IP Multicasting](https://datatracker.ietf.org/doc/html/rfc1112)
- [MAVLink GLOBAL_POSITION_INT](https://mavlink.io/en/messages/common.html#GLOBAL_POSITION_INT) — int32 lat/lon × 1e7 convention
- [CRC-16/CCITT-FALSE](https://reveng.sourceforge.io/crc-catalogue/16.htm) — poly 0x1021
- [FNV-1a hash](http://www.isthe.com/chongo/tech/comp/fnv/) — лёгкий deterministic string hash
