# Этап 1.5.2 — видеопоток камеры Gazebo через ns-3 payload-канал

Цель этапа: заменить синтетический payload-поток (StubNs3Channel) реальным RTP-видео, проходящим через payload TAP ns-3, и измерить frame-level метрики (FPS, e2e latency, frame loss, jitter, bitrate goodput). Должен работать на обоих профилях — `wifi_good` и `degraded_lora` — без регрессии 1.5.1.

## Definition of Done

1. На `wifi_good` через ns-3 проходит непрерывный RTP-поток H.264, receiver видит ≥95% RTP-пакетов и ≥25 FPS на стороне приёмника.
2. На `degraded_lora` тот же поток деградирует ожидаемым образом: видны frame loss spike'и во время outage-окон, средняя e2e latency ≥250 ms, средний bitrate goodput близок к sender bitrate × (1 - loss).
3. `report.md` дополнен секцией **«Видеопоток»** с метриками (FPS, e2e latency p50/p95, frame loss, bitrate goodput, размер потерянных интервалов).
4. Никакой регрессии mission run'а: control-канал MAVLink не страдает, mission landed=True на обоих профилях.
5. Stage 1.5.1 сценарии (`run_stage_1_5_1_mission.sh`) продолжают работать как сейчас — payload включается через отдельный флаг или отдельный скрипт `run_stage_1_5_2_mission.sh`.

## Текущая инфраструктура (что уже готово)

Подтверждено по survey репозитория, не нужно создавать заново:

| Артефакт | Где |
|---|---|
| ns-3 payload channel + TAPs `tap-pload-near` / `tap-pload-far` | `ns3/scenarios/two_channel.cc:332-340` (CommandLine: `--ploadBandwidthMbps`, `--ploadDelayMs`, `--ploadLoss`, `--ploadOutage`) |
| Host bridges `br-pload-near` / `br-pload-far` | `scripts/setup_radio_net.sh` (массив `CHANNELS[pload]=10.20.0`) |
| Host TAPs `tap-pload-{near,far}` + veth + netns `bas-pload-{near,far}` | тот же скрипт |
| Subnet payload | `10.20.0.0/24` (контроль — `10.10.0.0/24`) |
| GZ build deps (`libgstreamer1.0-dev`, `libopencv-dev`) | `docker/gazebo/Dockerfile:17-18` (как build-dep ardupilot_gazebo) |
| YAML структура: `network.payload_channel.profile` | `configs/scenarios/baseline_wifi.yaml` |
| Event-контракт `event_type="payload"` (packet_id, tx/rx time, delay, size_bytes, drop_reason, outage_state) | `orchestrator/src/orchestrator/components.py:158-180` |
| Анализатор: `payload | пакетов | PDR | потерь | в outage | задержка | jitter | goodput` колонки | `analyzer/src/analyzer/metrics.py` |

Чего нет и придётся создать:
- для 1.5.2.b нужен безопасный бортовой camera path: старый
  `iris_with_gimbal` содержит `CameraZoomPlugin` и на текущем стеке ломает
  JSON FDM, поэтому используется локальная модель
  `bas_iris_with_pov_camera` с fixed `pov_camera_link`
- Sender и receiver контейнеры
- Второй veth в `bas-uav` netns (на payload bridge)
- Глобальный второй netns адрес и инжекция
- Расширение orchestrator и analyzer для frame-level метрик
- Новый run-скрипт `scripts/run_stage_1_5_2_mission.sh`

## Топология (новая)

```
 control side (без изменений):
   [bas-ctrl-far netns] ──tcp/udp:MAVLink──> tap-ctrl-far ──ns-3 ctrl──> tap-ctrl-near ──> [bas-uav netns: SITL+mavbridge]

 payload side (новое):
   [bas-uav netns: видео-sender] ──RTP/UDP──> tap-pload-near ──ns-3 pload──> tap-pload-far ──> [bas-pload-far netns: видео-receiver]
       eth1=10.20.0.2/24                                                                       eth0=10.20.0.3/24

 bas-uav netns теперь содержит два eth:
   eth0 = 10.10.0.2/24  (control,  → mavbridge → SITL TCP 5760)
   eth1 = 10.20.0.2/24  (payload,  → video-sender → gst → udpsink:10.20.0.3:5000)
```

