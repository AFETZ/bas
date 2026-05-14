"""Display all component phase events from events.jsonl."""
import json, sys
from pathlib import Path

run_dir = Path(sys.argv[1])
events_file = run_dir / "events.jsonl"
SKIP_KEYS = {"event_type","run_id","scenario_id","wall_time","wall_dt","phase","component"}
with events_file.open() as f:
    for l in f:
        e = json.loads(l)
        p = e.get("phase")
        if p:
            extras = {k: v for k, v in e.items() if k not in SKIP_KEYS}
            t = e.get("wall_dt", 0)
            print(f"+{t:.1f}s  {p}  {extras}")
