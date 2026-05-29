"""ИССГР large-map (>20×20 km) adaptation.

Закрывает пункт ТЗ "Адаптация под карты >20×20 км" (зона
Андрончева А.Д. / Карпова А.К.) как scalable spatial primitive.

Проблема: текущая урбан-сцена покрывает ~150м × ~150м локально. Для
operational-level missions нужна работа с tiled maps размером 20×20 км
или больше — это:

  * **Sionna RT** не может ray-trace 400 км² за один pass; нужен
    per-tile cache.
  * **AirSim / Gazebo** не могут render 20×20 км в одном level;
    нужно tile streaming + LOD.
  * **ИССГР** должна давать sub-second queries "что в области X?"
    при ~100k объектов на карте; нужен spatial index.
  * **Multicast sync** должен фильтровать packets по tile (или Object
    AOI) чтобы не flood'ить ноды чужими данными.

Этот модуль закрывает **алгоритмическую часть**: TileGrid (lat/lon
↔ tile coords), BoundingTile (NED extent + objects bucket), spatial
index (bucketed без external R-tree dep). Use в виде:

  >>> grid = TileGrid(origin_lat=-35.36, origin_lon=149.165,
  ...                 tile_size_m=2000, n_tiles_north=10, n_tiles_east=10)
  >>> tile = grid.tile_at(uav_lat=-35.35, uav_lon=149.17)
  >>> for nb in grid.neighbors(tile, radius=2):
  ...     # Pre-load Sionna RT cache + AirSim assets для neighbor tiles
  ...     ...
  >>> idx = SpatialIndex(grid)
  >>> idx.insert("obstacle-123", lat=-35.35, lon=149.17, alt=10)
  >>> hits = idx.query_bbox(-35.36, 149.16, -35.35, 149.18)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Iterator


EARTH_RADIUS_M = 6_371_000.0

# ---------------------------------------------------------------------------
# WGS84 / UTM geodesy.
#
# Primary backend = **pyproj** (PROJ engine) — индустриальный стандарт,
# покрывает все зоны/датумы/edge-cases. Fallback (если pyproj не
# установлен — напр. минимальный CI) = self-contained transverse Mercator
# (Snyder 1987 series), точность ~мм в пределах UTM-зоны. Публичный API
# одинаков для обоих backend'ов; активный backend — в `UTM_BACKEND`.
# ---------------------------------------------------------------------------
_WGS84_A = 6_378_137.0                     # semi-major axis (m)
_WGS84_F = 1.0 / 298.257223563             # flattening
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)    # first eccentricity squared
_WGS84_EP2 = _WGS84_E2 / (1.0 - _WGS84_E2)  # second eccentricity squared
_UTM_K0 = 0.9996                           # UTM central-meridian scale
_UTM_FE = 500_000.0                        # false easting
_UTM_FN_S = 10_000_000.0                   # false northing (southern hemi)
_EPSG_WGS84 = 4326

try:                                       # pragma: no cover - env-dependent
    from pyproj import Transformer as _Transformer
    _HAS_PYPROJ = True
except Exception:                          # pragma: no cover
    _Transformer = None                    # type: ignore[assignment]
    _HAS_PYPROJ = False

#: Активный геодезический backend: "pyproj" (PROJ) либо "series" (Snyder).
UTM_BACKEND = "pyproj" if _HAS_PYPROJ else "series"


def utm_zone_number(lon: float) -> int:
    return int((lon + 180.0) / 6.0) % 60 + 1


def _utm_epsg(zone: int, is_northern: bool) -> int:
    """EPSG код UTM CRS: 326xx (north) / 327xx (south)."""
    return (32600 if is_northern else 32700) + zone


@lru_cache(maxsize=256)
def _fwd_transformer(zone: int, is_northern: bool):   # pragma: no cover
    """lat/lon (EPSG:4326) → UTM easting/northing. always_xy → (lon,lat)→(x,y)."""
    return _Transformer.from_crs(_EPSG_WGS84, _utm_epsg(zone, is_northern),
                                 always_xy=True)


@lru_cache(maxsize=256)
def _inv_transformer(zone: int, is_northern: bool):   # pragma: no cover
    """UTM easting/northing → lat/lon. always_xy → (x,y)→(lon,lat)."""
    return _Transformer.from_crs(_utm_epsg(zone, is_northern), _EPSG_WGS84,
                                 always_xy=True)


def latlon_to_utm(lat: float, lon: float,
                  force_zone: int | None = None) -> tuple[float, float, int, bool]:
    """WGS84 lat/lon → (easting, northing, zone, is_northern).

    pyproj (PROJ) если доступен, иначе Snyder series fallback.
    """
    zone = force_zone if force_zone is not None else utm_zone_number(lon)
    is_northern = lat >= 0
    if _HAS_PYPROJ:
        e, n = _fwd_transformer(zone, is_northern).transform(lon, lat)
        return e, n, zone, is_northern
    return _latlon_to_utm_series(lat, lon, force_zone=zone)


def utm_to_latlon(easting: float, northing: float, zone: int,
                  is_northern: bool) -> tuple[float, float]:
    """UTM → WGS84 lat/lon. pyproj если доступен, иначе Snyder inverse series."""
    if _HAS_PYPROJ:
        lon, lat = _inv_transformer(zone, is_northern).transform(easting, northing)
        return lat, lon
    return _utm_to_latlon_series(easting, northing, zone, is_northern)


def _latlon_to_utm_series(lat: float, lon: float,
                          force_zone: int | None = None) -> tuple[float, float, int, bool]:
    """Snyder 1987 forward transverse Mercator (pure-python fallback)."""
    zone = force_zone if force_zone is not None else utm_zone_number(lon)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)   # central meridian
    phi = math.radians(lat)
    lam = math.radians(lon)
    e2, ep2, a, k0 = _WGS84_E2, _WGS84_EP2, _WGS84_A, _UTM_K0
    sin_p, cos_p, tan_p = math.sin(phi), math.cos(phi), math.tan(phi)
    N = a / math.sqrt(1 - e2 * sin_p * sin_p)
    T = tan_p * tan_p
    C = ep2 * cos_p * cos_p
    A = (lam - lon0) * cos_p
    M = a * (
        (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * phi
        - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*phi)
        + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*phi)
        - (35*e2**3/3072) * math.sin(6*phi)
    )
    easting = (k0 * N * (A + (1 - T + C) * A**3 / 6
               + (5 - 18*T + T*T + 72*C - 58*ep2) * A**5 / 120) + _UTM_FE)
    northing = (k0 * (M + N * tan_p * (A*A/2
                + (5 - T + 9*C + 4*C*C) * A**4 / 24
                + (61 - 58*T + T*T + 600*C - 330*ep2) * A**6 / 720)))
    is_northern = lat >= 0
    if not is_northern:
        northing += _UTM_FN_S
    return easting, northing, zone, is_northern


def _utm_to_latlon_series(easting: float, northing: float, zone: int,
                          is_northern: bool) -> tuple[float, float]:
    """Snyder 1987 inverse transverse Mercator (pure-python fallback)."""
    e2, ep2, a, k0 = _WGS84_E2, _WGS84_EP2, _WGS84_A, _UTM_K0
    x = easting - _UTM_FE
    y = northing if is_northern else northing - _UTM_FN_S
    M = y / k0
    mu = M / (a * (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3*e1/2 - 27*e1**3/32) * math.sin(2*mu)
            + (21*e1**2/16 - 55*e1**4/32) * math.sin(4*mu)
            + (151*e1**3/96) * math.sin(6*mu)
            + (1097*e1**4/512) * math.sin(8*mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    C1 = ep2 * cos1 * cos1
    T1 = tan1 * tan1
    N1 = a / math.sqrt(1 - e2 * sin1 * sin1)
    R1 = a * (1 - e2) / (1 - e2 * sin1 * sin1) ** 1.5
    D = x / (N1 * k0)
    phi = (phi1 - (N1 * tan1 / R1) * (D*D/2
           - (5 + 3*T1 + 10*C1 - 4*C1*C1 - 9*ep2) * D**4 / 24
           + (61 + 90*T1 + 298*C1 + 45*T1*T1 - 252*ep2 - 3*C1*C1) * D**6 / 720))
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    lam = lon0 + (D - (1 + 2*T1 + C1) * D**3 / 6
                  + (5 - 2*C1 + 28*T1 - 3*C1*C1 + 8*ep2 + 24*T1*T1)
                  * D**5 / 120) / cos1
    return math.degrees(phi), math.degrees(lam)


# ---------------------------------------------------------------------------
# Geo helpers — local NED via UTM (production-accurate) с flat-earth fallback
# ---------------------------------------------------------------------------
def latlon_to_local_ned(
    lat: float, lon: float, origin_lat: float, origin_lon: float,
) -> tuple[float, float]:
    """WGS84 lat/lon → local NED (north_m, east_m) от origin через UTM.

    Точность ~см в пределах UTM-зоны origin (≈668 км). Обе точки проецируются
    в **зону и полушарие origin** (общий CRS), поэтому false-northing/easting
    сокращаются при вычитании — корректно даже при пересечении экватора, в
    отличие от старого flat-earth, накапливавшего ошибку с дистанцией.
    """
    zone = utm_zone_number(origin_lon)
    is_north = origin_lat >= 0.0
    if _HAS_PYPROJ:
        tr = _fwd_transformer(zone, is_north)
        e0, n0 = tr.transform(origin_lon, origin_lat)
        e1, n1 = tr.transform(lon, lat)
        return (n1 - n0), (e1 - e0)
    e0, n0, _, _ = _latlon_to_utm_series(origin_lat, origin_lon, force_zone=zone)
    e1, n1, _, _ = _latlon_to_utm_series(lat, lon, force_zone=zone)
    return (n1 - n0), (e1 - e0)


def local_ned_to_latlon(
    x_north: float, y_east: float,
    origin_lat: float, origin_lon: float,
) -> tuple[float, float]:
    """Local NED → WGS84 lat/lon через UTM (inverse of latlon_to_local_ned)."""
    zone = utm_zone_number(origin_lon)
    is_north = origin_lat >= 0.0
    if _HAS_PYPROJ:
        e0, n0 = _fwd_transformer(zone, is_north).transform(origin_lon, origin_lat)
        lon, lat = _inv_transformer(zone, is_north).transform(e0 + y_east, n0 + x_north)
        return lat, lon
    e0, n0, z, north = _latlon_to_utm_series(origin_lat, origin_lon, force_zone=zone)
    return _utm_to_latlon_series(e0 + y_east, n0 + x_north, z, north)


def latlon_to_local_ned_flat(
    lat: float, lon: float, origin_lat: float, origin_lon: float,
) -> tuple[float, float]:
    """Flat-earth approximation (legacy). Точность ±1м в радиусе 50 км."""
    dlat = math.radians(lat - origin_lat)
    dlon = math.radians(lon - origin_lon)
    x_north = EARTH_RADIUS_M * dlat
    y_east = EARTH_RADIUS_M * dlon * math.cos(math.radians(origin_lat))
    return x_north, y_east


def local_ned_to_latlon_flat(
    x_north: float, y_east: float,
    origin_lat: float, origin_lon: float,
) -> tuple[float, float]:
    """Flat-earth inverse (legacy)."""
    lat = origin_lat + math.degrees(x_north / EARTH_RADIUS_M)
    lon = origin_lon + math.degrees(
        y_east / (EARTH_RADIUS_M * math.cos(math.radians(origin_lat)))
    )
    return lat, lon


# ---------------------------------------------------------------------------
# Tile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TileId:
    """Tile ID — 2D индекс (i_north, j_east) от origin (0,0)."""
    i: int   # index along north axis
    j: int   # index along east axis

    def as_str(self) -> str:
        return f"T_{self.i:+04d}_{self.j:+04d}"


@dataclass(frozen=True)
class TileBounds:
    """Bbox tile'а в local NED + lat/lon."""
    tile: TileId
    n_min_m: float
    n_max_m: float
    e_min_m: float
    e_max_m: float
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @property
    def center_lat(self) -> float:
        return (self.lat_min + self.lat_max) / 2.0

    @property
    def center_lon(self) -> float:
        return (self.lon_min + self.lon_max) / 2.0

    @property
    def width_m(self) -> float:
        return self.e_max_m - self.e_min_m

    @property
    def height_m(self) -> float:
        return self.n_max_m - self.n_min_m

    def contains_latlon(self, lat: float, lon: float) -> bool:
        return (self.lat_min <= lat <= self.lat_max
                and self.lon_min <= lon <= self.lon_max)

    def to_geojson_polygon(self) -> dict:
        return {
            "type": "Polygon",
            "coordinates": [[
                [self.lon_min, self.lat_min],
                [self.lon_max, self.lat_min],
                [self.lon_max, self.lat_max],
                [self.lon_min, self.lat_max],
                [self.lon_min, self.lat_min],
            ]],
        }