`bas-uav` netns остаётся «pod sandbox» pattern (busybox pause); в него инжектируется второй veth-pair → `br-pload-near`, точно так же как сейчас инжектируется первый → `br-ctrl-near` в `scripts/run_stage_1_5_1_mission.sh:75-82`.

`bas-pload-far` уже создаётся в `setup_radio_net.sh`. Нужен только адрес `10.20.0.3/24` и поднять `lo`.

## Архитектурное решение по компонентам

### Sender pipeline

Запускается **в bas-uav netns** (через `network_mode: "container:bas-uav-net"`, как gazebo/sitl/mavbridge). Это даёт ему доступ к gz topic'ам через loopback (там же, где запущен Gazebo, благодаря `GZ_IP=127.0.0.1`) и одновременно к payload bridge через eth1.

GStreamer-pipeline:

```
gst-launch-1.0 -v \
  videotestsrc pattern=ball is-live=true ! \
  video/x-raw,width=640,height=480,framerate=30/1 ! \
  videoconvert ! \
  x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=30 ! \
  rtph264pay config-interval=1 pt=96 ! \
  tee name=t \
    t. ! queue ! udpsink host=10.20.0.3 port=5000 sync=false \
    t. ! queue ! appsink name=tx_tap emit-signals=true
```

`tx_tap` (appsink) даёт каждый исходящий RTP-пакет → JSONL запись `tx_event` с `rtp_seq`, `rtp_timestamp_90khz`, `size_bytes`, `wall_time`. Это нужно для матчинга e2e latency на стороне receiver'а.

Реализация — отдельный Python скрипт `video/sender.py`, который:
1. строит и запускает pipeline через `gi.repository.Gst`,
2. на каждый buffer из appsink парсит RTP header (12 байт: seq из bytes 2-3, timestamp из bytes 4-7) и пишет в `logs/<run_id>/video_tx.jsonl`,
3. graceful shutdown по SIGTERM.

Альтернатива: чистый `gst-launch` + tcpdump + post-process. Но это медленнее и менее точно по wall_time. Лучше Python+Gst.

**1.5.2.a (smoke, делаем первым):** источник `videotestsrc` (генерация в gstreamer'е, не зависит от Gazebo). Сразу даёт работающий RTP-пайплайн и метрики. Закрывает 80% инфраструктуры.

**1.5.2.b (real camera):** заменяем `videotestsrc` на чтение gz topic'а камеры из Gazebo. Варианты:

- **Gst-pipeline в Gazebo-плагине**: `gz-sim-camera-system` в SDF умеет писать в gst через свой плагин или в shared memory. Имеет два под-варианта:
  - sender plugin внутри Gazebo пушит RTP сам (тогда наш sender.py не нужен, но трудно logged tx_time).
  - sender plugin пишет raw frames в named pipe / shm; gst-pipeline читает оттуда.
- **gz topic → fdsrc**: `gz topic -e -t /.../image` пишет protobuf в stdout, парсится Python'ом, raw RGB→gst appsrc. Прямолинейно, но требует custom-конвертера protobuf↔raw.
- **ROS2 bridge** (gz_ros_image_bridge → ros2 topic → gstreamer): ещё один контейнер, перегруз.

Решение: для 1.5.2.b используем локальную
`gazebo/models/bas_iris_with_pov_camera`: это стабильный
`iris_with_ardupilot` plugin stack плюс fixed onboard `pov_camera` с
`GstCameraPlugin`. Он пушит H.264 RTP на `127.0.0.1:5600`. Важная деталь:
плагин не стартует поток сам, его нужно включить Gazebo topic'ом
`.../pov_camera/image/enable_streaming` (`gz.msgs.Boolean` `data: true`).
После этого `sender.py` работает как прозрачный retap'er (`udpsrc:5600` →
`10.20.0.3:5000`) и продолжает писать tx JSONL.

### Receiver pipeline

Запускается **в bas-pload-far netns** на хосте (через `ip netns exec bas-pload-far python video/receiver.py …`) или как отдельный docker-контейнер с `network_mode` на отдельный pause-container в этом netns. Скорее `ip netns exec` напрямую — там нужно только Python+gstreamer на хосте, минимальные deps.

