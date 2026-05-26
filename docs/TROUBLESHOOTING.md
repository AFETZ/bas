# Troubleshooting Guide

Частые проблемы и быстрые решения.

## Docker / containers

### `Docker daemon не отвечает`

```bash
sudo systemctl status docker
sudo systemctl restart docker
# Или WSL2:
sudo service docker start
```

### `joining network namespace of container: No such container`

Stale docker state после crashed run.

```bash
sudo docker rm -f bas-uav-net bas-gazebo bas-sitl bas-sitl2 bas-mavbridge \
     bas-mavrouter bas-mavrouter-multi bas-ns3-stage24 bas-fpv-mjpeg \
     bas-video-sender bas-video-receiver 2>/dev/null
sudo docker container prune -f
sudo rm -f /var/run/netns/bas-uav /var/run/netns/bas-ctrl-far /var/run/netns/bas-ctrl-near
```

### `Error response from daemon: error while creating mount source path`

Volume mount conflict. Перезапустить Docker daemon:

```bash
sudo systemctl restart docker
```

## Web GCS UI

### `OSError: [Errno 98] Address already in use` на :8765

Stale Python от прошлого запуска. С последнего commit (`8463acf`) wrapper
сам преflight-килит, но если осталось:

```bash
sudo ss -tlnp | grep :8765
sudo kill <PID>
```

### Web UI открывается но `local_source: null` навсегда

SITL не публикует LOCAL_POSITION_NED. Это норма — мой derived NED fix
работает из GPS lat/lon. Если по-прежнему `null` через 30с:

```bash
# Проверить SITL подключён?
sudo ip netns exec bas-uav ss -tln | grep 5760
# MAVProxy получает данные?
tail -f logs/<run>/mavproxy_stdout.log | grep -E 'GPS_RAW|GLOBAL_POSITION'
```

### WASD/ЦФЫВ не работают, дрон стоит

С commit `4592227` movement_actions auto-takeoff. Если до этого:
```bash
git pull origin main
```

Иначе руками: GUIDED → ARM → TAKEOFF 10м, потом WASD.

## ns-3

### `ns-3 контейнер завершился до старта`

```bash
sudo docker logs bas-ns3-stage24 2>&1 | tail -80
# Часто: build failure внутри контейнера. Перерёбилл:
sudo bash scripts/debug/_start_build.sh
```

### ns-3 не получает пакеты от bas-uav

ARP race на WSL2. Wrapper уже флашит neigh table, но если осталось:

```bash
sudo ip netns exec bas-uav ip neigh flush all
sudo ip netns exec bas-ctrl-far ip neigh flush all
```

### `setsockopt: Operation not permitted` в ns-3

TapBridge требует CAP_NET_ADMIN. Контейнер должен быть `--cap-add NET_ADMIN
--privileged`. Это уже в compose.

## SITL

### SITL TCP 5760 single-client conflict

Если пытаешься подключить и MAVProxy, и QGC напрямую к 5760 — refuse.
Используй `BAS_GCS_QGC=1` mode — там mavp2p router multiplex'ит.

### `Param ARMING_CHECK ... pre-arm failed`

```bash
sudo env BAS_STAGE24_FORCE_ARM=1 bash scripts/run_stage_2_4_fpv_rf_demo.sh
# или через UI кнопка FORCE
```

### Mission AUTO armed но дрон стоит

Это была проблема Stage 1.8 до коммита `22e8622`. Решено через
`MAV_CMD_MISSION_START` (CommandLong 300).

### Stage 4 `_real_sitl_e2e_smoke.py` не стартует

Проверь локальный ArduPilot binary:

```bash
test -x ~/ardupilot/build/sitl/bin/arducopter || bash scripts/install_ardupilot.sh
```

Если порт `5760` занят, останови старые SITL/bridge процессы:

```bash
ps -eo pid,args | rg '[a]rducopter|[a]rducopter_airsim_interface'
pkill -f 'arducopter --model json' || true
pkill -f 'arducopter_airsim_interface.py.*json_fdm' || true
```

Ожидаемый успешный финал: `ARMED: True`, takeoff delta >0.5m,
max PWM > hover. Если `GLOBAL_POSITION_INT` невалидный, смотри
`/tmp/_real_sitl_smoke_*/sitl.log` и `bridge.log`: чаще всего причина в
сломанных JSON-FDM fields или в том, что bridge не видит `servo_packet_16`.

## GPU / Sionna RT / AirSim

### `Could not initialize OptiX`

В WSL2 OptiX SDK по дефолту не пробрасывается. Решения:

```bash
# Sionna RT — переключиться на LLVM (CPU) variant
export MITSUBA_VARIANT=llvm_ad_mono_polarized

# Или установить OptiX через NVIDIA WSL guide
# https://developer.nvidia.com/blog/announcing-cuda-on-windows-subsystem-for-linux-2/
```

### Cosys-AirSim Linux: `Initializing SDL: dummy video driver` + crash

UE5 5.5 на WSL2 + llvmpipe Vulkan SM6 не работает (missing mesh_shader,
int64 atomics). Решения:

1. **Использовать Windows mode** (рекомендуется):
   ```bash
   sudo env BAS_AIRSIM_MODE=windows bash scripts/run_stage_2_2_airsim_overlay.sh
   ```
2. **Использовать `-nullrhi`** (pose API ok, image API empty):
   ```bash
   sudo env BAS_AIRSIM_MODE=linux bash scripts/run_stage_2_2_airsim_overlay.sh
   ```

### `Refusing to run with the root privileges` от UE5

UE5 security check. Wrapper уже использует `sudo -u $SUDO_USER` для запуска.
Если ставил вручную:

