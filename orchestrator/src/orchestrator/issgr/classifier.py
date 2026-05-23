"""Унифицированный классификатор ИССГР.

Top-level классы из ТЗ:
  * geospatial_objects (геопространственные объекты): здания, мачты,
    дорожная инфраструктура, рельеф, растительность
  * operational_situation (оперативная обстановка): БАС, опкоманды
  * functional_objects (функциональные объекты): GCS, retranslator,
    sensor stations
  * raster_3d_displays (растровые/3D отображения): радиокарты, occupancy
    grids — представлены ссылочно

Кодировка ИССГР-класса — dot-separated path: top.middle.leaf, например
"operational_situation.uav.rotary_wing". Это даёт расширяемость без
рестричных enum'ов: новый class — добавь строку, не ломая существующих
клиентов.
"""
from __future__ import annotations

from enum import Enum


class IssgrClass(str, Enum):
    """Унифицированный classifier — минимальный subset для текущего стенда.

    Используется как str-enum чтобы JSON-serializable из коробки и
    совместим с openapi/swagger schema.
    """

    # ---- geospatial_objects -----------------------------------------------
    GEO_BUILDING = "geospatial_objects.building.generic"
    GEO_HANGAR = "geospatial_objects.building.hangar"
    GEO_TOWER = "geospatial_objects.building.tower"
    GEO_MAST = "geospatial_objects.infrastructure.mast"
    GEO_RUNWAY = "geospatial_objects.infrastructure.runway"
    GEO_VEGETATION = "geospatial_objects.terrain.vegetation"
    GEO_TERRAIN = "geospatial_objects.terrain.dem"

    # ---- operational_situation --------------------------------------------
    OPS_UAV_ROTARY = "operational_situation.uav.rotary_wing"
    OPS_UAV_FIXED = "operational_situation.uav.fixed_wing"
    OPS_UAV_VTOL = "operational_situation.uav.vtol"
    OPS_MISSION = "operational_situation.mission.waypoint_route"
    OPS_TARGET = "operational_situation.target.point"

    # ---- functional_objects -----------------------------------------------
    FUNC_GCS = "functional_objects.gcs.command_post"
    FUNC_RETRANSLATOR = "functional_objects.retranslator.lora"
    FUNC_SENSOR_STATION = "functional_objects.sensor.ground_station"

    # ---- raster_3d_displays -----------------------------------------------
    DISPLAY_RADIO_MAP = "raster_3d_displays.radio_map.coverage"
    DISPLAY_OCCUPANCY = "raster_3d_displays.occupancy.grid"

    @classmethod
    def from_string(cls, value: str) -> "IssgrClass":
        """Lookup with fallback на closest top-level если leaf неизвестен."""
        try:
            return cls(value)
        except ValueError:
            # Fallback: ищем top-level совпадение.
            top = value.split(".", 1)[0]
            for member in cls:
                if member.value.startswith(top + "."):
                    return member
            raise ValueError(f"unknown ISSGR class: {value}")

    @property
    def top_level(self) -> str:
        """Возвращает 'geospatial_objects' / 'operational_situation' / ..."""
        return self.value.split(".", 1)[0]


# Mapping для совместимости с RF_OBSTACLES из gcs_web_ui_server.py.
RF_OBSTACLE_MATERIAL_TO_CLASS = {
    "metal": IssgrClass.GEO_HANGAR,
    "concrete": IssgrClass.GEO_TOWER,
    "brick": IssgrClass.GEO_BUILDING,
    "wood": IssgrClass.GEO_BUILDING,
}