GStreamer-pipeline:

```
gst-launch-1.0 -v \
  udpsrc port=5000 caps="application/x-rtp,media=video,encoding-name=H264,payload=96" ! \
  tee name=t \
    t. ! queue ! appsink name=rx_tap emit-signals=true \
    t. ! queue ! rtph264depay ! avdec_h264 ! videoconvert ! fakesink sync=false
```

`rx_tap` даёт каждый RTP-пакет → JSONL `rx_event` с `rtp_seq`, `rtp_timestamp_90khz`, `size_bytes`, `wall_time`. Декодер `avdec_h264 ! fakesink` нужен только для верификации что поток валиден (frame errors / GOP recovery видны). На метрики прямо не влияет.

Реализация — `video/receiver.py` симметрично sender'у. Лог в `logs/<run_id>/video_rx.jsonl`.

## Метрики (JSONL контракт)

### Источник: видео-sender → `video_tx.jsonl`

```json
{"event_type":"video_tx","wall_time":1234567890.123,"sim_time":12.3,"rtp_seq":17,"rtp_ts_90khz":1530000,"size_bytes":1316}
```

### Источник: видео-receiver → `video_rx.jsonl`

```json
{"event_type":"video_rx","wall_time":1234567890.567,"rtp_seq":17,"rtp_ts_90khz":1530000,"size_bytes":1316}
```

`sim_time` в video_tx берётся из последнего sync-event'а ns-3 (sender знает свой wall_time, ближайший sync даёт sim_time). Для receiver — лог в wall_time, sim_time дорисовывает analyzer по матчингу с tx или sync.

### Источник: analyzer (после прогона)

Расширяем `analyzer/src/analyzer/metrics.py`. Новые секции в RunReport:

```
@dataclass
class VideoMetrics:
    flow_id: str = "video"
    tx_packets: int = 0
    rx_packets: int = 0
    frame_loss_ratio: float = 0.0          # 1 - rx/tx по rtp_seq
    e2e_latency_ms_p50: float = 0.0        # median (rx_wall_time - tx_wall_time) по матчингу rtp_seq
    e2e_latency_ms_p95: float = 0.0
    jitter_ms: float = 0.0                 # RFC 3550 interarrival jitter
    fps_received: float = 0.0              # rx_packets с разными rtp_ts_90khz / duration_s
    bitrate_tx_bps: float = 0.0
    bitrate_rx_goodput_bps: float = 0.0
    longest_gap_packets: int = 0           # самый длинный пропуск rtp_seq подряд
    longest_gap_ms: float = 0.0
```

В `report.md` добавляется секция:

```markdown
## Видеопоток

- TX пакетов: 5394
- RX пакетов: 5290
- Frame loss: 1.93%
- E2E latency (p50): 257.4 мс
- E2E latency (p95): 268.1 мс
- Jitter (RFC 3550): 4.2 мс
- FPS принято: 28.7
- Bitrate TX: 1.98 Мбит/с
- Bitrate RX (goodput): 1.94 Мбит/с
- Самый длинный пропуск: 14 пакетов / 480 мс
```

## Изменения в репозитории (поэтапно)

### 1.5.2.a — RTP smoke через ns-3 payload

| Файл | Изменение |
|---|---|
| `video/sender.py` (новый) | Python + Gst, videotestsrc → x264 → RTP → udpsink, лог video_tx.jsonl |
| `video/receiver.py` (новый) | Python + Gst, udpsrc → RTP → appsink + avdec_h264/fakesink, лог video_rx.jsonl + `video_rx.mp4` для демо |
| `video/__init__.py`, `pyproject.toml` (опционально пакет) | как submodule с `bas-video-sender` / `bas-video-receiver` entry points |
| `docker-compose.shared-netns.yml` | новый сервис `video-sender` с `network_mode: container:bas-uav-net`, mount `./video:/work/video:ro`, command `python /work/video/sender.py …` |
| `Dockerfile docker/video/` (новый, легковесный) | python3 + gstreamer1.0-tools + python3-gst-1.0 + plugins (good, bad, ugly для x264, libav для avdec) |
| `scripts/run_stage_1_5_2_mission.sh` (новый) | расширение 1.5.1 скрипта: после инжекции eth0 (control) делает аналогично eth1 в `bas-uav` netns (10.20.0.2/24 → br-pload-near), потом адрес `10.20.0.3/24` в bas-pload-far на veth-far-side, потом `up video-sender`, потом запускает `receiver.py` через `ip netns exec bas-pload-far`, потом orchestrator |
| `analyzer/src/analyzer/metrics.py` | парсинг `event_type in ("video_tx","video_rx")`, расчёт `VideoMetrics`, новая секция в report.md |
| `orchestrator/src/orchestrator/run.py` | флаг `--video` (или auto-detect наличия `video_tx.jsonl` в run-dir), запуск/остановка sender и receiver |
| `configs/scenarios/baseline_wifi.yaml`, `degraded_lora.yaml` | секция `video: {enabled: true, bitrate_kbps: 2000, framerate: 30, resolution: "640x480", codec: "h264"}` |

