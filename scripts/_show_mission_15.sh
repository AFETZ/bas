#!/usr/bin/env bash
LAST="${1:-$(ls -td /home/afetz/bas-prototype/logs/stage_1_5_1_mission_* | head -1)}"
echo "DIR=$LAST"
ls -la "$LAST/" 2>/dev/null
echo
python3 - <<PYEOF
import json, statistics
events = []
try:
    with open("$LAST/events.jsonl") as f:
        for line in f:
            events.append(json.loads(line))
except FileNotFoundError:
    events = []
print(f"events: {len(events)}")
by_type = {}
for e in events:
    by_type[e.get("event_type", "?")] = by_type.get(e.get("event_type", "?"), 0) + 1
print(f"  by event_type: {by_type}")

scn = [e for e in events if e.get("event_type")=="scenario"]
if scn:
    print(f"  scenario final: status={scn[-1].get('status')}, reason={scn[-1].get('reason')}")

# Mission runner timeline.
mr = [e for e in events if e.get("component")=="mission-runner"]
print(f"\nmission-runner timeline:")
for e in mr:
    extras = {k:v for k,v in e.items() if k not in ("event_type","run_id","scenario_id","wall_time","wall_dt","phase","component")}
    extras_s = " ".join(f"{k}={v}" for k,v in extras.items())
    print(f"  +{e.get('wall_dt'):.1f}s {e.get('phase'):30s} {extras_s}")

# Flight statistics.
flight = [e for e in events if e.get("event_type")=="flight"]
if flight:
    f0, fn = flight[0], flight[-1]
    fly_durations = [fn.get("sim_time", 0) - f0.get("sim_time", 0)]
    print(f"\nflight: {len(flight)} samples, span={fly_durations[0]:.1f}s")
    landed = [e for e in flight if e.get("mission_state")=="landed"]
    print(f"  landed samples: {len(landed)}")
    print(f"  final pos: {fn.get('position')}")
    print(f"  final mode: {fn.get('flight_mode')}")

# STATUSTEXT messages of severity <=3 (warnings/errors).
sts = [e for e in events if e.get("message_type")=="STATUSTEXT" and e.get("severity",6)<=4]
if sts:
    print(f"\nstatus warnings/errors ({len(sts)}):")
    for e in sts[:15]:
        print(f"  +{e.get('wall_dt'):.1f}s sev={e.get('severity')} {e.get('text')!r}")
PYEOF
echo
echo "=== analyzer report ==="
/home/afetz/bas-prototype/.venv/bin/bas-analyzer "$LAST" 2>&1 | sed -n '7,18p'
