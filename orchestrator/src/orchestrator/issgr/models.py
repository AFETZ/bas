"""ИССГР объектная модель.

Все объекты имеют:
  * `id` — ObjectIdentifier (domain/system/object UUID per ТЗ)
  * `issgr_class` — IssgrClass (унифицированный классификатор)
  * `geometry` — Geometry (GeoJSON Point/Polygon/LineString)
  * `pose` (опционально) — orientation + altitude
  * `properties` — domain-specific атрибуты
  * `timestamp` — момент последнего обновления (UTC)
  * `domain` / `system` — namespace из ИССГР identifier model

Все pydantic — авто-JSON schema через `model.model_json_schema()`.
"""
from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .classifier import IssgrClass


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


# -----------------------------------------------------------------------------
# Identifiers
# -----------------------------------------------------------------------------
class ObjectIdentifier(BaseModel):
    """Идентификатор ИССГР: domain + system + object (UUID).

    Pattern: `<domain>:<system>:<uuid>`. Например `bas:fizulin-rig:550e8400-...`.
    Позволяет гарантировать глобальную уникальность даже при merge
    нескольких ИССГР-инстансов.
    """
    domain: str = Field(default="bas", description="Top-level домен (bas, asw, ...)")
    system: str = Field(default="fizulin-rig", description="Источник данных")
    object_uuid: _uuid.UUID = Field(default_factory=_uuid.uuid4)

    model_config = ConfigDict(frozen=True)

    def as_string(self) -> str:
        return f"{self.domain}:{self.system}:{self.object_uuid}"

    @classmethod
    def parse(cls, s: str) -> "ObjectIdentifier":
        parts = s.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid ObjectIdentifier: {s}")
        return cls(domain=parts[0], system=parts[1], object_uuid=_uuid.UUID(parts[2]))


# -----------------------------------------------------------------------------
# Geometry — GeoJSON-compatible
# -----------------------------------------------------------------------------
class _GeometryBase(BaseModel):
    """Базовый GeoJSON geometry без discriminator-overhead."""
    type: str
    coordinates: list[Any]


class PointGeometry(_GeometryBase):
    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(
        ..., min_length=2, max_length=3,
        description="[longitude, latitude] или [lon, lat, alt_m]",
    )


class PolygonGeometry(_GeometryBase):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[list[float]]] = Field(
        ..., description="GeoJSON Polygon: [[ring], [hole], ...]",
    )


class LineStringGeometry(_GeometryBase):
    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]] = Field(
        ..., min_length=2, description="Sequence [lon,lat] точек",
    )


Geometry = Annotated[
    Union[PointGeometry, PolygonGeometry, LineStringGeometry],
    Field(discriminator="type"),
]


class BBox(BaseModel):
    """Bounding box [west, south, east, north] (опционально с min/max alt)."""
    west: float
    south: float
    east: float
    north: float
    min_alt: float | None = None
    max_alt: float | None = None

    def as_list(self) -> list[float]:
        if self.min_alt is not None and self.max_alt is not None:
            return [self.west, self.south, self.min_alt,
                    self.east, self.north, self.max_alt]
        return [self.west, self.south, self.east, self.north]


# -----------------------------------------------------------------------------
# Pose
# -----------------------------------------------------------------------------
class Pose(BaseModel):
    """6DOF поза: lat/lon/alt + quaternion orientation."""
    latitude_deg: float = Field(..., ge=-90.0, le=90.0)
    longitude_deg: float = Field(..., ge=-180.0, le=180.0)
    altitude_m: float = Field(default=0.0, description="AGL или AMSL — см. issgr_class")
    qw: float = Field(default=1.0)
    qx: float = Field(default=0.0)
    qy: float = Field(default=0.0)
    qz: float = Field(default=0.0)
    heading_deg: float | None = Field(
        default=None, ge=0.0, le=360.0,
        description="Если задан — derived из quaternion для удобства",
    )