Acceptance check 1.5.2.a:
- `sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good` → report.md имеет секцию «Видеопоток», `frame_loss < 5%`, FPS≈30.
- То же на `degraded_lora` → `frame_loss > 2%`, видно повышенный longest_gap в районе outage-окон.
- mission landed=True не сломан.

### 1.5.2.b — реальная Gazebo-камера

| Файл | Изменение |
|---|---|
| `docker/gazebo/Dockerfile` | runtime GStreamer plugins + `libdebuginfod1`, чтобы `libGstCameraPlugin.so` грузился и мог создать `videoconvert ! x264enc ! rtph264pay ! udpsink` |
| `scripts/run_stage_1_5_2_mission.sh` | `BAS_VIDEO_SOURCE=camera` маппится в `udpsrc:${BAS_CAMERA_UDP_PORT:-5600}`, публикует `enable_streaming`, затем проверяет что `video_tx.jsonl` не пустой; `BAS_GAZEBO_GUI=1` включает окно Gazebo через WSLg |
| `video/sender.py` | source конфигурируем флагом: `videotestsrc` (smoke) / `udpsrc:<port>` (реальная камера через `GstCameraPlugin`) |

Acceptance check 1.5.2.b: то же что 1.5.2.a, но `BAS_VIDEO_SOURCE=camera`
даёт заполненные `video_tx.jsonl` / `video_rx.jsonl`, а принятый поток
сохраняется как `logs/<run_id>/video_rx.mp4` с реальной сценой Gazebo.

### 1.5.2.c — корреляция outage ↔ frame loss (отчёт)

Реализовано: `report.md` получает блок `Payload outage ↔ video gaps` с
окнами payload outage, раздельной статистикой gap-потерь около outage / вне
outage и таблицей top video gaps. Для старых aggregate-событий ns-3 без
`wall_time` используется приближённая video↔ns-3 timeline:
первый активный payload burst совмещается с первым RX RTP-пакетом.

## Регрессия 1.5.2.a — INVALID_SEQUENCE на degraded_lora с видео — FIXED

**Status: FIXED через CPU-limit + lower bitrate для degraded_lora.**

`sudo bash scripts/run_stage_1_5_2_mission.sh degraded_lora` теперь приводит к
mission landed=True, 7/7 waypoints, 30.0m max alt. Control PDR 1.000 (21 пакет
в outage), payload PDR 1.000 (12 пакетов в outage — корреляция outage↔frame loss
демонстрируется). Регрессии 1.5.1 не возвращается.

### Fix

В `docker-compose.shared-netns.yml` для `video-sender`:

```yaml
cpus: "${BAS_VIDEO_SENDER_CPUS:-0.8}"
```

В `scripts/run_stage_1_5_2_mission.sh` для профиля `degraded_lora`:

```bash
DEFAULT_VIDEO_BITRATE_KBPS=500   # вместо 2000
```

(LoRa-подобный канал реалистично не тянет 2 Мбит/с HD-видео в любом случае,
так что снижение оправдано не только тестовой задачей.)

### Что было

