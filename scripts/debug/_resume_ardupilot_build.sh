#!/usr/bin/env bash
# Resume waf build для arducopter (re-run после empy fix).
LOG=/home/afetz/bas-prototype/logs/ardupilot_install/install.log
echo "[resume] re-running waf copter at $(date -u)" >> "$LOG"
nohup setsid bash -c "cd /home/afetz/ardupilot && ./waf -j $(nproc) copter" \
    >> "$LOG" 2>&1 < /dev/null &
PID=$!
echo "$PID" > /home/afetz/bas-prototype/logs/ardupilot_install/build.pid
disown "$PID" 2>/dev/null || true
echo "[resume] waf copter started PID=$PID"
sleep 3
ps -p "$PID" -o pid,etime,cmd 2>&1