```bash
sudo chown -R $USER:$USER ~/cosys-airsim
~/cosys-airsim/Blocks_packaged_Linux_55_33/Linux/Blocks.sh
```

### Cosys-AirSim Windows: `Connection refused` от 172.30.16.1:41451

Windows Firewall блокирует. Одноразовая настройка (admin PowerShell):

```powershell
New-NetFirewallRule -DisplayName "CosysAirSim 41451" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 41451
```

Или из WSL через RunAs:
```bash
powershell.exe -Command "Start-Process cmd.exe -Verb RunAs -ArgumentList '/c','netsh advfirewall firewall add rule name=CosysAirSim41451 dir=in action=allow protocol=TCP localport=41451'"
```

### Vulkan показывает только `llvmpipe`

```bash
# Установить Dozen (Vulkan-over-D3D12) для NVIDIA GPU в WSL2:
sudo add-apt-repository -y ppa:kisak/kisak-mesa
sudo apt update && sudo apt install -y mesa-vulkan-drivers
vulkaninfo --summary | grep deviceName
# Должно появиться: Microsoft Direct3D12 (NVIDIA GeForce RTX ...)
```

## FPV / video

### `FPV upstream not reachable ([Errno 104] Connection reset by peer)`

socat в bas-uav netns ещё не stabilизирован. С commit `0f9c2c4`-ish auto_demo
делает 6 retry с 3-секундной паузой. Если по-прежнему — gst pipeline crashed:

```bash
sudo docker logs bas-fpv-mjpeg 2>&1 | tail -20
sudo docker restart bas-fpv-mjpeg
```

### FPV в UI чёрный экран

Gazebo camera plugin не enabled. Wrapper делает `gz topic enable_streaming`
в `start_fpv_pipeline()`. Если поломалось:

```bash
sudo docker exec bas-gazebo gz topic -t \
  "/world/iris_runway/model/iris_with_gimbal/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming" \
  -m gz.msgs.Boolean -p "data: true"
```

### `video_rx.mp4` пустой (0 KB)

GStreamer receiver не получил frames. Проверь:

```bash
tail -20 logs/<run>/video_receiver.log
# Часто: "no RTP frames received" — выяснилось что sender не запустился
tail -20 logs/<run>/video_sender.log
```

## MAVROS

### `mavros_node` segfault / timeout

ROS2 humble + MAVROS 2.14 имеют известные QoS issues. С commit `d3e882a`
исправлено `qos_profile_sensor_data` + `transient_local` для ctrl topics.

```bash
git log --oneline | grep -E 'stage 1\.8|mavros'
git pull origin main
```

### MAVROS не получает heartbeat

mavros_node должен подключаться к SITL TCP 5760 напрямую. Если запущен с
mavbridge параллельно — TCP single-client conflict:

```bash
# Stage 1.8 wrapper НЕ запускает mavbridge — это правильно
# Если ручной запуск, убедись:
sudo docker ps | grep mavbridge   # должно быть пусто в Stage 1.8
```

## LoRa Serial

### `socat E /dev/ptyGCS_lora: No such file or directory`

PTY не создан. Запусти setup_lora_bridge:

```bash
sudo bash scripts/setup_lora_bridge.sh up
ls -la /tmp/ptyGCS_lora   # должен быть символ link
```

## Sionna RT

### `ModuleNotFoundError: No module named 'sionna'`

Не активирован sionna_env venv:

```bash
source sionna_env/bin/activate
python -c 'import sionna; print(sionna.__version__)'
# 1.2.2
```

Если venv нет — см. [INSTALL.md#tier-4--sionna-rt](INSTALL.md#tier-4--sionna-rt-опционально-для-stage-21).

### `mi.set_variant('cuda_*') failed: Could not initialize OptiX!`

См. выше "OptiX". Fallback на `llvm_ad_mono_polarized`.

## WSL2 specific

### `WSL: проксисервер localhost обнаружена, но не отражена в WSL`

Это warning, не error. Игнорируется.

### Сервис на 8765/41451 видим в WSL но не на Windows

WSL2 NAT mode имеет ограниченный port forwarding. Решения:

1. Использовать WSL eth0 IP напрямую (`wsl hostname -I`) вместо `localhost`
2. Включить mirrored networking в `%USERPROFILE%/.wslconfig`:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   `wsl --shutdown` → перезапуск.

### Time drift между WSL и Windows

Часы WSL2 могут уйти на несколько секунд после Windows hibernate. Это ломает
SITL clock sync. Чинить:

```bash
sudo hwclock -s
# или
sudo ntpdate pool.ntp.org
```

## Получить более детальный лог

```bash
# Включить debug в каждом компоненте
sudo env GST_DEBUG=4 BAS_GAZEBO_GUI=0 bash scripts/run_stage_2_4_fpv_rf_demo.sh

# Все Docker логи в один файл
LATEST=$(ls -td logs/* | head -1)
for c in bas-gazebo bas-sitl bas-mavbridge bas-ns3-stage24 bas-fpv-mjpeg; do
    sudo docker logs $c > $LATEST/${c}.log 2>&1
done

# Live tail всего events.jsonl с фильтрацией
tail -f $LATEST/events.jsonl | jq -c 'select(.event_type=="flight" or .event_type=="component")'
```

## Если ничего не помогло

1. Открой issue на GitHub с:
   - Команда которую запустил
   - Полный output вplaceholder последних 50 строк
   - `git log -1 --oneline`
   - `uname -a` + `lsb_release -a`
   - `docker info | head -10`
2. Прикрепи tarball последнего `logs/<run>/` — там обычно весь контекст
