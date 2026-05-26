"""FastAPI приложение с OGC API Features 1.0 endpoints.

Минимальный compliant subset:
  * GET /                  — landing page (links to all resources)
  * GET /conformance        — список conformance classes
  * GET /api                — OpenAPI 3.0 schema (auto-generated FastAPI)
  * GET /collections        — список collections
  * GET /collections/{c}    — описание collection
  * GET /collections/{c}/items  — GeoJSON FeatureCollection (query params:
                                  bbox, limit, offset, issgr_class)
  * GET /collections/{c}/items/{id}  — single GeoJSON Feature
  * POST /collections/{c}/items  — добавить объект (для имитаторов АСУ)
  * DELETE /collections/{c}/items/{id}

Дополнительно (не OGC, BAS-specific):
  * GET /stats              — счётчики по collections
  * GET /digital_twin       — единая GeoJSON FeatureCollection всех ИССГР объектов
  * GET /schema             — JSON Schema всех моделей
  * GET /classifier         — список classifier values

Совместим с QGIS, leaflet.js, openlayers, любыми OGC API Features клиентами.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from .classifier import IssgrClass
from .models import (
    BBox, GCS, IssgrObject, Mission, Obstacle, SensorReading, UAV,
)
from .repository import COLLECTION_TITLES, COLLECTIONS, IssgrRepository
from .serializers import to_geojson_feature, to_geojson_feature_collection


log = logging.getLogger("issgr.api")


def create_app(
    repo: IssgrRepository | None = None,
    title: str = "BAS Prototype — ИССГР API",
    description: str = (
        "OGC API Features 1.0 для имитаторов АСУ и внешних клиентов.\n\n"
        "Назначение: хранить и отдавать цифровой двойник стенда BAS: БАС, "
        "препятствия, НПУ, missions и sensor readings в GeoJSON-compatible "
        "формате.\n\n"
        "Важно: это API данных ИССГР, а не пульт управления полётом. "
        "Команды БАС отправляются через Web GCS/MAVProxy/QGroundControl; "
        "этот API нужен для внешних клиентов, синхронизации БД, карт и "
        "интеграции с ИССГР."
    ),
) -> FastAPI:
    """Создать FastAPI app, optionally с существующим repository."""
    app = FastAPI(
        title=title,
        description=description,
        version="0.1.0",
        contact={
            "name": "BAS Prototype",
            "url": "https://github.com/AFETZ/bas",
        },
        openapi_tags=[
            {"name": "Discovery", "description": "OGC service discovery and conformance."},
            {"name": "Collections", "description": "Read GeoJSON FeatureCollections from ИССГР."},
            {"name": "Write", "description": "Create/update objects from ASU simulators and CV pipeline."},
            {"name": "Digital twin", "description": "Unified operational picture across all collections."},
            {"name": "Schema", "description": "Classifier and JSON schema reference."},
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    state_repo = repo or IssgrRepository()
    app.state.repo = state_repo

    # -------------------------------------------------------------------- OGC
    @app.get("/", include_in_schema=False)
    def landing() -> dict[str, Any]:
        return {
            "title": title,
            "description": description,
            "links": [
                {"href": "/", "rel": "self", "type": "application/json"},
                {"href": "/conformance", "rel": "conformance"},
                {"href": "/api", "rel": "service-desc",
                 "type": "application/vnd.oai.openapi+json;version=3.0"},
                {"href": "/openapi.json", "rel": "service-desc"},
                {"href": "/collections", "rel": "data"},
                {"href": "/digital_twin", "rel": "alternate", "title": "Цифровой двойник"},
                {"href": "/stats", "rel": "stats"},
                {"href": "/schema", "rel": "describedby"},
                {"href": "/classifier", "rel": "classifier"},
            ],
        }

    @app.get("/conformance", summary="OGC API Features conformance classes",
             tags=["Discovery"])
    def conformance() -> dict[str, list[str]]:
        return {
            "conformsTo": [
                "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
                "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
                "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
                "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/json",
            ],
        }

    @app.get("/api", include_in_schema=False)
    def api_redirect() -> RedirectResponse:
        return RedirectResponse(url="/openapi.json")

    @app.get("/collections", summary="Список ИССГР collections",
             tags=["Collections"])
    def list_collections() -> dict[str, Any]:
        collections = []
        for cid, cls in COLLECTIONS.items():
            n = len(state_repo.list_collection(cid, limit=10_000))
            collections.append({
                "id": cid,
                "title": COLLECTION_TITLES.get(cid, cid),
                "description": f"Collection of {cls.__name__} objects",
                "itemType": "feature",
                "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
                "n_objects": n,
                "links": [
                    {"href": f"/collections/{cid}", "rel": "self"},
                    {"href": f"/collections/{cid}/items", "rel": "items",
                     "type": "application/geo+json"},
                ],
            })
        return {"collections": collections,
                "links": [{"href": "/collections", "rel": "self"}]}

    @app.get("/collections/{collection_id}",
             summary="Описание одного collection",
             tags=["Collections"])
    def describe_collection(collection_id: str) -> dict[str, Any]:
        if collection_id not in COLLECTIONS:
            raise HTTPException(404, f"unknown collection: {collection_id}")
        cls = COLLECTIONS[collection_id]
        return {
            "id": collection_id,
            "title": COLLECTION_TITLES.get(collection_id, collection_id),
            "description": cls.__doc__ or "",
            "itemType": "feature",
            "schema": cls.model_json_schema(),
            "links": [
                {"href": f"/collections/{collection_id}", "rel": "self"},
                {"href": f"/collections/{collection_id}/items", "rel": "items",
                 "type": "application/geo+json"},
            ],
        }

    @app.get("/collections/{collection_id}/items",
             summary="Список объектов collection (GeoJSON FeatureCollection)",
             tags=["Collections"],
             description=(
                 "Основной read endpoint для внешних клиентов. Возвращает "
                 "GeoJSON FeatureCollection: координаты, свойства объекта, "
                 "класс ИССГР и ссылки. Поддерживает bbox, pagination и "
                 "фильтр по `issgr_class`."
             ))
    def list_items(
        collection_id: str,
        bbox: str | None = Query(
            None, description="west,south,east,north (опционально alt min,max)",
        ),
        limit: int = Query(100, ge=1, le=10000),
        offset: int = Query(0, ge=0),
        issgr_class: str | None = Query(
            None, description="Фильтр по точному IssgrClass value",
        ),
    ) -> JSONResponse:
        if collection_id not in COLLECTIONS:
            raise HTTPException(404, f"unknown collection: {collection_id}")

        bbox_obj: BBox | None = None
        if bbox:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) == 4:
                bbox_obj = BBox(west=parts[0], south=parts[1],
                                east=parts[2], north=parts[3])
            elif len(parts) == 6:
                bbox_obj = BBox(west=parts[0], south=parts[1], min_alt=parts[2],
                                east=parts[3], north=parts[4], max_alt=parts[5])
            else:
                raise HTTPException(400, "bbox must be 4 or 6 floats")

        class_filter: IssgrClass | None = None
        if issgr_class:
            try:
                class_filter = IssgrClass.from_string(issgr_class)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        objs = state_repo.list_collection(
            collection_id, bbox=bbox_obj,
            issgr_class_filter=class_filter,
            limit=limit, offset=offset,
        )
        fc = to_geojson_feature_collection(
            objs,
            links=[
                {"href": f"/collections/{collection_id}/items", "rel": "self"},
                {"href": f"/collections/{collection_id}", "rel": "collection"},
            ],
        )
        return JSONResponse(content=fc, media_type="application/geo+json")

    @app.get("/collections/{collection_id}/items/{item_id}",
             summary="Один объект collection (GeoJSON Feature)",
             tags=["Collections"])
    def get_item(collection_id: str, item_id: str) -> JSONResponse:
        if collection_id not in COLLECTIONS:
            raise HTTPException(404, f"unknown collection: {collection_id}")
        obj = state_repo.get(collection_id, item_id)
        if obj is None:
            raise HTTPException(404, f"item not found: {item_id}")
        feature = to_geojson_feature(obj)
        return JSONResponse(content=feature, media_type="application/geo+json")

    # ---- POST для имитаторов АСУ -------------------------------------------
    @app.post("/collections/uavs/items", summary="POST новый UAV",
              tags=["Write"],
              description="Создать или обновить БАС в цифровом двойнике. Используется simulator/tailer или внешний АСУ-клиент.")
    def post_uav(uav: UAV) -> dict[str, str]:
        oid = state_repo.upsert("uavs", uav)
        return {"id": oid, "status": "created"}

    @app.post("/collections/obstacles/items", summary="POST новый Obstacle",
              tags=["Write"])
    def post_obstacle(obstacle: Obstacle) -> dict[str, str]:
        oid = state_repo.upsert("obstacles", obstacle)
        return {"id": oid, "status": "created"}

    @app.post("/collections/gcs/items", summary="POST новый GCS",
              tags=["Write"])
    def post_gcs(gcs: GCS) -> dict[str, str]:
        oid = state_repo.upsert("gcs", gcs)
        return {"id": oid, "status": "created"}

    @app.post("/collections/missions/items", summary="POST новую Mission",
              tags=["Write"])
    def post_mission(mission: Mission) -> dict[str, str]:
        oid = state_repo.upsert("missions", mission)
        return {"id": oid, "status": "created"}

    @app.post("/collections/sensor_readings/items",
              summary="POST sensor observation",
              tags=["Write"],
              description="Записать наблюдение сенсора: CV detection, RSSI, LiDAR-like sample или другой payload от БАС.")
    def post_sensor(reading: SensorReading) -> dict[str, str]:
        oid = state_repo.upsert("sensor_readings", reading)
        return {"id": oid, "status": "created"}

    @app.delete("/collections/{collection_id}/items/{item_id}",
                summary="DELETE объект из collection",
                tags=["Write"])
    def delete_item(collection_id: str, item_id: str) -> dict[str, str]:
        if collection_id not in COLLECTIONS:
            raise HTTPException(404, f"unknown collection: {collection_id}")
        ok = state_repo.delete(collection_id, item_id)
        if not ok:
            raise HTTPException(404, f"item not found: {item_id}")
        return {"id": item_id, "status": "deleted"}

    # ---- BAS-specific endpoints --------------------------------------------
    @app.get("/digital_twin",
             summary="Объединённая GeoJSON FeatureCollection всех ИССГР объектов",
             tags=["Digital twin"],
             description="Единая картина обстановки: все БАС, препятствия, НПУ, missions и sensor readings в одном GeoJSON FeatureCollection.")
    def digital_twin() -> JSONResponse:
        all_objs: list[IssgrObject] = []
        for cid in state_repo.collections():
            all_objs.extend(state_repo.list_collection(cid, limit=10_000))
        fc = to_geojson_feature_collection(all_objs)
        return JSONResponse(content=fc, media_type="application/geo+json")

    @app.get("/stats", summary="Счётчики по collections",
             tags=["Digital twin"])
    def stats() -> dict[str, Any]:
        return state_repo.stats()

    @app.get("/schema", summary="JSON Schema всех ИССГР моделей",
             tags=["Schema"])
    def schema() -> dict[str, Any]:
        return {
            cls.__name__: cls.model_json_schema()
            for cls in (UAV, Obstacle, GCS, Mission, SensorReading)
        }

    @app.get("/classifier", summary="Список IssgrClass values + top-level",
             tags=["Schema"])
    def classifier() -> dict[str, list[dict[str, str]]]:
        groups: dict[str, list[dict[str, str]]] = {}
        for member in IssgrClass:
            groups.setdefault(member.top_level, []).append({
                "value": member.value, "name": member.name,
            })
        return groups

    return app
