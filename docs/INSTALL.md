# Installation Guide

Полная установка BAS Prototype от нуля до запущенного `auto_demo`.

> 💡 Если просто хочешь one-command setup — запусти `sudo bash scripts/bootstrap.sh`.
> Этот файл нужен если что-то пошло не так или хочешь поэтапно понять что
> происходит.

## Tier 0 — System requirements check

```bash
# OS
lsb_release -a              # Ubuntu 22.04+ or 24.04 (WSL2 тоже OK)

# CPU
nproc                       # минимум 4, рекомендую 8+

# RAM
free -g                     # минимум 16 GB

# GPU (опционально, нужен для Sionna RT live + Windows AirSim)
nvidia-smi                  # должен показать NVIDIA GPU + CUDA 12.x

# Disk
df -h .                     # минимум 10 GB свободного места

# Networking
ip route                    # должен быть default route
```

## Tier 1 — System packages (apt)

```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential cmake git curl wget unzip \
    python3 python3-pip python3-venv \
    iproute2 bridge-utils socat \
    docker.io docker-compose-v2 \
    ffmpeg \
    vulkan-tools libvulkan1 mesa-vulkan-drivers \
    libsdl2-2.0-0 libsdl2-image-2.0-0 \
    libxss1 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpangocairo-1.0-0 libasound2-data libasound2t64 \
    fonts-liberation \
    vulkan-validationlayers
```

### WSL2 GPU rendering (опционально, для Cosys-AirSim Linux mode)

```bash
# kisak-mesa PPA — даёт Dozen Vulkan-over-D3D12 ICD для NVIDIA в WSL2
sudo add-apt-repository -y ppa:kisak/kisak-mesa
sudo apt-get update
sudo apt-get install -y mesa-vulkan-drivers   # переустановит на Mesa 26+

# Verify Dozen ICD
ls /usr/share/vulkan/icd.d/dzn_icd.json
vulkaninfo --summary | grep -E 'deviceName|driverID'
# Должно быть: "Microsoft Direct3D12 (NVIDIA GeForce RTX ...)" / DRIVER_ID_MESA_DOZEN
```

## Tier 2 — Docker

```bash
# Если ещё не установлен — bootstrap скрипт сделает это сам
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker   # или logout/login

# Verify
docker info | head -5
```

WSL2 specifics: Docker Desktop НЕ нужен — используется системный Docker
внутри WSL2 distro. Если Docker Desktop установлен, отключи WSL2 integration
для этой distro.

## Tier 3 — Python venv

```bash
cd /path/to/bas-prototype

# Создать venv (без system site packages)
python3 -m venv .venv
source .venv/bin/activate

# Установить orchestrator + analyzer
pip install --upgrade pip
pip install -e ./orchestrator
pip install -e ./analyzer

# Дополнительные deps
pip install msgpack playwright

# Playwright Chromium для auto demo recorder
playwright install chromium
sudo playwright install chromium   # отдельно под root, потому что
                                   #  auto_demo wrapper запускается через sudo
```

## Tier 4 — Sionna RT (опционально, для Stage 2.1)

Sionna требует отдельный venv с TensorFlow + Mitsuba 3:

```bash
python3 -m venv sionna_env
source sionna_env/bin/activate
pip install -r requirements_sionna.txt
```

Verify:

```bash
./sionna_env/bin/python scripts/sionna_smoke.py
# Должно вывести "smoke OK" и shape радиокарты
```

## Tier 5 — ns-3 build (внутри Docker)

ns-3 собирается **внутри** контейнера `bas/ns3:dev` — не нужно ставить локально.
Bootstrap скрипт собирает:

```bash
sudo bash scripts/debug/_start_build.sh     # собирает bas/ns3:dev (~10 мин)
# или
docker compose -f docker-compose.shared-netns.yml build ns3
```

Verify:
```bash
docker run --rm bas/ns3:dev /work/ns3-src/build/scratch/ns3.40-two_channel-optimized --help
```

## Tier 6 — ArduPilot source SITL binary (для Stage 4 JSON-FDM e2e)

Обычные Docker/Gazebo demos используют контейнерный SITL. Для real
`_real_sitl_e2e_smoke.py` нужен локальный `arducopter` binary:

```bash
bash scripts/install_ardupilot.sh

# Verify binary exists
test -x ~/ardupilot/build/sitl/bin/arducopter
```

Stage 4 e2e smoke сам запускает `arducopter --model json:127.0.0.1`,
поднимает `JsonFdmBridge`, проверяет MAVLink telemetry, force-arm и RC
takeoff.

## Tier 7 — Cosys-AirSim (опционально, для Stage 2.2 overlay)

Wrapper делает auto-download при первом запуске. Если хочешь pre-download:

### Linux build (~637 MB) — для headless smoke на WSL2

