#!/usr/bin/env bash
LAST="$(ls -td /home/afetz/bas-prototype/logs/stage_1_5_0_* | head -1)"
echo "DIR=$LAST"
ls -la "$LAST/"
echo
echo "=== shadow last 6 ==="
tail -6 "$LAST/shadow_gcs.jsonl"
echo
echo "=== ns3 last 4 ==="
tail -4 "$LAST/ns3_events.jsonl"
echo
echo "=== summary ==="
python3 - <<PYEOF
import json
import sys
events = []
with open("$LAST/shadow_gcs.jsonl") as f:
    for line in f:
        events.append(json.loads(line))
print(f"shadow events: {len(events)}")
by_type = {}
flights = []
for e in events:
    if e.get("event_type") == "flight":
        flights.append(e)
    by_type[e.get("event_type", "?")] = by_type.get(e.get("event_type", "?"), 0) + 1
print(f"  by event_type: {by_type}")
if flights:
    f0 = flights[0]
    fn = flights[-1]
    print(f"  first flight pos: {f0.get('position')}")
    print(f"  last  flight pos: {fn.get('position')}")
    print(f"  span: {fn.get('sim_time', 0) - f0.get('sim_time', 0):.1f}s")
summary_evt = [e for e in events if e.get("phase") == "summary"]
if summary_evt:
    s = summary_evt[-1]
    print(f"  by mavlink type (top-10):")
    for t, n in list(s.get("by_type", {}).items())[:10]:
        print(f"    {t}: {n}")
PYEOF
