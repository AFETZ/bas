# Итоговая архитектура прототипа

Этот документ фиксирует фактическое состояние репозитория после закрытия личной
зоны Физулина А.В. по моделированию БАС, каналам связи, MAVROS, ns-3/Sionna и
ручному управлению одним БАС, а также после доведения Stage 3/4 интерфейсов до
проверяемого состояния.

## Что построено

Прототип объединяет шесть контуров:

1. **Полётный контур** — Gazebo Harmonic + `ardupilot_gazebo` + ArduPilot SITL.
2. **Командный контур** — orchestrator, MAVProxy GCS или MAVROS backend.
3. **Сетевой контур** — ns-3 realtime с control/payload каналами, outage/loss,
   LoRa Serial и dynamic channel hook.
4. **Visual/sensor контур** — Gazebo camera, FPV, Cosys-AirSim overlay.
5. **Stage 4 sim-bridge контур** — JsonFdmBridge, MAVLink fanout router,
   real ArduPilot JSON-FDM ARM+takeoff proof.
6. **Доказательный контур** — JSONL события, Markdown/CSV отчёты, видео и plots.

```mermaid
flowchart TB
    subgraph GCS["GCS / НПУ"]
        UI["Web GCS UI<br/>manual controls + RF panel"]
        MP["MAVProxy CLI<br/>verified stdin path"]
        ORCH["Python orchestrator<br/>mission control"]
        ROS["MAVROS bridge<br/>ROS2 service calls"]
    end

    subgraph RADIO["ns-3 / radio simulation"]
        TC["two_channel.cc<br/>control + payload TAP"]
        LS["lora_serial.cc<br/>PTY byte stream"]
        SP["Sionna / RF JSON<br/>loss, delay, RSSI"]
    end

    subgraph UAV["UAV runtime"]
        MB["mavbridge<br/>UDP14550 <-> TCP5760"]
        AP["ArduPilot SITL<br/>ArduCopter"]
        GZ["Gazebo Harmonic<br/>physics, scene, camera"]
        JF["JsonFdmBridge<br/>PWM -> 6DOF -> IMU/GPS"]
    end

    subgraph ART["Artifacts"]
        EV["events.jsonl"]
        NEV["ns3_events.jsonl"]
        REP["report.md / comparison.md"]
        VID["video_rx.mp4 / plots"]
    end

    UI --> MP
    MP --> TC
    ORCH --> TC
    ROS --> TC
    TC --> MB
    LS --> AP
    SP --> TC
    MB --> AP
    AP <--> GZ
    GZ --> TC
    AP <--> JF
    UI --> SP
    ORCH --> EV
    MP --> EV
    TC --> NEV
    LS --> NEV
    EV --> REP
    NEV --> REP
    GZ --> VID
```

## Командные пути

| Путь | Компоненты | Назначение | Статус |
|---|---|---|---|
| `pymavlink` mission | orchestrator -> ns-3 control -> mavbridge -> SITL | AUTO mission, базовый acceptance | Готово |
| MAVProxy GCS | Web UI / driver -> MAVProxy stdin -> ns-3 -> SITL | Ручное управление Stage 2.4 | Готово |
| MAVROS | ROS2/MAVROS bridge -> ns-3 -> SITL | Проверка ROS-based path из ТЗ | Готово |
| LoRa Serial | host PTY -> ns-3 byte stream -> UAV bridge -> SITL | MAVLink без IP-stack в радио-петле | Готово |
| QGroundControl | QGC -> host UDP relay -> mavp2p -> SITL | Внешний GUI одновременно с Web GCS | Готово |
| Stage 4 JSON-FDM | ArduPilot `--model json` -> JsonFdmBridge -> 6DOF dynamics -> sensor JSON -> SITL | ArduPilot ↔ AirSim/JSON-FDM contract + ARM/takeoff proof | Готово |

Stage 2.4 намеренно использует MAVProxy как GCS backend. Прямой `pymavlink`
в ручном Web UI не используется как источник flight-команд.

## Сетевые каналы

### `two_channel.cc`

Основной ns-3 сценарий для IP/TAP режимов:

- `control` канал: MAVLink commands/telemetry;
- `payload` канал: RTP/H.264 video и payload эксперименты;
- параметры: delay, loss, outage windows, jitter/goodput/PDR metrics;
- dynamic JSON hook для Sionna/RF channel updates.

### `lora_serial.cc`

Отдельный сценарий для буквального LoRa Serial требования:

