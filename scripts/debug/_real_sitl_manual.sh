#!/usr/bin/env bash
# Manual SITL + bridge run to debug protocol.
pkill -f arducopter 2>/dev/null
pkill -f arducopter_airsim_interface 2>/dev/null
sleep 1

LOG_DIR=/tmp/sitl_manual
rm -rf "$LOG_DIR"
mkdir -p "$LOG_DIR"

# 1. Start REAL bridge with our X-config physics.
(/home/afetz/bas-prototype/.venv/bin/python -u /home/afetz/bas-prototype/scripts/arducopter_airsim_interface.py \
    --mode=json_fdm --ardupilot-frame=json --no-airsim \
    --log-file "$LOG_DIR/bridge_frames.jsonl" \
    --max-seconds 25 \
    > "$LOG_DIR/bridge.log" 2>&1 &)
sleep 1
echo "=== bridge started ==="
cat "$LOG_DIR/bridge.log"

# 2. Start SITL.
cd /home/afetz/ardupilot/ArduCopter
/home/afetz/ardupilot/build/sitl/bin/arducopter \
    --model json:127.0.0.1 --speedup 1 \
    --defaults /home/afetz/ardupilot/Tools/autotest/default_params/copter.parm \
    -S > "$LOG_DIR/sitl.log" 2>&1 &
SITL_PID=$!
sleep 2

# 3. TCP wake (any connection wakes SITL).
(python3 -c "
import socket, time
s = socket.socket(); s.connect(('127.0.0.1', 5760))
print('[mav-tcp] connected, sleeping 18s')
time.sleep(18)
print('[mav-tcp] done')
" >> "$LOG_DIR/mav_wake.log" 2>&1 &)

# Wait for SITL to write logs.
sleep 12

echo "=== bridge log (последние 30 строк) ==="
tail -30 "$LOG_DIR/bridge.log"
echo
echo "=== SITL log (последние 30 строк) ==="
tail -30 "$LOG_DIR/sitl.log"
echo
echo "=== bridge_frames count ==="
wc -l "$LOG_DIR/bridge_frames.jsonl" 2>&1 || echo "no file"

pkill -f arducopter 2>/dev/null
pkill -f arducopter_airsim_interface 2>/dev/null
pkill -f "mav-tcp" 2>/dev/null