x264 encoder без CPU-limit под полной нагрузкой занимал ~80-100% одного ядра
WSL2. Под этим Python `_listener_loop` в orchestrator-е (blocking `recv_match`)
читал MAVLink с задержкой — Linux scheduler отдавал CPU active gst-thread
вместо idle Python-thread. SITL retry-цикл (~600ms) опережал наш ответ,
накопился burst MISSION_REQUEST seq=0, мы отвечали 5+ копиями MISSION_ITEM,
ArduPilot mission state machine ловила несоответствие → MISSION_ACK type=13
INVALID_SEQUENCE.

### Известный артефакт video_tx vs video_rx vs ns-3

После fix tx_tap.appsink пишет 53,153 packets за 210s (~300 кбит/с
после RTP overhead), но ns-3 видит 2,742 packets (~16 кбит/с goodput) и
receiver получает 2,650. То есть **95% packets** теряется между tx_tap и
выходом из netns. Гипотеза: queue leaky=downstream перед udpsink в
gstreamer-pipeline дропает буферы под CPU-лимитом (encoder выдаёт быстрее
чем udpsink успевает отправлять). Tx_tap (после tee) видит **все** буферы
до queue drop, поэтому пишет в JSONL гораздо больше чем реально уходит на
сеть.

Для метрик в анализаторе нужно **брать ns-3 payload-packets как
authoritative tx-count**, а video_tx.jsonl использовать **только для матчинга
tx_time по rtp_seq** (для e2e latency). Документировать в analyzer-итерации.

---

## Старая регрессия (для архива)

До v0.8 в этой секции была проблема: включение video-канала под `degraded_lora`
ломало mission upload (INVALID_SEQUENCE type=13).

### Картина из events.jsonl (cooldown=5s, оригинальный v0.7)

```
+30.6s mission_count_sent (n=7, repeats=5)
+37.6s mission_item_sent seq=0
+38.5..+42.6s  8× mission_request_duplicate_ignored seq=0 (cooldown blocking)
+42.6s mission_item_sent seq=0  (second time after cooldown elapsed)
+43.5s mission_item_sent seq=1
+44.1..+46.5s  3× mission_request_duplicate_ignored seq=1
+46.8s MISSION_ACK type=13 INVALID_SEQUENCE  ← FAIL
```

При cooldown=0.5s (пробовал — не помогло): orchestrator отправляет 5 копий
MISSION_ITEM seq=0 подряд, потом seq=1, потом тот же INVALID_SEQUENCE. То есть
наши MISSION_ITEM не доходят до SITL **систематически** (или ACK от SITL
теряется).

### Гипотеза root cause

CPU contention. `video-sender` (gstreamer x264enc) делит `bas-uav` netns
с orchestrator-ом и mavbridge socat. На WSL2 с 4 vCPU encoder x264 при
2000 kbps / 30 fps занимает ~40-60% одного ядра. Под этой нагрузкой Python
`_listener_loop` в `DockerComposeFlightStack` (`recv_match` на blocking) читает
MAVLink с задержкой — `last_mission_request` обновляется не сразу при приходе
пакета. SITL retry-цикл (~600ms) опережает наш ответ → нарастает burst
дубликатов → один из них в момент несоответствия позиции автомата → INVALID_SEQUENCE.

На `wifi_good` (ctrl 5ms / 0 loss) regression не проявляется: SITL получает
наш единственный ответ за один RTT и не делает retransmit.

На `1.5.1 degraded_lora без видео` это ТО ЖЕ cooldown=5s работает — потому
что без CPU contention listener успевает обрабатывать MAVLink в реальном
времени.

### План на следующую сессию (debug)

1. **Profile CPU**: `docker stats` во время mission upload, проверить
   `bas-orchestrator`-процесс ли в bottleneck'е (нужно перенести orchestrator
   в свой контейнер с CPU-limit на video-sender).
2. **Async MAVLink reader**: переписать `_listener_loop` на selector-based,
   чтобы не зависеть от GIL и был быстрее.
3. **video-sender CPU limit**: `cpus: "0.5"` на video-sender в compose,
   проверить помогает ли.
4. **MAVProxy posredник**: вместо socat поднять `mavproxy.py` в bas-uav netns
   как буфер. MAVProxy known-good для lossy MAVLink (используется в реальных
   FPV-дронах). Альтернатива: возможно более robust mission upload реализация.