# ---------------------------------------------------------------------------
# TileGrid
# ---------------------------------------------------------------------------
class TileGrid:
    """Equal-size square tile grid в local NED, anchored к geo origin.

    Tile (0,0) — соседний с origin'ом (его SW corner = origin). Positive
    i = на север, positive j = на восток. Negative — на юг/запад.
    """

    def __init__(
        self,
        origin_lat: float,
        origin_lon: float,
        tile_size_m: float = 2000.0,
        n_tiles_north: int = 10,
        n_tiles_east: int = 10,
    ) -> None:
        if tile_size_m <= 0:
            raise ValueError("tile_size_m must be > 0")
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.tile_size_m = tile_size_m
        self.n_tiles_north = n_tiles_north
        self.n_tiles_east = n_tiles_east

    @property
    def total_area_km2(self) -> float:
        return (self.tile_size_m * self.n_tiles_north
                * self.tile_size_m * self.n_tiles_east) / 1e6

    @property
    def coverage_north_km(self) -> float:
        return self.tile_size_m * self.n_tiles_north / 1000.0

    @property
    def coverage_east_km(self) -> float:
        return self.tile_size_m * self.n_tiles_east / 1000.0

    @property
    def total_tiles(self) -> int:
        return self.n_tiles_north * self.n_tiles_east

    def tile_at(self, lat: float, lon: float) -> TileId:
        """Который tile содержит данную точку."""
        n, e = latlon_to_local_ned(lat, lon, self.origin_lat, self.origin_lon)
        i = int(math.floor(n / self.tile_size_m))
        j = int(math.floor(e / self.tile_size_m))
        return TileId(i=i, j=j)

    def bounds(self, tile: TileId) -> TileBounds:
        n_min = tile.i * self.tile_size_m
        n_max = n_min + self.tile_size_m
        e_min = tile.j * self.tile_size_m
        e_max = e_min + self.tile_size_m
        lat_min, lon_min = local_ned_to_latlon(
            n_min, e_min, self.origin_lat, self.origin_lon)
        lat_max, lon_max = local_ned_to_latlon(
            n_max, e_max, self.origin_lat, self.origin_lon)
        return TileBounds(
            tile=tile, n_min_m=n_min, n_max_m=n_max,
            e_min_m=e_min, e_max_m=e_max,
            lat_min=lat_min, lat_max=lat_max,
            lon_min=lon_min, lon_max=lon_max,
        )

    def neighbors(self, tile: TileId, radius: int = 1) -> list[TileId]:
        """Все tile'ы внутри chebyshev radius от данного (включая сам)."""
        out: list[TileId] = []
        for di in range(-radius, radius + 1):
            for dj in range(-radius, radius + 1):
                out.append(TileId(i=tile.i + di, j=tile.j + dj))
        return out

    def iter_all(self) -> Iterator[TileId]:
        """Все tile'ы within configured (n_tiles_north × n_tiles_east) area."""
        for i in range(self.n_tiles_north):
            for j in range(self.n_tiles_east):
                yield TileId(i=i, j=j)

    def tiles_in_bbox(
        self, lat_min: float, lon_min: float,
        lat_max: float, lon_max: float,
    ) -> list[TileId]:
        """Все tile'ы пересекающие данный lat/lon bbox."""
        t_sw = self.tile_at(lat_min, lon_min)
        t_ne = self.tile_at(lat_max, lon_max)
        out: list[TileId] = []
        for i in range(min(t_sw.i, t_ne.i), max(t_sw.i, t_ne.i) + 1):
            for j in range(min(t_sw.j, t_ne.j), max(t_sw.j, t_ne.j) + 1):
                out.append(TileId(i=i, j=j))
        return out


