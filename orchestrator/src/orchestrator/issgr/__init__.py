"""ИССГР — объектно-ориентированная геопространственная база данных + REST/OGC API.

Реализует подмножество требований из `docs/Краткая_выдержка_актуального_из_гранта_БАС.docx`:

  * Объектная модель: уникальные идентификаторы (domain/system/object UUID),
    геометрия (Point/Polygon/LineString), временные метки, классификатор.
  * Унифицированный классификатор: top-level классы geospatial_objects /
    operational_situation / functional_objects.
  * Бортовая часть (UAV + sensor readings) — обновляется live из
    orchestrator events.jsonl.
  * Наземная часть (GCS, obstacles, mission targets) — статичная либо
    POST-able через REST API.
  * Цифровой двойник группы БАС — GeoJSON FeatureCollection /collections.
  * REST/OGC API Features endpoints для имитаторов АСУ.

Этот модуль — **зона Андрончева А.Д. / Карпова А.К. / Маргарян А.Г.** по
ТЗ распределения задач, но интерфейс положен здесь как контрактный артефакт
для последующей передачи.
"""
from .classifier import IssgrClass
from .models import (
    BBox, GCS, Geometry, LineStringGeometry, Mission, Obstacle,
    ObjectIdentifier, PointGeometry, PolygonGeometry, Pose,
    SensorReading, UAV, Waypoint,
)
from .large_map import (
    EARTH_RADIUS_M, IndexEntry, SpatialIndex, TileBounds, TileGrid,
    TileId, latlon_to_local_ned, local_ned_to_latlon,
    sionna_cache_key, tiles_to_preload,
)
from .onboard import CompositeMetrics, OnBoardDB
from .repository import IssgrRepository
from .serializers import to_geojson_feature, to_geojson_feature_collection
from .sync import (
    AutoPublisher, L1Packet, L2Packet, MulticastPublisher,
    MulticastSubscriber, decode_packet, encode_heartbeat,
    encode_position_l1, encode_sensor_l2,
    DEFAULT_MULTICAST_GROUP, DEFAULT_MULTICAST_PORT, PACKET_L1_SIZE,
    PACKET_L2_SIZE,
)

__all__ = [
    "BBox", "GCS", "Geometry", "IssgrClass", "IssgrRepository",
    "LineStringGeometry", "Mission", "ObjectIdentifier", "Obstacle",
    "PointGeometry", "PolygonGeometry", "Pose", "SensorReading", "UAV",
    "Waypoint", "to_geojson_feature", "to_geojson_feature_collection",
    # On-board persistence
    "CompositeMetrics", "OnBoardDB",
    # Large-map adaptation (>20×20 km)
    "EARTH_RADIUS_M", "IndexEntry", "SpatialIndex", "TileBounds", "TileGrid",
    "TileId", "latlon_to_local_ned", "local_ned_to_latlon",
    "sionna_cache_key", "tiles_to_preload",
    # Multicast sync
    "AutoPublisher", "L1Packet", "L2Packet", "MulticastPublisher",
    "MulticastSubscriber", "decode_packet", "encode_heartbeat",
    "encode_position_l1", "encode_sensor_l2",
    "DEFAULT_MULTICAST_GROUP", "DEFAULT_MULTICAST_PORT",
    "PACKET_L1_SIZE", "PACKET_L2_SIZE",
]