```bash
mkdir -p ~/cosys-airsim
cd ~/cosys-airsim
curl -L -O https://github.com/Cosys-Lab/Cosys-AirSim/releases/download/5.5-v3.3/Blocks_packaged_Linux_55_33.zip
unzip -q Blocks_packaged_Linux_55_33.zip
```

### Windows build (~556 MB) — для real GPU rendering из WSL2

```bash
# WSL2: Windows download через PowerShell или curl на /mnt/c
mkdir -p /mnt/c/Users/$USER/cosys-airsim
cd /mnt/c/Users/$USER/cosys-airsim
curl -L -O https://github.com/Cosys-Lab/Cosys-AirSim/releases/download/5.5-v3.3/Blocks_packaged_Windows_55_33.zip
powershell.exe -Command "Expand-Archive -Path '$(wslpath -w Blocks_packaged_Windows_55_33.zip)' -Force"

# Firewall rule (одноразово, требует UAC)
powershell.exe -Command "Start-Process cmd.exe -Verb RunAs -ArgumentList '/c','netsh advfirewall firewall add rule name=CosysAirSim41451 dir=in action=allow protocol=TCP localport=41451'"
```

### Cosys-AirSim Python client

```bash
cd ~/cosys-airsim
curl -L -O https://github.com/Cosys-Lab/Cosys-AirSim/releases/download/5.5-v3.3/python_api_client_33.whl
mv python_api_client_33.whl cosysairsim-3.3-py3-none-any.whl   # fix PEP 427 имя
./bas-prototype/.venv/bin/pip install cosysairsim-3.3-py3-none-any.whl
```

Детальная инструкция: [stage_2_2_airsim_overlay.md](stage_2_2_airsim_overlay.md).

## Tier 8 — Verify install

```bash
# 1. Stub-режим (без Docker, без UE5)
sudo .venv/bin/python scripts/airsim_stub_server.py --port 41452 &
.venv/bin/python scripts/airsim_client.py --port 41452
kill %1
# Должно вывести: ping=True, server version=1, scene objects

# 2. Web GCS demo mode (без Docker, без SITL)
.venv/bin/python scripts/gcs_web_ui_server.py --demo
# Открыть http://127.0.0.1:8765/ — увидите УИ с simulated telemetry

# 3. ns-3 inside Docker
sudo bash scripts/debug/_smoke_radio.sh
# Должно создать logs/<run_id>/ns3_events.jsonl с tx/rx events

# 4. Full headless smoke
sudo bash scripts/run_stage_1_5_2_mission.sh wifi_good
# ~3 мин: создаст video_rx.mp4 + report.md с 7/7 waypoints

# 5. Stage 4 bridge smoke (без Docker/Gazebo)
bash scripts/run_stage_4_sim_bridges_demo.sh smoke
# Должно вывести: router smoke OK + JSON-FDM climb/yaw physics OK

# 6. Stage 4 real ArduPilot JSON-FDM e2e (требует Tier 6)
.venv/bin/python scripts/_real_sitl_e2e_smoke.py
# Должно вывести: ARMED=True, Takeoff delta >0.5m, Max PWM > hover
```

## WSL2 particulars

| Аспект | Что делать |
|---|---|
| `wsl --version` | Минимум 2.0.0, рекомендую WSL2 22+ |
| `.wslconfig` (опционально) | `[wsl2]\nnetworkingMode=mirrored` упростит host-доступ к WSL services |
| systemd | WSL2 поддерживает systemd с 2022. `/etc/wsl.conf`: `[boot]\nsystemd=true` |
| Docker | системный, **не** Docker Desktop |
| GPU compute (CUDA) | работает из коробки через `/usr/lib/wsl/lib/libcuda.so` |
| GPU graphics (Vulkan) | через Dozen (D3D12 backend) или Linux native NVIDIA driver |
| WSLg X11 | DISPLAY=:0 авто, `/tmp/.X11-unix/X0` |

## Очистка после install/runs

```bash
# Полная остановка стенда
sudo bash scripts/setup_radio_net.sh down
sudo docker compose -f docker-compose.shared-netns.yml \
    --profile fpv --profile qgc --profile multi down -v
sudo pkill -f 'Blocks\|gcs_web_ui\|mavproxy\|airsim_'

# Удаление per-run артефактов
rm -rf logs/*
rm -rf output/

# Удаление установленных AirSim binary (если нужно)
rm -rf ~/cosys-airsim /mnt/c/Users/$USER/cosys-airsim
```

## Troubleshooting

См. [TROUBLESHOOTING.md](TROUBLESHOOTING.md) для частых проблем:
- ns-3 build fail
- Docker daemon not reachable
- Sionna RT GPU not detected
- WSL2 networking issues (порт занят, firewall)
- Cosys-AirSim Windows firewall blocks 41451
- GPU rendering on WSL2 (Dozen vs llvmpipe)
