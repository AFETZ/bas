#!/bin/bash
# Container-side entrypoint для lora_serial с PTY-bridge.
# Запускается ИЗ ns-3 контейнера. Делает:
#   1. Поднимает container-side socat: UNIX socket -> container PTY
#   2. Компилирует ns-3 сценарий
#   3. Запускает lora_serial с PTY-paths
#
# Ожидается что host-side socat и UNIX sockets уже подняты через
# scripts/setup_lora_bridge.sh up на host'е, и /tmp/bas-bridge примонтирован
# в /bridge.
set -e

RUN_ID="${1:-lora_bridge_run}"
DURATION="${2:-30}"
SF="${3:-7}"
BW="${4:-125000}"
DISTANCE="${5:-500}"

echo "[entrypoint] container-side socat: UNIX-CONNECT -> PTY"
mkdir -p /tmp/pty
socat -d PTY,link=/tmp/ptyUAV_lora,raw,echo=0,b57600 \
         UNIX-CONNECT:/bridge/lora-uav.sock &
SOCAT_UAV_PID=$!
socat -d PTY,link=/tmp/ptyGCS_lora,raw,echo=0,b57600 \
         UNIX-CONNECT:/bridge/lora-gcs.sock &
SOCAT_GCS_PID=$!
sleep 2

echo "[entrypoint] container PTYs:"
ls -la /tmp/ptyUAV_lora /tmp/ptyGCS_lora 2>&1 || true

echo "[entrypoint] compiling ns-3"
cp /work/ns3/scenarios/lora_serial.cc /work/ns3-src/scratch/
cd /work/ns3-src
./ns3 build > /tmp/build.log 2>&1

echo "[entrypoint] running ns-3 lora_serial"
/work/ns3-src/build/scratch/ns3.40-lora_serial-optimized \
    --runId="${RUN_ID}" \
    --duration="${DURATION}" \
    --sf="${SF}" --bandwidth="${BW}" --distance="${DISTANCE}" \
    --ptyUavPath=/tmp/ptyUAV_lora \
    --ptyGcsPath=/tmp/ptyGCS_lora

# Cleanup.
kill "${SOCAT_UAV_PID}" "${SOCAT_GCS_PID}" 2>/dev/null || true
