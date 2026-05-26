#!/usr/bin/env bash
# Запускает docker compose build в фоне, сохраняет PID в logs/build.pid.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p logs
rm -f logs/build.log logs/build.pid

nohup sg docker -c "docker compose build --progress=plain" \
    > logs/build.log 2>&1 &
BUILD_PID=$!
echo "$BUILD_PID" > logs/build.pid
disown

echo "BUILD_PID=$BUILD_PID"
echo "LOG=logs/build.log"
sleep 2
ls -la logs/build.log logs/build.pid
echo "---first lines---"
head -5 logs/build.log 2>/dev/null || echo "(пока пусто)"
