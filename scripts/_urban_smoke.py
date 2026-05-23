#!/usr/bin/env python3
"""Quick smoke: GET /stats + /collections/obstacles/items from running ISSGR server."""
import sys, json, urllib.request

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8774"

stats = json.loads(urllib.request.urlopen(f"{base}/stats", timeout=3.0).read())
print("=== stats ===")
print(json.dumps(stats, indent=2, ensure_ascii=False))

obs = json.loads(urllib.request.urlopen(
    f"{base}/collections/obstacles/items", timeout=3.0).read())
print(f"\n=== obstacles n={obs['numberReturned']} ===")
for f in obs["features"]:
    p = f["properties"]
    print(f"  - {p['name']:22s}  cls={p['issgr_class']:35s}  "
          f"h={p['height_m']:5.1f}м  material={p['material']}")