# ---------------------------------------------------------------------------
# Spatial index — bucketed по tile
# ---------------------------------------------------------------------------
@dataclass
class IndexEntry:
    object_id: str
    lat: float
    lon: float
    alt: float = 0.0
    metadata: dict = field(default_factory=dict)


class SpatialIndex:
    """Bucketed spatial index анкеренный на TileGrid.

    Каждый object приклеплён к exactly one tile (по lat/lon). Queries
    идут только по tile'ам пересекающим bbox — O(tile_count_in_bbox)
    instead of O(total_objects).

    Не использует external R-tree — для prototype достаточно bucketing.
    На ~100k объектов в 10×10 grid: per-tile ~1000 records, sub-ms query.
    """

    def __init__(self, grid: TileGrid) -> None:
        self.grid = grid
        self._buckets: dict[TileId, list[IndexEntry]] = {}
        self._object_lookup: dict[str, TileId] = {}

    def insert(self, object_id: str, lat: float, lon: float,
               alt: float = 0.0, metadata: dict | None = None) -> TileId:
        tile = self.grid.tile_at(lat, lon)
        # Idempotent — remove если уже был.
        if object_id in self._object_lookup:
            self.remove(object_id)
        entry = IndexEntry(
            object_id=object_id, lat=lat, lon=lon, alt=alt,
            metadata=metadata or {},
        )
        self._buckets.setdefault(tile, []).append(entry)
        self._object_lookup[object_id] = tile
        return tile

    def remove(self, object_id: str) -> bool:
        tile = self._object_lookup.pop(object_id, None)
        if tile is None:
            return False
        bucket = self._buckets.get(tile, [])
        before = len(bucket)
        bucket[:] = [e for e in bucket if e.object_id != object_id]
        if not bucket:
            self._buckets.pop(tile, None)
        return len(bucket) < before

    def query_tile(self, tile: TileId) -> list[IndexEntry]:
        return list(self._buckets.get(tile, []))

    def query_bbox(
        self, lat_min: float, lon_min: float,
        lat_max: float, lon_max: float,
    ) -> list[IndexEntry]:
        out: list[IndexEntry] = []
        for tile in self.grid.tiles_in_bbox(lat_min, lon_min, lat_max, lon_max):
            for e in self._buckets.get(tile, []):
                if lat_min <= e.lat <= lat_max and lon_min <= e.lon <= lon_max:
                    out.append(e)
        return out

    def query_neighborhood(
        self, lat: float, lon: float, radius_tiles: int = 1,
    ) -> list[IndexEntry]:
        center = self.grid.tile_at(lat, lon)
        out: list[IndexEntry] = []
        for tile in self.grid.neighbors(center, radius=radius_tiles):
            out.extend(self._buckets.get(tile, []))
        return out

    @property
    def total_objects(self) -> int:
        return sum(len(b) for b in self._buckets.values())

    @property
    def occupied_tiles(self) -> int:
        return len(self._buckets)

    def stats(self) -> dict:
        if not self._buckets:
            return {"total_objects": 0, "occupied_tiles": 0,
                    "max_bucket": 0, "min_bucket": 0, "avg_bucket": 0.0}
        sizes = [len(b) for b in self._buckets.values()]
        return {
            "total_objects": sum(sizes),
            "occupied_tiles": len(sizes),
            "max_bucket": max(sizes),
            "min_bucket": min(sizes),
            "avg_bucket": sum(sizes) / len(sizes),
        }


