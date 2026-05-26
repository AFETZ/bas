#!/usr/bin/env bash
# Trace SITL UDP + force MAVLink connect.
LOG=/tmp/sitl_port_trace2.log
SITL_LOG=/tmp/sitl_port_capture2.log
BRIDGE_LOG=/tmp/bridge_capture2.log

pkill -f arducopter 2>/dev/null
sleep 1
rm -f "$LOG" "$SITL_LOG" "$BRIDGE_LOG"

# Start bridge.
(/home/afetz/bas-prototype/.venv/bin/python -u <<'PYEOF' &) > "$BRIDGE_LOG" 2>&1
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 9002))
print(f"[bridge] bound 9002", flush=True)
n = 0
import time, json
t0 = time.time()
while True:
    try:
        data, addr = s.recvfrom(2048)
    except KeyboardInterrupt:
        break
    n += 1
    if n <= 3 or n == 50 or n % 200 == 0:
        print(f"[bridge] #{n} got {len(data)}B from {addr}", flush=True)
    payload = json.dumps({
        "timestamp": time.time() - t0,
        "imu": {"gyro": [0,0,0], "accel_body": [0,0,-9.81]},
        "position": [0,0,0],
        "velocity": [0,0,0],
        "attitude": [0,0,0],
    })
    s.sendto(payload.encode() + b"\x00", addr)
PYEOF
sleep 1
echo "[trace] bridge log so far:"
cat "$BRIDGE_LOG"

# Start SITL.
cd /home/afetz/ardupilot/ArduCopter
(/home/afetz/ardupilot/build/sitl/bin/arducopter \
    --model json:127.0.0.1 --speedup 1 \
    --defaults /home/afetz/ardupilot/Tools/autotest/default_params/copter.parm \
    -S 2>&1 | head -50) > "$SITL_LOG" &
SITL_PID=$!
sleep 2

# Connect to SITL TCP 5760 чтобы выйти из "Waiting for connection".
echo "[trace] open TCP 5760 to wake SITL..."
(python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 5760))
print(f'[mav-stub] connected to SITL')
import time; time.sleep(8)
print(f'[mav-stub] disconnect')
s.close()
" 2>&1) &
sleep 6

echo
echo "[trace] === bridge log ==="
cat "$BRIDGE_LOG"
echo
echo "[trace] === SITL log (last 20) ==="
tail -20 "$SITL_LOG"
echo
echo "[trace] === ss -uln after SITL alive ==="
ss -uln | grep -E "9002|9003|9005"
echo
echo "[trace] === ss -utn === (TCP)"
ss -utn | grep -E "5760|9002"

pkill -f arducopter 2>/dev/null
pkill -f "bound 9002" 2>/dev/null