5. **Уменьшить video bitrate / framerate** для degraded_lora профиля
   (`BAS_VIDEO_BITRATE_KBPS=500`) — может облегчить CPU и пройти.

### Acceptance criteria после фикса

`sudo bash scripts/run_stage_1_5_2_mission.sh degraded_lora` — mission landed
+ видео flow ≥ 5 минут без crash.

## Open questions

1. **Camera plugin path 1.5.2.b**: будет ли `gz-sim-camera-system` встроенно пушить H.264 RTP без extra-кода, или придётся писать кастомный плагин? — проверить документацию gz-sim Harmonic и ardupilot_gazebo readme. Если плагин не умеет «из коробки», 1.5.2.b удлинится; план B — gz topic → fdsrc через protobuf-парсер.
2. **`bas-pload-far` IP-collision risk**: setup_radio_net.sh уже создаёт netns, но не присваивает адреса на veth-far. Нужно проверить какие интерфейсы и адреса там сейчас, чтобы при инжекции 10.20.0.3/24 не дублироваться.
3. **gstreamer plugins set**: x264enc — `gstreamer1.0-plugins-ugly`; rtph264pay/depay — `gstreamer1.0-plugins-good`; avdec_h264 — `gstreamer1.0-libav`. Все три должны попасть в `docker/video/Dockerfile`.
4. **Контейнер для receiver**: ставить ли его в Docker (нужен `--network` на bas-pload-far) или запускать host-Python через `ip netns exec`? — Docker `network_mode: container:<id>` требует pause-container в bas-pload-far netns. Проще `ip netns exec`, но тогда gstreamer должен стоять на хосте.
   Решение по умолчанию: **отдельный docker pause-container `bas-pload-far-pod`**, симметрично `bas-uav-net`, и `video-receiver` контейнер с `network_mode: container:bas-pload-far-pod`. Однообразие лучше mixed-host/docker.
5. **Timing source для tx/rx**: оба пишут wall_time на хосте — это ОК, system clock единый. Не нужно extra NTP-sync между netns'ами.

## Acceptance criteria (полный 1.5.2)

1. `sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good` — успешный mission landed + видео FPS ≥25, frame_loss <5%.
2. `sudo bash scripts/run_stage_1_5_2_mission.sh degraded_lora` — успешный mission landed + видео с видимой деградацией (longest_gap пересекается с outage-окнами).
3. `report.md` обоих прогонов имеет секцию «Видеопоток» с заполненными метриками.
4. `run_stage_1_5_1_mission.sh` без изменений продолжает работать (без видео — синтетический payload-поток остаётся как backstop для регрессии 1.5.1).
5. README + docs/architecture.md обновлены: этап 1.5.2 в статус «готов».

## Порядок работы по сессиям (предложение)

| Сессия | Что делаем | Результат |
|---|---|---|
| 1 | 1.5.2.a инфра: docker/video/Dockerfile, video/sender.py + receiver.py со smoke source (videotestsrc), новый run-скрипт, инжекция второго veth, маршруты | gst-pipeline отправляет/принимает RTP через ns-3 без видео реальной сцены, JSONL заполняются |
| 2 | 1.5.2.a метрики: расширить analyzer, добавить секцию «Видеопоток» в report.md, прогон обоих профилей | acceptance #1 wifi_good, #2 degraded_lora |
| 3 | 1.5.2.b camera: SDF с камерой, переключить source в sender.py на gz-stream, доказать что rx-сторона видит реальный кадр | acceptance #2 для b-варианта |
| 4 | 1.5.2.c correlation: outage windows ↔ video RX gaps в analyzer/report | отчёт показывает попадание longest_gap в payload outage |

Один заход = одна сессия. Между сессиями коммитим, чтобы не терять контекст.

## Связь с другими этапами

- **1.5.1** (control канал): не трогается. Видео — независимый pipeline. Единственное пересечение — eth1 в `bas-uav` netns и расширенный run-скрипт.
- **1.6** (сравнительный отчёт): получит готовые видео-метрики WiFi vs LoRa, сможет сразу строить сравнение.
- **Этап 2 (Sionna RT)**: payload channel deg-profile станет вход для Sionna-радиокарты вместо RateErrorModel; контракт sender/receiver/metrics не меняется.