# ---------------------------------------------------------------------------
# Sionna RT cache key generator
# ---------------------------------------------------------------------------
def sionna_cache_key(tile: TileId, freq_mhz: float, antenna_height_m: float) -> str:
    """Deterministic key для cached Sionna coverage map per (tile, freq).

    На 100×100 km area с 1 km tiles + 5 frequencies + 3 antenna heights
    = 100*100*5*3 = 150k entries. Pre-compute offline, runtime — O(1)
    cache hit per UAV position lookup.
    """
    return f"sionna/{tile.as_str()}/f{int(freq_mhz)}_h{int(antenna_height_m * 10)}"


# ---------------------------------------------------------------------------
# AirSim/Gazebo tile loading helper
# ---------------------------------------------------------------------------
def tiles_to_preload(
    grid: TileGrid, uav_lat: float, uav_lon: float,
    radius_tiles: int = 1,
) -> list[TileId]:
    """Какие tile'ы должны быть hot-loaded в AirSim / Gazebo для UAV
    в данной позиции. Соседство rad=1 → 9 tiles (3x3), rad=2 → 25, и т.д.
    """
    center = grid.tile_at(uav_lat, uav_lon)
    return grid.neighbors(center, radius=radius_tiles)


__all__ = [
    "EARTH_RADIUS_M",
    "latlon_to_local_ned", "local_ned_to_latlon",
    "latlon_to_local_ned_flat", "local_ned_to_latlon_flat",
    "utm_zone_number", "latlon_to_utm", "utm_to_latlon",
    "TileId", "TileBounds", "TileGrid",
    "IndexEntry", "SpatialIndex",
    "sionna_cache_key", "tiles_to_preload",
]
