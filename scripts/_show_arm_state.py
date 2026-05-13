"""Помощник для анализа состояния ARM-стадии в events.jsonl."""
import json
import sys

path = sys.argv[1]
with open(path) as f:
    events = [json.loads(l) for l in f]

print(f"total events: {len(events)}")
arm_events = [e for e in events if "arm" in (e.get("phase") or "").lower()]
print(f"\n--- arm-related component events ---")
for e in arm_events:
    print(f"+{e.get('wall_dt', 0):.1f}s {e.get('phase')}  attempt={e.get('attempt')}  force={e.get('force')}")

acks = [e for e in events if e.get("message_type") == "COMMAND_ACK"]
RESULT_NAMES = {0:"ACCEPTED", 1:"TEMP_REJECTED", 2:"DENIED", 3:"UNSUPPORTED", 4:"FAILED", 5:"IN_PROGRESS"}
print(f"\n--- COMMAND_ACK ({len(acks)} total) ---")
for e in acks:
    cmd = e.get("command")
    res = e.get("result")
    cmd_name = {400:"ARM_DISARM",22:"TAKEOFF",176:"SET_MODE",300:"MISSION_START",512:"REQUEST_MESSAGE"}.get(cmd, str(cmd))
    print(f"+{e.get('wall_dt', 0):.1f}s cmd={cmd_name} result={RESULT_NAMES.get(res, res)}")

sts = [e for e in events if e.get("message_type") == "STATUSTEXT"]
print(f"\n--- ALL STATUSTEXT messages ({len(sts)} total) ---")
for e in sts:
    print(f"+{e.get('wall_dt', 0):.1f}s sev={e.get('severity')} {e.get('text')!r}")

# arm flag transitions
hb = [e for e in events if e.get("message_type") == "HEARTBEAT"]
print(f"\n--- HEARTBEAT: total={len(hb)} ---")
prev_mode, prev_armed = None, None
for e in hb:
    mode = e.get("flight_mode")
    armed = e.get("armed")
    if mode != prev_mode or armed != prev_armed:
        print(f"+{e.get('wall_dt', 0):.1f}s mode={mode} armed={armed}")
        prev_mode, prev_armed = mode, armed

# Heartbeat density (count per 10s bin)
print(f"\n--- HEARTBEAT density (per 10s, last 80s) ---")
if hb:
    last_t = hb[-1].get("wall_dt", 0)
    bins = {}
    for e in hb:
        t = e.get("wall_dt", 0)
        bin_id = int(t // 10) * 10
        bins[bin_id] = bins.get(bin_id, 0) + 1
    for t in sorted(bins.keys())[-12:]:
        print(f"  +{t}s..+{t+10}s: {bins[t]} HBs")