# -----------------------------------------------------------------------------
# Core objects — UAV / Obstacle / GCS / SensorReading / Mission
# -----------------------------------------------------------------------------
class _IssgrObjectBase(BaseModel):
    """Общий базовый класс для всех ИССГР объектов."""
    id: ObjectIdentifier = Field(default_factory=ObjectIdentifier)
    issgr_class: IssgrClass
    name: str
    timestamp: _dt.datetime = Field(default_factory=_utcnow)
    properties: dict[str, Any] = Field(default_factory=dict)


class UAV(_IssgrObjectBase):
    """Беспилотное авиационное средство — operational_situation.uav.*"""
    issgr_class: IssgrClass = IssgrClass.OPS_UAV_ROTARY
    sysid: int = Field(..., ge=1, le=255, description="MAVLink SYSID_THISMAV")
    pose: Pose
    armed: bool = False
    flight_mode: str = "UNKNOWN"
    battery_v: float | None = None
    velocity_ned: list[float] | None = Field(
        default=None, min_length=3, max_length=3,
        description="[vx_north, vy_east, vz_down] m/s",
    )

    @property
    def geometry(self) -> PointGeometry:
        return PointGeometry(coordinates=[
            self.pose.longitude_deg, self.pose.latitude_deg, self.pose.altitude_m,
        ])


class Obstacle(_IssgrObjectBase):
    """Геопространственное препятствие: здание, башня, мачта."""
    issgr_class: IssgrClass = IssgrClass.GEO_BUILDING
    geometry_polygon: PolygonGeometry
    height_m: float = Field(..., gt=0.0)
    material: str = Field(
        default="generic",
        description="metal/concrete/brick/wood (см. RF_OBSTACLE_MATERIAL_TO_CLASS)",
    )


class GCS(_IssgrObjectBase):
    """Наземный пункт управления — functional_objects.gcs.*"""
    issgr_class: IssgrClass = IssgrClass.FUNC_GCS
    pose: Pose
    operator_callsign: str | None = None

    @property
    def geometry(self) -> PointGeometry:
        return PointGeometry(coordinates=[
            self.pose.longitude_deg, self.pose.latitude_deg, self.pose.altitude_m,
        ])


class SensorReading(_IssgrObjectBase):
    """Бортовое сенсорное наблюдение: RSSI, LiDAR distance, etc.

    Tied к UAV через `source_uav_id`. Сохраняется в repository как
    historical time-series.
    """
    issgr_class: IssgrClass = IssgrClass.FUNC_SENSOR_STATION
    source_uav_id: ObjectIdentifier
    sensor_type: str = Field(..., description="rssi / lidar / camera_object / ...")
    value: dict[str, Any] = Field(..., description="Sensor-specific payload")
    pose_at_observation: Pose | None = None


class Waypoint(BaseModel):
    """Точка миссии."""
    seq: int = Field(..., ge=0)
    action: str = Field(..., description="takeoff / waypoint / land")
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    altitude_m: float | None = None
    local_north_m: float | None = None
    local_east_m: float | None = None
    speed_mps: float | None = None


class Mission(_IssgrObjectBase):
    """Миссия — операционная situация waypoint_route.

    Идентифицируется как ИССГР объект, geometry = LineString из waypoints.
    """
    issgr_class: IssgrClass = IssgrClass.OPS_MISSION
    target_uav_id: ObjectIdentifier
    waypoints: list[Waypoint] = Field(..., min_length=1)
    state: str = Field(
        default="pending",
        description="pending / uploading / running / completed / aborted",
    )

    @property
    def geometry(self) -> LineStringGeometry:
        coords: list[list[float]] = []
        for wp in self.waypoints:
            if wp.longitude_deg is not None and wp.latitude_deg is not None:
                if wp.altitude_m is not None:
                    coords.append([wp.longitude_deg, wp.latitude_deg, wp.altitude_m])
                else:
                    coords.append([wp.longitude_deg, wp.latitude_deg])
        if len(coords) < 2:
            # Fallback — degenerate line через origin.
            coords = [[149.165237, -35.363262], [149.165237, -35.363262]]
        return LineStringGeometry(coordinates=coords)


# Union для type-hint всех ИССГР объектов.
IssgrObject = Union[UAV, Obstacle, GCS, SensorReading, Mission]