- host-side PTY для GCS;
- container-side PTY/UNIX socket bridge;
- byte stream через ns-3, без IP-stack в радиопетле;
- PHY-calibrated PointToPoint режим под Semtech SX1276;
- legacy signetlabdei/lorawan baseline сохранён в
  `ns3/scenarios/lora_serial_lorawan.cc`.

### Sionna RT / RF

В репозитории есть два связанных, но разных слоя:

1. **Sionna RT pipeline** — offline scene/radio map:
   `scene/iris_runway.xml`, `radio_maps/iris_runway.npz`,
   `scripts/compute_radio_map.py`, `scripts/sionna_channel_publisher.py`.
2. **Stage 2.4 RF/LOS live demo** — lightweight geometry model в Web GCS,
   видимые препятствия в Gazebo, live LOS/NLOS/RSSI/loss/delay график,
   channel JSON для ns-3 polling.

Важно: есть оба режима. RF/LOS demo остаётся лёгким операторским visual layer,
а online Sionna RT включается через `scripts/run_stage_2_4_rt_online_demo.sh`
и обновляет ns-3 по live PathSolver/JSON hook.

## Полётный контур

Gazebo и SITL работают в shared namespace (`bas-uav`) по pause-container
pattern. Это позволяет:

- держать Gazebo/SITL FDM локально и стабильно;
- выводить наружу только моделируемые каналы;
- подключать `mavbridge` внутри BAS-side namespace;
- запускать headless acceptance или Gazebo GUI через WSLg.

Для видео используется либо `videotestsrc`, либо штатная Gazebo POV camera на
модели `iris_with_gimbal`.

## Доказательный контур

Каждый запуск создаёт `logs/<run_id>/`. Основные файлы:

| Файл | Смысл |
|---|---|
| `events.jsonl` | События orchestrator / GCS / MAVLink |
| `ns3_events.jsonl` | ns-3 tx/rx/drop/outage/channel update events |
| `report.md` | Итоговый отчёт по одному прогону |
| `comparison.md`, `comparison.csv` | Сравнение WiFi/LoRa или других пар прогонов |
| `mavproxy_stdout.log` | Реальный вывод MAVProxy |
| `operator_ui_manifest.json` | Конфигурация Web GCS запуска |
| `video_rx.mp4` | Принятый payload video |

Analyzer считает flight metrics, PDR, loss, jitter, goodput, video FPS,
frame loss, e2e latency approximation и outage correlation.

## Этапы

| Этап | Содержание | Состояние |
|---|---|---|
| 1.0-1.4 | Skeleton, Docker, SITL+Gazebo, ns-3 TapBridge, MAVLink без ns-3 | Готово |
| 1.5.0 | Shadow GCS в `bas-ctrl-far` через ns-3 | Готово |
| 1.5.1 | Mission через ns-3 control, `wifi_good` и `degraded_lora` | Готово |
| 1.5.2 | RTP/H.264 payload, Gazebo camera, video metrics, outage correlation | Готово |
| 1.6 | Сравнительный отчёт WiFi vs LoRa | Готово |
| 1.7 | LoRa через Serial Port без IP-stack | Готово |
| 1.8 | ROS2/MAVROS backend | Готово |
| 2.1 | Sionna RT offline radio map + dynamic channel hook | Готово |
| 2.1.e | Online Sionna RT PathSolver + `control+payload` hook | Готово |
| 2.2 | Cosys-AirSim overlay, Windows GPU rendering | Готово |
| 2.3 | Multi-UAV MVP: 2 SITL + 2 iris + mavp2p | Готово |
| 2.4 | Web GCS / MAVProxy ручное управление одним БАС | Готово |
| 2.4 RF | Gazebo obstacles + live LOS/NLOS/RSSI graph | Готово |
| 2.4 QGC | QGroundControl bridge через mavp2p | Готово |
| 2.4 Auto | Playwright + ffmpeg demo recorder | Готово |
| 3.x | Urban scene, ИССГР API/sync/on-board/CV | Готово |
| 4.x | ArduPilot↔AirSim JSON-FDM, MAVLink router, real SITL ARM+takeoff | Готово |

## Stub vs real

Stub-режим в orchestrator оставлен для быстрой отладки логики сценариев и
анализатора без Docker/Gazebo/ns-3. Acceptance и демонстрационные прогоны
используют real-mode runners из `scripts/`.

## Известные ограничения

- HIL/field tests с реальным Pixhawk и motors не входят в scope репозитория.
- Real OSM/satellite streaming не входит в текущий стенд; есть algorithmic
  20x20 км tile grid и synthetic/primitive scenes.
- Stage 4 JsonFdmBridge покрывает Iris-like X-config quadrotor; wind,
  ground effect и battery sag оставлены как future model fidelity.
