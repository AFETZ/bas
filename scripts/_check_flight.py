"""Анализ полёта: max altitude, mission errors, SITL takeoff state."""
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
events_file = run_dir / "events.jsonl"
sitl_file = run_dir / "sitl.log"

events = []
with events_file.open() as f:
    for l in f:
        events.append(json.loads(l))

print(f"=== Run: {run_dir.name} ===\n")

flight = [e for e in events if e.get("event_type") == "flight"]
max_alt = 0.0
max_at = 0.0
for e in flight:
    alt = e.get("position", {}).get("alt_rel_m", 0)
    if alt > max_alt:
        max_alt = alt
        max_at = e.get("wall_dt", 0)
print(f"flight events: {len(flight)}")
print(f"max alt_rel: {max_alt:.2f}m at +{max_at:.1f}s")

mission_evts = [e for e in events if e.get("phase") in (
    "mission_error", "mission_complete", "waypoint_done", "land_timeout",
    "takeoff_sent", "armed", "land_commanded")]
print(f"\nmission lifecycle:")
for e in mission_evts:
    extras = {k:v for k,v in e.items() if k in ("idx", "error", "final_state", "alt_m")}
    print(f"  +{e.get('wall_dt', 0):.1f}s {e.get('phase')}  {extras}")

print(f"\nSITL last 30 lines:")
if sitl_file.exists():
    lines = sitl_file.read_text(errors="replace").splitlines()
    for l in lines[-30:]:
        print(f"  {l}")
else:
    print("  (no sitl.log)")
