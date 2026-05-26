#!/usr/bin/env bash
# Trace SITL UDP behavior — what ports does it use?
LOG=/tmp/sitl_port_trace.log
PORT_LOG=/tmp/sitl_port_capture.log

rm -f "$LOG" "$PORT_LOG"

# Start bridge with verbose logging.
(/home/afetz/bas-prototype/.venv/bin/python -u <<'EOF' &) 2>>"$LOG"
import socket, json, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 9002))
print(f"[bridge] bound 9002", flush=True)
n = 0
while True:
    data, addr = s.recvfrom(2048)
    n += 1
    if n <= 5 or n % 50 == 0:
        print(f"[bridge] #{n} got {len(data)}B from {addr}", flush=True)
    # Reply с null-terminated minimal sensor JSON.
    payload = '{"timestamp":%f,"imu":{"gyro":[0,0,0],"accel_body":[0,0,-9.81]},"position":[0,0,0],"velocity":[0,0,0],"attitude":[0,0,0]}' % (n*0.0025)
    s.sendto(payload.encode() + b"\x00", addr)
EOF
sleep 1
echo "[trace] bridge started" >> "$LOG"

# Start SITL.
cd /home/afetz/ardupilot/ArduCopter
/home/afetz/ardupilot/build/sitl/bin/arducopter \
    --model json:127.0.0.1 --speedup 1 \
    --defaults /home/afetz/ardupilot/Tools/autotest/default_params/copter.parm \
    -S 2>&1 | tee -a /tmp/sitl_port_capture.log | head -30 &

sleep 5
echo "[trace] SITL ports listening:"
ss -uln | grep -E "9002|9003" || echo "(none)"
echo
echo "[trace] SITL processes:"
ps -ef | grep arducopter | grep -v grep

sleep 2
echo "[trace] bridge log (first 20 lines):"
head -20 "$LOG"
echo "[trace] SITL log (last 15):"
tail -15 "$PORT_LOG"

pkill -f arducopter 2>/dev/null
pkill -f "0.0.0.0\", 9002" 2>/dev/null
pkill -f "bound 9002" 2>/dev/null
