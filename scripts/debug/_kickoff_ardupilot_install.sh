#!/usr/bin/env bash
# Kickoff ArduPilot install в фоне с правильным logging.
set -e
LOG_DIR=/home/afetz/bas-prototype/logs/ardupilot_install
mkdir -p "$LOG_DIR"
rm -rf /home/afetz/ardupilot 2>/dev/null || true
echo "[kickoff] starting ArduPilot install at $(date -u)" > "$LOG_DIR/install.log"
nohup setsid env BAS_SKIP_APT=1 bash /home/afetz/bas-prototype/scripts/install_ardupilot.sh \
    >> "$LOG_DIR/install.log" 2>&1 < /dev/null &
PID=$!
echo "$PID" > "$LOG_DIR/install.pid"
disown "$PID" 2>/dev/null || true
echo "[kickoff] PID=$PID logged to $LOG_DIR/install.log"
sleep 2
echo "[kickoff] verify process running:"
ps -p "$PID" -o pid,etime,cmd 2>&1 || echo "process not found"
