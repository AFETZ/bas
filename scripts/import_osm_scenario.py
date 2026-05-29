#!/usr/bin/env python3
"""OSM → ИССГР + Gazebo + Sionna scenario importer.

Тянет building footprints из OpenStreetMap через **Overpass API** (чистый
HTTP + JSON, без geopandas/GDAL — self-contained, stdlib-only) и генерит:

  1. ИССГР obstacles — POST в REST API (--issgr-url) ИЛИ seed JSON файл.
     Каждое здание = Obstacle(geometry_polygon, height_m, material).
  2. Gazebo SDF world — box-приближение каждого здания (для физики/визуала).
  3. Summary — статистика сцены (N зданий, суммарный footprint, bbox).

Так "карта тестового сценария" становится любой точкой Земли, а не
хардкод iris_runway. Связывает 3 модуля: ИССГР ↔ Gazebo ↔ (опц. Sionna).

Usage:
  # По bbox (south,west,north,east):
  ./import_osm_scenario.py --bbox 55.7558,37.6173,55.7608,37.6223 \\
      --name moscow_center --out-dir generated/moscow

  # По названию места (Nominatim geocode → bbox):
  ./import_osm_scenario.py --place "Tverskaya, Moscow" --radius-m 300 \\
      --name tverskaya --issgr-url http://127.0.0.1:8770

Лицензия данных: OpenStreetMap © ODbL. Атрибуция обязательна при
публикации производных карт.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# Несколько Overpass-зеркал — main instance часто перегружен (timeout/429).
# Пробуем по очереди, пока какое-то не ответит.
OVERPASS_MIRRORS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "bas-prototype-osm-importer/1.0 (grant simulation; contact: local)"

# building:levels → meters (типовая высота этажа).
METERS_PER_LEVEL = 3.0
DEFAULT_HEIGHT_M = 10.0

# OSM building tag → ИССГР material.
MATERIAL_MAP = {
    "concrete": "concrete", "brick": "brick", "steel": "metal",
    "metal": "metal", "wood": "wood", "glass": "glass",
}


def geocode_place(place: str, timeout: float = 10.0) -> tuple[float, float]:
    """Nominatim geocode → (lat, lon) центра места."""
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    req = urllib.request.Request(f"{NOMINATIM_URL}?{q}",
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    if not data:
        raise ValueError(f"Nominatim: место не найдено: {place!r}")
    return float(data[0]["lat"]), float(data[0]["lon"])


def bbox_from_center(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """Центр + радиус → (south, west, north, east)."""
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def fetch_buildings(bbox: tuple[float, float, float, float],
                    timeout: float = 90.0,
                    mirrors: list[str] | None = None) -> list[dict]:
    """Overpass query → список ways/relations с building tag.

    Пробует mirrors по очереди (main instance часто timeout'ит).
    Возвращает list of {tags, geometry:[{lat,lon},...]}.
    """
    south, west, north, east = bbox
    query = (
        "[out:json][timeout:90];"
        f'(way["building"]({south},{west},{north},{east});'
        f' relation["building"]({south},{west},{north},{east}););'
        "out body geom;"
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    last_err: Exception | None = None
    for url in (mirrors or OVERPASS_MIRRORS):
        try:
            req = urllib.request.Request(url, data=data,
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode())
            out = []
            for el in payload.get("elements", []):
                geom = el.get("geometry")
                if not geom:
                    continue
                out.append({"tags": el.get("tags", {}), "geometry": geom,
                            "type": el.get("type"), "id": el.get("id")})
            print(f"    (via {url.split('/')[2]})")
            return out
        except Exception as e:    # noqa: BLE001 — try next mirror
            print(f"    [mirror {url.split('/')[2]} failed: {type(e).__name__}]")
            last_err = e
            continue
    raise RuntimeError(f"all Overpass mirrors failed; last: {last_err}")


def building_height(tags: dict) -> float:
    """Высота из тегов: height (м) или building:levels*3, иначе default."""
    h = tags.get("height")
    if h:
        try:
            return float(str(h).split()[0].replace(",", "."))
        except ValueError:
            pass
    lv = tags.get("building:levels")
    if lv:
        try:
            return max(1.0, float(str(lv).split()[0])) * METERS_PER_LEVEL
        except ValueError:
            pass
    return DEFAULT_HEIGHT_M


def building_material(tags: dict) -> str:
    mat = (tags.get("building:material") or tags.get("material") or "").lower()
    return MATERIAL_MAP.get(mat, "concrete")


def latlon_to_local(lat: float, lon: float,
                    origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """(lat,lon) → (north_m, east_m) от origin."""
    n = (lat - origin_lat) * 111_320.0
    e = (lon - origin_lon) * 111_320.0 * math.cos(math.radians(origin_lat))
    return n, e


def polygon_ring(geom: list[dict]) -> list[list[float]]:
    """Overpass geometry → GeoJSON ring [[lon,lat],...] (closed)."""
    ring = [[g["lon"], g["lat"]] for g in geom]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def ring_centroid_bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    """Ring [[lon,lat],...] → (centroid_lat, centroid_lon, span_n_m, span_e_m)."""
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    clat = sum(lats) / len(lats)
    clon = sum(lons) / len(lons)
    span_n = (max(lats) - min(lats)) * 111_320.0
    span_e = (max(lons) - min(lons)) * 111_320.0 * math.cos(math.radians(clat))
    return clat, clon, max(span_n, 1.0), max(span_e, 1.0)


def build_scenario(buildings: list[dict], origin_lat: float, origin_lon: float,
                   max_buildings: int = 200, terrain_grid: dict | None = None) -> list[dict]:
    """Преобразует raw OSM buildings → нормализованные obstacle records.

    Если передан terrain_grid (из terrain_elevation), каждое здание получает
    base_elevation_m (реальная высота рельефа под ним, AMSL).
    """
    elevation_at = None
    if terrain_grid is not None:
        from terrain_elevation import elevation_at as _eat
        elevation_at = _eat
    obstacles = []
    for idx, b in enumerate(buildings[:max_buildings]):
        ring = polygon_ring(b["geometry"])
        if len(ring) < 4:
            continue
        clat, clon, span_n, span_e = ring_centroid_bbox(ring)
        north, east = latlon_to_local(clat, clon, origin_lat, origin_lon)
        h = building_height(b["tags"])
        mat = building_material(b["tags"])
        name = b["tags"].get("name") or f"bld-{b.get('id', idx)}"
        rec = {
            "name": name,
            "centroid_lat": clat, "centroid_lon": clon,
            "north_m": round(north, 2), "east_m": round(east, 2),
            "span_n_m": round(span_n, 2), "span_e_m": round(span_e, 2),
            "height_m": round(h, 2),
            "material": mat,
            "ring": ring,
            "osm_id": b.get("id"),
        }
        if elevation_at is not None:
            rec["base_elevation_m"] = round(elevation_at(terrain_grid, clat, clon), 1)
        obstacles.append(rec)
    return obstacles


def emit_issgr_json(obstacles: list[dict], out_path: Path) -> None:
    """Write ИССГР-совместимый obstacles seed JSON."""
    import uuid as _uuid
    records = []
    for o in obstacles:
        records.append({
            "id": {"domain": "bas", "system": "osm-import",
                   "object_uuid": str(_uuid.uuid4())},
            "name": o["name"],
            "issgr_class": "geospatial_objects.building.generic",
            "geometry_polygon": {"type": "Polygon", "coordinates": [o["ring"]]},
            "height_m": o["height_m"],
            "material": o["material"],
            "properties": {"osm_id": o["osm_id"],
                           "local_north_m": o["north_m"],
                           "local_east_m": o["east_m"],
                           **({"base_elevation_m": o["base_elevation_m"]}
                              if "base_elevation_m" in o else {})},
        })
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def post_to_issgr(obstacles: list[dict], issgr_url: str) -> int:
    """POST каждое obstacle в /collections/obstacles/items. Returns n_ok."""
    import uuid as _uuid
    n_ok = 0
    url = issgr_url.rstrip("/") + "/collections/obstacles/items"
    for o in obstacles:
        payload = {
            "id": {"domain": "bas", "system": "osm-import",
                   "object_uuid": str(_uuid.uuid4())},
            "name": o["name"],
            "issgr_class": "geospatial_objects.building.generic",
            "geometry_polygon": {"type": "Polygon", "coordinates": [o["ring"]]},
            "height_m": o["height_m"],
            "material": o["material"],
            "properties": {"osm_id": o["osm_id"]},
        }
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5).read()
            n_ok += 1
        except Exception as e:
            print(f"  [!] POST {o['name']} failed: {e}", file=sys.stderr)
    return n_ok


def emit_gazebo_sdf(obstacles: list[dict], world_name: str, out_path: Path) -> None:
    """Write Gazebo SDF world с box-приближением каждого здания."""
    lines = [
        '<?xml version="1.0"?>',
        '<sdf version="1.7">',
        f'  <world name="{world_name}">',
        '    <include><uri>model://sun</uri></include>',
        '    <include><uri>model://ground_plane</uri></include>',
    ]
    for o in obstacles:
        # ENU: x=east, y=north, z=height/2.
        x = o["east_m"]; y = o["north_m"]; z = o["height_m"] / 2.0
        sx = o["span_e_m"]; sy = o["span_n_m"]; sz = o["height_m"]
        safe = "".join(c if c.isalnum() else "_" for c in o["name"])[:40]
        lines += [
            f'    <model name="{safe}_{o.get("osm_id", "x")}">',
            '      <static>true</static>',
            f'      <pose>{x:.2f} {y:.2f} {z:.2f} 0 0 0</pose>',
            '      <link name="link">',
            '        <collision name="collision"><geometry><box>'
            f'<size>{sx:.2f} {sy:.2f} {sz:.2f}</size></box></geometry></collision>',
            '        <visual name="visual"><geometry><box>'
            f'<size>{sx:.2f} {sy:.2f} {sz:.2f}</size></box></geometry></visual>',
            '      </link>',
            '    </model>',
        ]
    lines += ['  </world>', '</sdf>']
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--bbox", help="south,west,north,east")
    g.add_argument("--place", help="название места (Nominatim geocode)")
    p.add_argument("--radius-m", type=float, default=300.0,
                   help="радиус вокруг --place (default 300м)")
    p.add_argument("--name", default="osm_scenario", help="имя сценария")
    p.add_argument("--out-dir", type=Path,
                   default=REPO / "generated" / "osm_scenario")
    p.add_argument("--issgr-url", help="POST obstacles в ИССГР REST вместо/вместе с JSON")
    p.add_argument("--max-buildings", type=int, default=200)
    p.add_argument("--origin", help="origin lat,lon для local NED (default = bbox center)")
    p.add_argument("--with-terrain", action="store_true",
                   help="добрать реальную высоту рельефа (AWS Terrain Tiles, keyless)")
    p.add_argument("--terrain-zoom", type=int, default=12)
    args = p.parse_args()

    # Resolve bbox.
    if args.bbox:
        south, west, north, east = (float(x) for x in args.bbox.split(","))
    else:
        print(f"==> geocode '{args.place}' via Nominatim...")
        clat, clon = geocode_place(args.place)
        print(f"    center: {clat:.6f}, {clon:.6f}")
        south, west, north, east = bbox_from_center(clat, clon, args.radius_m)
    bbox = (south, west, north, east)
    print(f"==> bbox: S={south:.5f} W={west:.5f} N={north:.5f} E={east:.5f}")

    origin_lat = (south + north) / 2
    origin_lon = (west + east) / 2
    if args.origin:
        origin_lat, origin_lon = (float(x) for x in args.origin.split(","))

    print("==> querying Overpass API (building footprints)...")
    t0 = time.time()
    raw = fetch_buildings(bbox)
    print(f"    {len(raw)} buildings fetched in {time.time()-t0:.1f}s")
    if not raw:
        print("    [!] no buildings in bbox — try larger area")
        return 1

    terrain_grid = None
    terrain_stats = None
    if args.with_terrain:
        print("==> fetching terrain elevation (AWS Terrain Tiles, keyless)...")
        try:
            from terrain_elevation import fetch_elevation_grid, grid_stats, elevation_at
            terrain_grid = fetch_elevation_grid(bbox, args.terrain_zoom)
            terrain_stats = grid_stats(terrain_grid)
            og = elevation_at(terrain_grid, origin_lat, origin_lon)
            print(f"    relief {terrain_stats['min_m']}–{terrain_stats['max_m']}m, "
                  f"origin ground {og:.1f}m AMSL")
        except Exception as e:    # noqa: BLE001
            print(f"    [!] terrain fetch failed: {e} — продолжаем без рельефа")

    obstacles = build_scenario(raw, origin_lat, origin_lon, args.max_buildings,
                               terrain_grid=terrain_grid)
    print(f"==> normalized {len(obstacles)} obstacles (cap {args.max_buildings})")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    issgr_json = args.out_dir / f"{args.name}_issgr_obstacles.json"
    sdf_path = args.out_dir / f"{args.name}.sdf"
    summary_path = args.out_dir / f"{args.name}_summary.json"

    emit_issgr_json(obstacles, issgr_json)
    emit_gazebo_sdf(obstacles, args.name, sdf_path)

    heights = [o["height_m"] for o in obstacles]
    summary = {
        "name": args.name, "bbox": bbox,
        "origin_lat": origin_lat, "origin_lon": origin_lon,
        "n_buildings": len(obstacles),
        "height_min_m": round(min(heights), 1),
        "height_max_m": round(max(heights), 1),
        "height_mean_m": round(sum(heights) / len(heights), 1),
        "materials": {m: sum(1 for o in obstacles if o["material"] == m)
                      for m in set(o["material"] for o in obstacles)},
        "files": {"issgr": str(issgr_json), "sdf": str(sdf_path)},
        "data_license": "OpenStreetMap © ODbL",
        **({"terrain": terrain_stats,
            "terrain_source": "AWS Terrain Tiles (SRTM-derived, keyless)"}
           if terrain_stats else {}),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    n_posted = 0
    if args.issgr_url:
        print(f"==> POSTing {len(obstacles)} obstacles → {args.issgr_url}")
        n_posted = post_to_issgr(obstacles, args.issgr_url)
        print(f"    {n_posted}/{len(obstacles)} POSTed OK")

    print("\n==> Scenario generated:")
    print(f"    ИССГР obstacles: {issgr_json}")
    print(f"    Gazebo SDF:      {sdf_path}")
    print(f"    Summary:         {summary_path}")
    print(f"    Buildings: {len(obstacles)}  "
          f"height {summary['height_min_m']}-{summary['height_max_m']}m "
          f"(mean {summary['height_mean_m']}m)")
    print(f"    Materials: {summary['materials']}")
    if args.issgr_url:
        print(f"    POSTed to ИССГР: {n_posted}")
    print(f"    Data: OpenStreetMap © ODbL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
