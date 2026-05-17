# Этап 1.7 — LoRa Serial Bridge (буквальная реализация ТЗ)

ТЗ Физулина: «Интерфейс каналов связи, которые надо эмулировать использует
стандарты **LoRa (через Serial Port)** / LoRaWan, WiFi (TCP/IP)».

В этапе 1 LoRa моделировалась как **IP-канал** с lossy profile через ns-3
TapBridge (`degraded_lora`). Это функциональный эквивалент, но не буква ТЗ:
реальная LoRa — это **serial modem** (SX1276/RFD900/SiK), MAVLink идёт
**байтстримом через UART**, не через IP-stack.

## Архитектурный принцип

Используем **готовые валидированные решения community**, не пишем эмуляторы
с нуля. Конкретные компоненты:

| Слой | Готовое решение | Зачем именно оно |
|---|---|---|
| LoRa PHY+MAC simulation | **ns-3 lorawan module** ([signetlabdei/lorawan](https://github.com/signetlabdei/lorawan)) | Università di Padova, цитируется в 50+ paper'ах, валидирован против реальных SX1276/RFM95. Поддерживает SF7-12, BW 125/250/500 kHz, CR 4/5-4/8, ADR, retransmissions. Это **the** academic standard для LoRaWAN моделирования |
| MAVLink ↔ serial bridge | **mavlink-router** ([mavlink-router/mavlink-router](https://github.com/mavlink-router/mavlink-router)) | Mature и поддерживается. Multi-endpoint MAVLink demuxer (UDP/TCP/serial), отвечает за reassembly + filtering. Используется в реальных дронах (Cube Orange + companion computer) |
| Альтернатива (если mavlink-router недоступен) | **MAVProxy** ([ArduPilot/MAVProxy](https://github.com/ArduPilot/MAVProxy)) | официальный ArduPilot GCS, поддерживает serial endpoint |
| PTY ↔ TCP/UDP bridge на уровне OS | **socat** | стандартная утилита Linux, у нас уже работает для UDP↔TCP mavbridge |
| MAVLink reader в orchestrator | **pymavlink.mavutil.mavlink_connection** | уже используется, поддерживает serial endpoint из коробки: `mavlink_connection("/dev/ttyUSB0", baud=57600)` |

## Definition of Done

1. **ns-3 lorawan module** интегрирован в наш ns-3 build:
   - Скачан из `signetlabdei/lorawan`, скопирован в `ns3/contrib/lorawan/`
   - `bas/ns3:dev` Dockerfile собирает lorawan вместе с mainline ns-3
   - Smoke: `./ns3 run lorawan-example` запускается без ошибок
2. **Новый ns-3 scenario** `ns3/scenarios/lora_serial.cc`:
   - Использует `LoraNetDevice` + `LoraChannelHelper` + `LoraPhyHelper`
     из модуля lorawan
   - 1 LoRa endpoint (GCS) + 1 LoRa endpoint (UAV), peer-to-peer
   - Не выходит на IP-уровень — работает на data-link слое (MAC payload)
   - На каждый MAC payload эмитит JSONL event с byte-count, drop reason,
     air time (computed by Lora PHY simulation)
3. **MAVLink↔LoRa bridge**: mavlink-router (или MAVProxy) запущен в новом
   контейнере `bas-lora-router`. Один endpoint — UDP 14550 (от mavbridge
   SITL), второй endpoint — PTY device, которое монтировано в bas-uav
   netns. ns-3 lorawan читает байты с PTY, симулирует LoRa передачу,
   выдаёт байты на другой PTY (для GCS).
4. **Orchestrator подключается через serial endpoint**:
   `--mavlink-endpoint=serial:/tmp/ptyGCS_lora:57600`. pymavlink handles
   serial natively.
5. **Mission AUTO landed=True** на профиле `lora_serial_sf7_bw125`
   (SF=7, BW=125 kHz, CR=4/5 → ~5.5 kbps). Видны realistic LoRa-метрики:
   air time на пакет, frame_drop_rate из BER, throughput goodput.
6. **Сравнительный отчёт** `wifi_good` vs `lora_serial_sf7` через
   `bas-analyzer-compare` — видны ожидаемые различия в задержке и
   throughput.

## Архитектура (с ns-3 lorawan)

```
SITL (TCP 5760)                                            orchestrator
        │                                                         │
        ▼                                                         ▼
   bas-mavbridge   ◄── UDP MAVLink 14550 ──►   bas-lora-router  --master=serial:/tmp/ptyGCS_lora,57600
   socat UDP↔TCP                                mavlink-router
                                                       │
                                                       ▼
                                                /tmp/ptySITL_lora
                                                       │
                                                       ▼
                                       ns-3 LoRa simulation
                                       (LoraNetDevice ↔ LoraChannel ↔ LoraNetDevice)
                                                       │
                                                       ▼
                                                /tmp/ptyGCS_lora
```

Ключевая идея: **ns-3 lorawan module как реальный LoRa simulator**, не
наши собственные эмуляции. MAVLink-байты пробрасываются через PTY в
ns-3, проходят через LoRa PHY+MAC simulation (правильные параметры
SF/BW/CR, BER из community-валидированных моделей), выходят на другой
PTY.

## Под-этапы

### 1.7.a — ns-3 lorawan module install и smoke (~2-3 дня)

| Артефакт | Что |
|---|---|
| `docker/ns3/Dockerfile` (модификация) | Добавить шаг: `git clone -b release signetlabdei/lorawan ns-3.40/contrib/lorawan`, затем `cmake --reconfigure` |
| `ns3/scenarios/lorawan_smoke.cc` (новый) | Минимальный пример: 1 endDevice + 1 gateway + LoraChannel, отправить 10 пакетов, проверить что приходят |
| Smoke run | `docker run bas/ns3:dev /work/ns3-src/build/scratch/lorawan_smoke` |

Acceptance: 10/10 LoRa-пакетов доставляются с правильным air time для
заданного SF.

### 1.7.b — ns-3 lorawan scenario для нашего канала (~3-4 дня)

| Артефакт | Что |
|---|---|
| `ns3/scenarios/lora_serial.cc` | Сценарий с 2 LoRa endpoints (GCS, UAV) + LoraChannelHelper. Каждый endpoint имеет PtyTap (читает байты с /tmp/pty...) + LoraMacLayer (фрагментирует MAVLink на LoRa MAC frames) |
| `ns3/scenarios/pty_lora_bridge.{h,cc}` | Helper class: открывает PTY через `posix_openpt` + `grantpt` + `unlockpt`, читает байты в ns-3 callback'е, инжектит как `LoraMacLayer::SendPacket()` |
| CommandLine флаги | `--ptySitlPath=/tmp/ptySITL_lora`, `--ptyGcsPath=/tmp/ptyGCS_lora`, `--sf=7`, `--bw=125000`, `--cr=4/5`, `--txPower=14` |

Acceptance: байты, записанные в `/tmp/ptySITL_lora`, появляются в
`/tmp/ptyGCS_lora` с правильным air time delay (по LoRa formula).

### 1.7.c — mavlink-router integration (~2 дня)

| Артефакт | Что |
|---|---|
| `docker/lora-router/Dockerfile` (новый) | apt install mavlink-router (или build from source: ~5 мин. на сборку cmake) |
| `docker-compose.shared-netns.yml` (модификация) | Сервис `lora-router` с `network_mode: container:bas-uav-net`, mount /tmp:/host_tmp |
| `configs/lora_router.conf` | mavlink-router config: master=tcp:127.0.0.1:5760 → endpoint=serial:/tmp/ptySITL_lora,57600 |

Acceptance: запущенный mavlink-router пробрасывает MAVLink heartbeat
от SITL → /tmp/ptySITL_lora (видно через `cat /tmp/ptySITL_lora | xxd`).

### 1.7.d — Orchestrator serial endpoint (~1 день)

`orchestrator/src/orchestrator/run.py`: parse endpoint string. Если
начинается с `serial:`, конвертировать в pymavlink call:
```python
if endpoint.startswith("serial:"):
    path, baud = endpoint[7:].rsplit(":", 1)
    return mavutil.mavlink_connection(path, baud=int(baud))
```

pymavlink делает остальное.

### 1.7.e — Profile `lora_serial_sf7_bw125.yaml` (~0.5 дня)

```yaml
profile_id: lora_serial_sf7_bw125
transport: ns3_lorawan_pty
link_type: lora_modem_sf7_bw125
description: |
  LoRa modem emulation через ns-3 lorawan module (community-валидированный
  signetlabdei/lorawan port). Параметры: SF=7, BW=125 kHz, CR=4/5, TxPower=14 dBm.
  Эффективная throughput ~5.5 kbps. Bridge между MAVLink (через
  mavlink-router) и ns-3 lorawan через PTY pair.

parameters:
  ns3_lorawan_module: "signetlabdei/lorawan"
  sf: 7
  bandwidth_hz: 125000
  coding_rate: "4/5"
  tx_power_dbm: 14
  pty_sitl: "/tmp/ptySITL_lora"
  pty_gcs:  "/tmp/ptyGCS_lora"
  mavlink_router_config: "configs/lora_router.conf"

log_fields:
  - sf
  - bandwidth_hz
  - coding_rate
  - tx_power_dbm
```

### 1.7.f — Analyzer LoRa metrics (~1-2 дня)

LoRa-специфические метрики из `ns3/scenarios/lora_serial.cc` JSONL output:
- `air_time_ms` per packet (computed by LoRa formula)
- `frames_total`, `frames_lost` (CRC fail)
- `effective_throughput_bps`
- `mean_sf_used` (если ADR включён)

| Артефакт | Что |
|---|---|
| `analyzer/src/analyzer/metrics.py` | `@dataclass LoraSerialMetrics` |
| `analyzer/src/analyzer/report.py` | Секция «LoRa serial канал» |
| `compare.py` | Колонки в comparison.md |

### 1.7.g — Acceptance run (~1-2 дня)

| Артефакт | Что |
|---|---|
| `scripts/run_stage_1_7_lora_serial.sh` | Полный pipeline: ns-3 lorawan compile → PTY setup → ns-3 scenario start → mavlink-router → SITL → orchestrator (serial endpoint) → mission AUTO → analyzer |
| Acceptance | Mission landed=True (или документируем что SF=7 BW=125 kHz throughput слишком низкий для MAVLink mission upload — тогда переходим на SF=7 BW=500 kHz) |

## Open questions

1. **ns-3 lorawan module compatibility**: `signetlabdei/lorawan` есть в
   ветках для ns-3.36, 3.39, 3.40. Уточнить какая версия наша (`bas/ns3:dev`
   — ns-3.40). Если нет совместимой ветки — port (мин. правки).
2. **PTY в Docker контейнерах**: контейнер не может создавать PTY на host'е
   напрямую. Решение: `socat` на host'е создаёт PTY-пару, mount /tmp в
   docker.
3. **MAVLink mission upload throughput**: при SF=7 BW=125 kHz effective
   ~5.5 kbps. MAVLink mission item ~64 байт × 7 waypoints = 450 байт.
   Air time ≈ 0.65 секунд per packet × 7 + overhead ≈ 5 секунд. Это в
   пределах SITL mission timeout (~10s). Если не успевает — `--sf=7
   --bw=500000` ускоряет в 4x.
4. **mavlink-router vs MAVProxy**: оба умеют serial endpoint. Сначала
   пробуем mavlink-router (более производительный, dedicated tool); если
   build/install проблемы — fallback на MAVProxy (Python, всегда работает).

## Acceptance criteria (полный 1.7)

1. ns-3 lorawan module скомпилирован и работает в нашем образе `bas/ns3:dev`.
2. `sudo bash scripts/run_stage_1_7_lora_serial.sh sf7_bw125` — mission
   landed=True через LoRa-эмулированный канал.
3. report.md содержит секцию «LoRa serial канал» с метриками: air_time,
   throughput, frame_drop_rate.
4. Сравнительный отчёт wifi_good vs lora_serial показывает разницу в
   задержке (LoRa air time vs WiFi delay) и throughput.
5. Документация обновлена: 1.7 готов в architecture/roadmap/tz_compliance.
6. Tag `v2.1-lora-serial` на main.

## Сроки

| Под-этап | Оценка |
|---|---|
| 1.7.a ns-3 lorawan install + smoke | 2-3 дня |
| 1.7.b lora_serial.cc + pty bridge | 3-4 дня |
| 1.7.c mavlink-router setup | 2 дня |
| 1.7.d orchestrator serial endpoint | 1 день |
| 1.7.e+f profile + metrics | 1-2 дня |
| 1.7.g acceptance + docs | 1-2 дня |
| **Итого** | **2-3 недели** |
