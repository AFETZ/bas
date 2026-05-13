#!/usr/bin/env bash
LAST="$(ls -td /home/afetz/bas-prototype/logs/stage_1_5_1_* | head -1)"
echo "DIR=$LAST"
ls -la "$LAST/"
echo
python3 - <<PYEOF
import json
events = []
with open("$LAST/shadow_gcs.jsonl") as f:
    for line in f:
        events.append(json.loads(line))
print(f"shadow events: {len(events)}")
by_type = {}
flights = []
for e in events:
    by_type[e.get("event_type", "?")] = by_type.get(e.get("event_type", "?"), 0) + 1
    if e.get("event_type") == "flight":
        flights.append(e)
print(f"  by event_type: {by_type}")
summary_evt = [e for e in events if e.get("phase") == "summary"]
if summary_evt:
    s = summary_evt[-1]
    print(f"  duration: {s.get('duration_s', 0):.1f}s")
    print(f"  total mavlink msgs: {s.get('messages_received', 0)}")
    print(f"  by mavlink type (top-10):")
    for t, n in list(s.get("by_type", {}).items())[:10]:
        print(f"    {t}: {n}")
    print(f"  last position: {s.get('last_position')}")
print()
# ns-3 stats
import json
ns3_events = []
with open("$LAST/ns3_events.jsonl") as f:
    for line in f:
        ns3_events.append(json.loads(line))
print(f"ns3 events: {len(ns3_events)}")
control = [e for e in ns3_events if e.get("flow_id") == "control"]
if control:
    last = control[-1]
    first_nonzero = next((e for e in control if e.get("packets_tx", 0) > 0), control[0])
    print(f"  control flow last: bytes_tx={last.get('bytes_tx')} packets_tx={last.get('packets_tx')} pdr={last.get('packets_rx',0)/max(1,last.get('packets_tx',1)):.4f}")
PYEOF
