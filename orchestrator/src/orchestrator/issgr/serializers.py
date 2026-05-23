"""GeoJSON serializers для ИССГР объектов.

Соответствуют RFC 7946 (GeoJSON) и OGC API Features 1.0 Feature/FeatureCollection
формату. Совместимы с QGIS, MapServer, leaflet.js, openlayers и любым другим
geo-консьюмером.
"""
from __future__ import annotations

from typing import Any, Iterable

from .models import (
    GCS, IssgrObject, Mission, Obstacle, SensorReading, UAV,
)


def to_geojson_feature(obj: IssgrObject) -> dict[str, Any]:
    """Сериализовать любой ИССГР объект в GeoJSON Feature."""
    # Выбираем geometry в зависимости от типа.
    if isinstance(obj, (UAV, GCS)):
        geometry = obj.geometry.model_dump()
    elif isinstance(obj, Obstacle):
        geometry = obj.geometry_polygon.model_dump()
    elif isinstance(obj, Mission):
        geometry = obj.geometry.model_dump()
    elif isinstance(obj, SensorReading):
        if obj.pose_at_observation is not None:
            geometry = {
                "type": "Point",
                "coordinates": [
                    obj.pose_at_observation.longitude_deg,
                    obj.pose_at_observation.latitude_deg,
                    obj.pose_at_observation.altitude_m,
                ],
            }
        else:
            geometry = None
    else:
        raise TypeError(f"unknown ISSGR object type: {type(obj).__name__}")

    # Properties = всё кроме geometry-полей + ISSGR-meta.
    props: dict[str, Any] = {
        "issgr_class": obj.issgr_class.value,
        "issgr_class_top": obj.issgr_class.top_level,
        "name": obj.name,
        "timestamp": obj.timestamp.isoformat().replace("+00:00", "Z"),
        "domain": obj.id.domain,
        "system": obj.id.system,
    }
    props.update(obj.properties)

    # Object-specific properties.
    if isinstance(obj, UAV):
        props.update({
            "object_type": "UAV",
            "sysid": obj.sysid,
            "armed": obj.armed,
            "flight_mode": obj.flight_mode,
            "battery_v": obj.battery_v,
            "velocity_ned": obj.velocity_ned,
            "altitude_m": obj.pose.altitude_m,
            "heading_deg": obj.pose.heading_deg,
        })
    elif isinstance(obj, GCS):
        props.update({
            "object_type": "GCS",
            "operator_callsign": obj.operator_callsign,
            "altitude_m": obj.pose.altitude_m,
        })
    elif isinstance(obj, Obstacle):
        props.update({
            "object_type": "Obstacle",
            "height_m": obj.height_m,
            "material": obj.material,
        })
    elif isinstance(obj, Mission):
        props.update({
            "object_type": "Mission",
            "target_uav_id": obj.target_uav_id.as_string(),
            "state": obj.state,
            "waypoint_count": len(obj.waypoints),
        })
    elif isinstance(obj, SensorReading):
        props.update({
            "object_type": "SensorReading",
            "source_uav_id": obj.source_uav_id.as_string(),
            "sensor_type": obj.sensor_type,
            "value": obj.value,
        })

    return {
        "type": "Feature",
        "id": obj.id.as_string(),
        "geometry": geometry,
        "properties": props,
    }


def to_geojson_feature_collection(
    objects: Iterable[IssgrObject],
    links: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Сериализовать collection ИССГР объектов в OGC FeatureCollection."""
    features = [to_geojson_feature(obj) for obj in objects]
    fc: dict[str, Any] = {
        "type": "FeatureCollection",
        "numberMatched": len(features),
        "numberReturned": len(features),
        "features": features,
    }
    if links:
        fc["links"] = links
    return fc
