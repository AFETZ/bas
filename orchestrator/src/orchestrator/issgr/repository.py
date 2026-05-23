"""In-memory + JSONL-backed repository ИССГР объектов.

Thread-safe (RLock). По-умолчанию объекты живут только в RAM, опциональная
persistence через JSONL snapshot (`/tmp/issgr_state.jsonl`) для warm-restart.

Source of truth:
  * Static objects (Obstacles, GCS) — POST через REST API либо loaded from
    `configs/issgr_seed.json` при старте.
  * Dynamic objects (UAV, SensorReading, Mission) — обновляются из
    orchestrator events.jsonl tailer'ом.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .classifier import IssgrClass
from .models import (
    BBox, GCS, IssgrObject, Mission, Obstacle, ObjectIdentifier,
    SensorReading, UAV,
)


# Mapping collection_id → ИССГР object type. Соответствует OGC API Features
# `/collections/{collectionId}` endpoints.
COLLECTIONS: dict[str, type] = {
    "uavs": UAV,
    "obstacles": Obstacle,
    "gcs": GCS,
    "missions": Mission,
    "sensor_readings": SensorReading,
}

COLLECTION_TITLES: dict[str, str] = {
    "uavs": "UAVs (operational_situation.uav.*)",
    "obstacles": "Obstacles (geospatial_objects.*)",
    "gcs": "Ground Control Stations (functional_objects.gcs.*)",
    "missions": "Missions (operational_situation.mission.waypoint_route)",
    "sensor_readings": "Sensor readings (time-series)",
}


class IssgrRepository:
    """Threaded in-memory repo для всех ИССГР объектов."""

    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._objects: dict[str, dict[str, IssgrObject]] = {
            cid: {} for cid in COLLECTIONS
        }
        self._persist_path = persist_path
        if persist_path is not None and persist_path.exists():
            self._load_snapshot()

    # ---- CRUD ---------------------------------------------------------------
    def upsert(self, collection_id: str, obj: IssgrObject) -> str:
        if collection_id not in COLLECTIONS:
            raise KeyError(f"unknown collection: {collection_id}")
        expected = COLLECTIONS[collection_id]
        if not isinstance(obj, expected):
            raise TypeError(
                f"collection {collection_id} expects {expected.__name__}, "
                f"got {type(obj).__name__}"
            )
        with self._lock:
            oid = obj.id.as_string()
            self._objects[collection_id][oid] = obj
            self._persist_async()
        return oid

    def get(self, collection_id: str, object_id: str) -> IssgrObject | None:
        with self._lock:
            return self._objects.get(collection_id, {}).get(object_id)

    def delete(self, collection_id: str, object_id: str) -> bool:
        with self._lock:
            removed = self._objects[collection_id].pop(object_id, None) is not None
            if removed:
                self._persist_async()
            return removed

    def list_collection(
        self,
        collection_id: str,
        bbox: BBox | None = None,
        issgr_class_filter: IssgrClass | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IssgrObject]:
        with self._lock:
            objs = list(self._objects.get(collection_id, {}).values())
        if issgr_class_filter:
            objs = [o for o in objs if o.issgr_class == issgr_class_filter]
        if bbox is not None:
            objs = [o for o in objs if _bbox_intersects(o, bbox)]
        # Sort: stable by timestamp desc — newer first.
        objs.sort(key=lambda o: o.timestamp, reverse=True)
        return objs[offset : offset + limit]

    def collections(self) -> list[str]:
        return list(COLLECTIONS.keys())

    def iter_all(self) -> Iterator[tuple[str, IssgrObject]]:
        """Для snapshot/export — все объекты со всех collections."""
        with self._lock:
            for cid, store in self._objects.items():
                for obj in store.values():
                    yield cid, obj

    # ---- Stats --------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "collections": {
                    cid: len(store) for cid, store in self._objects.items()
                },
                "total_objects": sum(
                    len(store) for store in self._objects.values()
                ),
                "as_of": datetime.utcnow().isoformat() + "Z",
            }

    # ---- Persistence --------------------------------------------------------
    def _persist_async(self) -> None:
        # Naive sync write — для production надо background queue. У нас
        # ISSGR small (десятки объектов), приемлемо.
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._persist_path.open("w", encoding="utf-8") as f:
                for cid, obj in self.iter_all():
                    record = {
                        "collection": cid,
                        "model_type": type(obj).__name__,
                        "data": obj.model_dump(mode="json"),
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass   # Не падать на persistence error

    def _load_snapshot(self) -> None:
        assert self._persist_path is not None
        type_map = {cls.__name__: cls for cls in COLLECTIONS.values()}
        loaded = 0
        for line in self._persist_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                cls = type_map[rec["model_type"]]
                obj = cls(**rec["data"])
                self._objects[rec["collection"]][obj.id.as_string()] = obj
                loaded += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        print(f"[issgr] loaded {loaded} objects from {self._persist_path}",
              flush=True)


def _bbox_intersects(obj: IssgrObject, bbox: BBox) -> bool:
    """Простая bbox-проверка через geometry centroid."""
    # Получаем lon/lat.
    if isinstance(obj, (UAV, GCS)):
        lon, lat = obj.pose.longitude_deg, obj.pose.latitude_deg
    elif isinstance(obj, Obstacle):
        # Centroid of first ring.
        ring = obj.geometry_polygon.coordinates[0]
        if not ring:
            return False
        lon = sum(p[0] for p in ring) / len(ring)
        lat = sum(p[1] for p in ring) / len(ring)
    elif isinstance(obj, Mission):
        if not obj.waypoints:
            return False
        wp = obj.waypoints[0]
        if wp.longitude_deg is None or wp.latitude_deg is None:
            return False
        lon, lat = wp.longitude_deg, wp.latitude_deg
    elif isinstance(obj, SensorReading) and obj.pose_at_observation is not None:
        lon, lat = (obj.pose_at_observation.longitude_deg,
                    obj.pose_at_observation.latitude_deg)
    else:
        return True   # Без geometry — пусть всегда видим
    return (bbox.west <= lon <= bbox.east) and (bbox.south <= lat <= bbox.north)
