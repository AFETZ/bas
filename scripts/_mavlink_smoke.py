"""Минимальный mavlink-санити-тест: подключиться к 5760, дождаться HEARTBEAT и GPS."""
import sys
from pymavlink import mavutil

m = mavutil.mavlink_connection("tcp:127.0.0.1:5760", source_system=255)
print("connecting to tcp:127.0.0.1:5760 ...", flush=True)
hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=15)
if not hb:
    print("FAIL: no HEARTBEAT in 15s", flush=True)
    sys.exit(1)
print(f"HEARTBEAT: sysid={hb.get_srcSystem()} type={hb.type} autopilot={hb.autopilot} base_mode={hb.base_mode}", flush=True)

# Запросим частоту телеметрии
m.mav.request_data_stream_send(hb.get_srcSystem(), hb.get_srcComponent(),
                               mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1)

# Несколько событий
collected = {}
import time
end = time.time() + 5
while time.time() < end:
    msg = m.recv_match(blocking=True, timeout=0.5)
    if msg is None:
        continue
    t = msg.get_type()
    collected[t] = collected.get(t, 0) + 1

print("received message types in 5s:", flush=True)
for t, n in sorted(collected.items(), key=lambda x: -x[1]):
    print(f"  {t}: {n}", flush=True)
